from django import forms
from django.utils.text import slugify
from dcim.models import Device, Interface
from netbox.forms import NetBoxModelFilterSetForm, NetBoxModelForm

from .v2_registry import V2_OBJECT_SPECS


def _build_model_form(spec):
    meta = type(
        'Meta',
        (),
        {
            'model': spec.model,
            'fields': spec.resolved_form_fields,
        },
    )
    return type(spec.form_name, (NetBoxModelForm,), {'__module__': __name__, 'Meta': meta})


def _build_filter_form(spec):
    attrs = {
        '__module__': __name__,
        'model': spec.model,
    }
    return type(spec.filter_form_name, (NetBoxModelFilterSetForm,), attrs)


for _spec in V2_OBJECT_SPECS:
    globals()[_spec.form_name] = _build_model_form(_spec)
    globals()[_spec.filter_form_name] = _build_filter_form(_spec)


class StampTemplateExecuteForm(forms.Form):
    fabric_name = forms.CharField(
        max_length=200,
        label='Fabric Name',
    )
    fabric_slug = forms.SlugField(
        max_length=200,
        label='Fabric Slug',
    )
    gpu_tray_device = forms.ModelChoiceField(
        queryset=Device.objects.order_by('name', 'pk'),
        required=False,
        label='GPU Tray Device',
        help_text='Optional NetBox Device to anchor the stamped GB300 tray node.',
    )
    gpu_osfp_1_interface = forms.ModelChoiceField(
        queryset=Interface.objects.select_related('device').order_by('device__name', 'name', 'pk'),
        required=False,
        label='GPU OSFP-1 Interface',
        help_text='Optional NetBox Interface to anchor the stamped GB300 OSFP-1 endpoint.',
    )

    @classmethod
    def initial_from_template(cls, template):
        fabric_name = template.name
        if fabric_name.lower().endswith(' proof'):
            fabric_name = fabric_name
        return {
            'fabric_name': fabric_name,
            'fabric_slug': slugify(fabric_name),
        }

    def source_bindings(self):
        if not self.is_valid():
            return {}
        nodes = {}
        endpoints = {}
        if self.cleaned_data.get('gpu_tray_device'):
            nodes['GB300-TRAY-1'] = self.cleaned_data['gpu_tray_device']
        if self.cleaned_data.get('gpu_osfp_1_interface'):
            endpoints['GB300-TRAY-1.OSFP-1'] = self.cleaned_data['gpu_osfp_1_interface']
        return {
            'nodes': nodes,
            'endpoints': endpoints,
        }


__all__ = ('StampTemplateExecuteForm',) + tuple(
    name
    for spec in V2_OBJECT_SPECS
    for name in (spec.form_name, spec.filter_form_name)
)
