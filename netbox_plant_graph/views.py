from django.contrib import messages
from django.core.exceptions import FieldDoesNotExist
from django.shortcuts import redirect
from django.views import View
from django.views.generic import TemplateView
from netbox.views import generic

from . import filtersets, forms, tables
from .models import Fabric, FabricArchitecture, OpticalLane, Plane, StampTemplate, TransferMap
from .services.resolver import resolve_optical_lane_path
from .services.stamp_preview import build_v2_stamp_template_preview
from .services.stamping import execute_stamp_template, stamp_roce_4plane_mini_fabric
from .v2_registry import V2_OBJECT_SPECS, get_v2_object_spec_for_model


class V2RegisteredObjectView(generic.ObjectView):
    template_name = 'netbox_plant_graph/v2_object.html'

    def get_extra_context(self, request, instance):
        spec = get_v2_object_spec_for_model(instance.__class__)
        detail_fields = []
        for field_name in spec.resolved_detail_fields:
            value = getattr(instance, field_name)
            try:
                label = instance._meta.get_field(field_name).verbose_name.title()
            except FieldDoesNotExist:
                label = field_name.replace('_', ' ').title()
            detail_fields.append({
                'name': field_name,
                'label': label,
                'value': value,
                'is_empty': value in (None, ''),
            })
        return {
            'object_spec': spec,
            'detail_fields': detail_fields,
            'stamp_template_execute_url': (
                'plugins:netbox_plant_graph:stamptemplate_execute'
                if spec.registry_key == 'stamptemplate'
                else None
            ),
        }


class HomeView(TemplateView):
    template_name = 'netbox_plant_graph/home.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'architecture_count': FabricArchitecture.objects.count(),
            'fabric_count': Fabric.objects.count(),
            'plane_count': Plane.objects.count(),
            'optical_lane_count': OpticalLane.objects.count(),
            'transfer_map_count': TransferMap.objects.count(),
            'v2_object_links': tuple(
                {
                    'label': spec.label_plural,
                    'url_name': f'plugins:netbox_plant_graph:{spec.model._meta.model_name}_list',
                }
                for spec in V2_OBJECT_SPECS
            ),
        })
        return context


class SeedV2ProofView(View):
    def post(self, request):
        result = stamp_roce_4plane_mini_fabric()
        failures = [path for path in result.resolved_paths if not path.path_found]
        if failures:
            messages.error(request, f'Stamped {result.fabric.name}, but {len(failures)} proof paths failed.')
        else:
            messages.success(request, f'Stamped {result.fabric.name} with {len(result.resolved_paths)} resolved paths.')
        return redirect('plugins:netbox_plant_graph:home')


class StampTemplateExecuteView(TemplateView):
    template_name = 'netbox_plant_graph/stamp_template_execute.html'

    def dispatch(self, request, *args, **kwargs):
        self.template = StampTemplate.objects.get(pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def _form(self, data=None):
        return forms.StampTemplateExecuteForm(
            data=data,
            initial=forms.StampTemplateExecuteForm.initial_from_template(self.template),
            template=self.template,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = kwargs.get('form') or self._form()
        preview_parameters = {
            'fabric_name': form.initial.get('fabric_name'),
            'fabric_slug': form.initial.get('fabric_slug'),
        }
        if form.is_bound and form.is_valid():
            preview_parameters = {
                'fabric_name': form.cleaned_data['fabric_name'],
                'fabric_slug': form.cleaned_data['fabric_slug'],
            }
        context.update({
            'template': self.template,
            'form': form,
            'preview': build_v2_stamp_template_preview(self.template, preview_parameters),
        })
        return context

    def post(self, request, *args, **kwargs):
        form = self._form(data=request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        result = execute_stamp_template(
            template=self.template,
            fabric_name=form.cleaned_data['fabric_name'],
            fabric_slug=form.cleaned_data['fabric_slug'],
            source_bindings=form.source_bindings(),
            creation_options=form.creation_options(),
        )
        failures = [path for path in result.resolved_paths if not path.path_found]
        if failures:
            messages.error(request, f'Stamped {result.fabric.name}, but {len(failures)} proof paths failed.')
        else:
            messages.success(
                request,
                f'Stamped {result.fabric.name} with {len(result.resolved_paths)} resolved paths.',
            )
        return redirect(result.fabric.get_absolute_url())


def _build_list_view(spec):
    return type(
        spec.list_view_name,
        (generic.ObjectListView,),
        {
            '__module__': __name__,
            'queryset': spec.model.objects.all(),
            'table': getattr(tables, spec.table_name),
            'filterset': getattr(filtersets, spec.filterset_name),
            'filterset_form': getattr(forms, spec.filter_form_name),
        },
    )


def _build_detail_view(spec):
    return type(
        spec.detail_view_name,
        (V2RegisteredObjectView,),
        {
            '__module__': __name__,
            'queryset': spec.model.objects.all(),
        },
    )


def _build_edit_view(spec):
    return type(
        spec.edit_view_name,
        (generic.ObjectEditView,),
        {
            '__module__': __name__,
            'queryset': spec.model.objects.all(),
            'form': getattr(forms, spec.form_name),
        },
    )


def _build_queryset_view(spec, view_name, base_class):
    return type(
        view_name,
        (base_class,),
        {
            '__module__': __name__,
            'queryset': spec.model.objects.all(),
        },
    )


for _spec in V2_OBJECT_SPECS:
    globals()[_spec.list_view_name] = _build_list_view(_spec)
    globals()[_spec.detail_view_name] = _build_detail_view(_spec)
    globals()[_spec.edit_view_name] = _build_edit_view(_spec)
    globals()[_spec.delete_view_name] = _build_queryset_view(_spec, _spec.delete_view_name, generic.ObjectDeleteView)
    globals()[_spec.changelog_view_name] = _build_queryset_view(
        _spec, _spec.changelog_view_name, generic.ObjectChangeLogView
    )
    globals()[_spec.journal_view_name] = _build_queryset_view(_spec, _spec.journal_view_name, generic.ObjectJournalView)


class PathQueryView(TemplateView):
    template_name = 'netbox_plant_graph/path_query.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        source_lanes = OpticalLane.objects.select_related(
            'fabric',
            'endpoint',
            'local_mpo_endpoint',
            'local_mpo_position',
            'plane',
        ).filter(direction='send').order_by('fabric__name', 'endpoint__address', 'lane_index')
        destination_lanes = OpticalLane.objects.select_related(
            'fabric',
            'endpoint',
            'local_mpo_endpoint',
            'local_mpo_position',
            'plane',
        ).filter(direction='receive').order_by('fabric__name', 'endpoint__address', 'lane_index')

        selected_source = None
        selected_destination = None
        resolved_path = None
        source_id = self.request.GET.get('source_lane')
        destination_id = self.request.GET.get('destination_lane')

        if source_id:
            selected_source = source_lanes.filter(pk=source_id).first()
            if selected_source is None:
                messages.error(self.request, 'Selected source lane was not found.')
            else:
                destination_queryset = destination_lanes.filter(fabric=selected_source.fabric)
                if destination_id:
                    selected_destination = destination_queryset.filter(pk=destination_id).first()
                    if selected_destination is None:
                        messages.error(self.request, 'Selected destination lane was not found in the source fabric.')
                resolved_path = resolve_optical_lane_path(
                    source=selected_source,
                    destination=selected_destination,
                )
                destination_lanes = destination_queryset

        context.update({
            'source_lanes': source_lanes,
            'destination_lanes': destination_lanes,
            'selected_source': selected_source,
            'selected_destination': selected_destination,
            'resolved_path': resolved_path,
        })
        return context
