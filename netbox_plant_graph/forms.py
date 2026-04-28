from django import forms
from django.db import models
from dcim.models import Device, DeviceRole, Location, Rack, Site
from netbox.forms import NetBoxModelFilterSetForm, NetBoxModelForm
from tenancy.models import Tenant
from utilities.forms.fields import DynamicModelChoiceField

from .choices import (
    DisjointnessChoices,
    DisjointnessExceptionScopeChoices,
    DisjointnessExceptionTypeChoices,
    GraphResolutionChoices,
)
from .models import DeploymentPlan, Fabric, FabricPlane
from .object_registry import FILTER_FORM_OBJECT_SPECS, FORM_OBJECT_SPECS
from .services.graph.resolution import DEFAULT_RESOLUTION
from .services.netbox.lookup import get_registry_label, get_registry_model


GENERATED_FORM_QUERY_PARAMS = {
    ('disjointnessexception', 'plane_a'): {'fabric': '$fabric'},
    ('disjointnessexception', 'plane_b'): {'fabric': '$fabric'},
    ('assemblymappingtemplate', 'a_connector'): {'template': '$template', 'side': 'A'},
    ('assemblymappingtemplate', 'b_connector'): {'template': '$template', 'side': 'B'},
    ('spatialtemplatenode', 'parent'): {'template': '$template'},
}

PATH_RESOLVER_REGISTRY_KEYS = (
    'interface',
    'frontport',
    'rearport',
    'attachmentunit',
    'signallane',
    'terminationpoint',
    'plantnode',
    'coarseedge',
)


def _build_dynamic_choice_field(*, queryset, label, required, query_params=None, selector=False, help_text=None):
    kwargs = {
        'queryset': queryset,
        'label': label,
        'required': required,
    }
    if query_params:
        kwargs['query_params'] = query_params
    if selector:
        kwargs['selector'] = True
    if help_text:
        kwargs['help_text'] = help_text
    return DynamicModelChoiceField(**kwargs)


def _build_registry_object_field(*, registry_key, role):
    label = get_registry_label(registry_key)
    model = get_registry_model(registry_key)
    return _build_dynamic_choice_field(
        queryset=model.objects.none(),
        label=f'{role} {label}',
        required=False,
        selector=True,
        help_text=f'Select a {label.lower()} as the {role.lower()} endpoint.',
    )


def _get_generated_field_query_params(spec, model_field, form_field_names):
    mapping = GENERATED_FORM_QUERY_PARAMS.get((spec.registry_key, model_field.name))
    if mapping is not None:
        return mapping

    related_label = model_field.related_model._meta.label_lower
    if related_label == 'dcim.location' and 'site' in form_field_names:
        return {'site_id': '$site'}
    if related_label == 'dcim.rack':
        params = {}
        if 'site' in form_field_names:
            params['site_id'] = '$site'
        if 'location' in form_field_names:
            params['location_id'] = '$location'
        if params:
            return params
    if related_label == 'netbox_plant_graph.fabricplane' and 'fabric' in form_field_names:
        return {'fabric': '$fabric'}

    return None


def _build_generated_form_field(spec, field_name):
    model_field = spec.model._meta.get_field(field_name)
    if not isinstance(model_field, models.ForeignKey):
        return None
    if model_field.related_model._meta.label_lower == 'contenttypes.contenttype':
        return None

    query_params = _get_generated_field_query_params(spec, model_field, spec.form.fields)
    return _build_dynamic_choice_field(
        queryset=model_field.related_model._default_manager.all(),
        label=str(model_field.verbose_name).title(),
        required=not model_field.blank,
        query_params=query_params,
    )


def _build_generated_filter_form_field(spec, field_name):
    try:
        model_field = spec.model._meta.get_field(field_name)
    except Exception:
        return forms.CharField(required=False)

    if isinstance(model_field, models.ForeignKey):
        if model_field.related_model._meta.label_lower == 'contenttypes.contenttype':
            return forms.ModelChoiceField(
                queryset=model_field.related_model._default_manager.all(),
                required=False,
                label=str(model_field.verbose_name).title(),
            )
        return _build_dynamic_choice_field(
            queryset=model_field.related_model._default_manager.all(),
            label=str(model_field.verbose_name).title(),
            required=False,
        )

    return forms.CharField(required=False)


# --- Planning: stamp wizard forms ---

STAMP_ACTION_CHOICES = [
    ('execute_now', 'Execute now'),
    ('add_to_plan', 'Add to plan (deferred)'),
]

STAMP_VARIABLE_FIELD_PREFIX = 'var__'


def _variable_field_name(variable_name: str) -> str:
    return f'{STAMP_VARIABLE_FIELD_PREFIX}{variable_name}'


def _build_template_variable_fields(form: forms.Form, template) -> None:
    """Add dynamic variable input fields declared by template.parameters."""
    schema = getattr(template, 'parameters', None) or {}
    for variable_name, variable_spec in schema.items():
        field_name = _variable_field_name(variable_name)
        if field_name in form.fields:
            continue

        variable_type = (variable_spec or {}).get('type', 'str')
        label = (variable_spec or {}).get('label') or variable_name.replace('_', ' ').title()
        default = (variable_spec or {}).get('default')

        if variable_type == 'int':
            form.fields[field_name] = forms.IntegerField(required=False, label=label, initial=default)
        elif variable_type == 'float':
            form.fields[field_name] = forms.DecimalField(required=False, label=label, initial=default)
        elif variable_type == 'bool':
            form.fields[field_name] = forms.BooleanField(required=False, label=label, initial=bool(default))
        else:
            form.fields[field_name] = forms.CharField(required=False, label=label, initial=default)


def collect_template_variables(cleaned_data: dict) -> dict:
    """Extract dynamic template variable values from form cleaned_data."""
    variables = {}
    for key, value in cleaned_data.items():
        if not key.startswith(STAMP_VARIABLE_FIELD_PREFIX):
            continue
        variable_name = key[len(STAMP_VARIABLE_FIELD_PREFIX):]
        if variable_name == '':
            continue
        if value in (None, ''):
            continue
        variables[variable_name] = value
    return variables


class AssemblyStampForm(forms.Form):
    """Form for stamping a passive device from an AssemblyTemplate."""
    name = forms.CharField(
        max_length=200,
        label='Device Name',
        help_text='Name for the stamped device.',
    )
    site = _build_dynamic_choice_field(
        queryset=Site.objects.none(),
        label='Site',
        required=True,
        selector=True,
        help_text='Select the site where the device will be placed.',
    )
    location = _build_dynamic_choice_field(
        queryset=Location.objects.none(),
        label='Location',
        required=False,
        query_params={'site_id': '$site'},
        help_text='Optional location (room/row) within the selected site.',
    )
    rack = _build_dynamic_choice_field(
        queryset=Rack.objects.none(),
        label='Rack',
        required=False,
        query_params={'site_id': '$site', 'location_id': '$location'},
        help_text='Optional rack to place the device in.',
    )
    position = forms.DecimalField(
        required=False,
        max_digits=4,
        decimal_places=1,
        label='Rack Position (U)',
        help_text='U position within the rack (optional).',
    )
    face = forms.ChoiceField(
        choices=[('front', 'Front'), ('rear', 'Rear')],
        initial='front',
        label='Rack Face',
        required=False,
    )
    device_role = _build_dynamic_choice_field(
        queryset=DeviceRole.objects.none(),
        label='Device Role',
        required=True,
        help_text='Select the DeviceRole for the stamped device.',
    )
    plan = _build_dynamic_choice_field(
        queryset=DeploymentPlan.objects.none(),
        label='Deployment Plan',
        required=False,
        help_text='Optional deployment plan to associate with this stamp.',
    )
    action = forms.ChoiceField(
        choices=STAMP_ACTION_CHOICES,
        initial='execute_now',
        required=False,
        label='Action',
        help_text='Execute immediately or queue as a pending record in the selected plan.',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['site'].queryset = Site.objects.order_by('name', 'pk')
        self.fields['location'].queryset = Location.objects.order_by('name', 'pk')
        self.fields['rack'].queryset = Rack.objects.order_by('name', 'pk')
        self.fields['device_role'].queryset = DeviceRole.objects.order_by('name', 'pk')
        self.fields['plan'].queryset = DeploymentPlan.objects.order_by('name', 'pk')

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('action') == 'add_to_plan' and not cleaned.get('plan'):
            self.add_error('plan', 'A deployment plan must be selected when using "Add to plan".')
        return cleaned


class SpatialStampForm(forms.Form):
    """Form for stamping a SpatialTemplate."""
    site = _build_dynamic_choice_field(
        queryset=Site.objects.none(),
        label='Site',
        required=True,
        selector=True,
        help_text='Select the site to stamp Locations/Racks under.',
    )
    parent_location = _build_dynamic_choice_field(
        queryset=Location.objects.none(),
        label='Parent Location',
        required=False,
        query_params={'site_id': '$site'},
        help_text='Optional existing location to nest new objects under.',
    )
    plan = _build_dynamic_choice_field(
        queryset=DeploymentPlan.objects.none(),
        label='Deployment Plan',
        required=False,
        help_text='Optional deployment plan to associate with this stamp.',
    )
    action = forms.ChoiceField(
        choices=STAMP_ACTION_CHOICES,
        initial='execute_now',
        required=False,
        label='Action',
        help_text='Execute immediately or queue as a pending record in the selected plan.',
    )

    def __init__(self, *args, **kwargs):
        template = kwargs.pop('template', None)
        super().__init__(*args, **kwargs)

        self.fields['site'].queryset = Site.objects.order_by('name', 'pk')
        self.fields['parent_location'].queryset = Location.objects.order_by('name', 'pk')
        self.fields['plan'].queryset = DeploymentPlan.objects.order_by('name', 'pk')
        if template is not None:
            _build_template_variable_fields(self, template)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('action') == 'add_to_plan' and not cleaned.get('plan'):
            self.add_error('plan', 'A deployment plan must be selected when using "Add to plan".')
        return cleaned


class RackPopulationStampForm(forms.Form):
    """Form for stamping a RackPopulationTemplate into a specific rack."""
    rack = _build_dynamic_choice_field(
        queryset=Rack.objects.none(),
        label='Rack',
        required=True,
        selector=True,
        help_text='Select the rack to populate.',
    )
    plan = _build_dynamic_choice_field(
        queryset=DeploymentPlan.objects.none(),
        label='Deployment Plan',
        required=False,
        help_text='Optional deployment plan to associate with this stamp.',
    )
    action = forms.ChoiceField(
        choices=STAMP_ACTION_CHOICES,
        initial='execute_now',
        required=False,
        label='Action',
        help_text='Execute immediately or queue as a pending record in the selected plan.',
    )

    def __init__(self, *args, **kwargs):
        template = kwargs.pop('template', None)
        super().__init__(*args, **kwargs)

        self.fields['rack'].queryset = Rack.objects.order_by('name', 'pk')
        self.fields['plan'].queryset = DeploymentPlan.objects.order_by('name', 'pk')
        if template is not None:
            _build_template_variable_fields(self, template)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('action') == 'add_to_plan' and not cleaned.get('plan'):
            self.add_error('plan', 'A deployment plan must be selected when using "Add to plan".')
        return cleaned


class BreakoutStampForm(forms.Form):
    """Form for applying a DeviceBreakoutTemplate to an existing device."""
    device = _build_dynamic_choice_field(
        queryset=Device.objects.none(),
        label='Device',
        required=True,
        selector=True,
        help_text='Select the device to expand child interfaces on.',
    )
    plan = _build_dynamic_choice_field(
        queryset=DeploymentPlan.objects.none(),
        label='Deployment Plan',
        required=False,
        help_text='Optional deployment plan to associate with this stamp.',
    )
    action = forms.ChoiceField(
        choices=STAMP_ACTION_CHOICES,
        initial='execute_now',
        required=False,
        label='Action',
        help_text='Execute immediately or queue as a pending record in the selected plan.',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['device'].queryset = Device.objects.order_by('name', 'pk')
        self.fields['plan'].queryset = DeploymentPlan.objects.order_by('name', 'pk')

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('action') == 'add_to_plan' and not cleaned.get('plan'):
            self.add_error('plan', 'A deployment plan must be selected when using "Add to plan".')
        return cleaned


def build_form_class(spec):
    meta_class = type('Meta', (), {'model': spec.model, 'fields': spec.form.fields})
    attrs = {'__module__': __name__, 'Meta': meta_class}
    for field_name in spec.form.fields:
        generated_field = _build_generated_form_field(spec, field_name)
        if generated_field is not None:
            attrs[field_name] = generated_field
    return type(spec.form.class_name, (NetBoxModelForm,), attrs)


def build_filter_form_class(spec):
    attrs = {
        '__module__': __name__,
        'model': spec.model,
        'q': forms.CharField(required=False, label='Search'),
    }
    for field_name in spec.filter_form.fields:
        attrs[field_name] = _build_generated_filter_form_field(spec, field_name)
    if spec.registry_key == 'auditfinding':
        attrs['suppressed'] = forms.ChoiceField(
            required=False,
            choices=(
                ('', '---------'),
                ('true', 'Yes'),
                ('false', 'No'),
            ),
            label='Suppressed',
        )
        attrs['min_age_days'] = forms.IntegerField(required=False, min_value=0, label='Minimum age (days)')
    if spec.registry_key == 'auditfindingevent':
        from .models import Fabric

        attrs['fabric'] = _build_dynamic_choice_field(
            queryset=Fabric.objects.order_by('name', 'pk'),
            label='Fabric',
            required=False,
        )
        attrs['created_after'] = forms.DateTimeField(required=False, label='Created after')
        attrs['created_before'] = forms.DateTimeField(required=False, label='Created before')
    return type(spec.filter_form.class_name, (NetBoxModelFilterSetForm,), attrs)


for object_spec in FORM_OBJECT_SPECS:
    globals()[object_spec.form.class_name] = build_form_class(object_spec)

for object_spec in FILTER_FORM_OBJECT_SPECS:
    globals()[object_spec.filter_form.class_name] = build_filter_form_class(object_spec)


# --- Workflow forms ---

def path_resolver_selector_field_name(role: str, registry_key: str) -> str:
    return f'{role}_{registry_key}'


def path_resolver_selector_specs():
    return tuple(
        {
            'registry_key': registry_key,
            'label': get_registry_label(registry_key),
            'source_field_name': path_resolver_selector_field_name('source', registry_key),
            'destination_field_name': path_resolver_selector_field_name('destination', registry_key),
        }
        for registry_key in PATH_RESOLVER_REGISTRY_KEYS
    )


class PathResolverForm(forms.Form):
    source_registry_key = forms.ChoiceField(
        label='Source Type',
        choices=tuple((key, get_registry_label(key)) for key in PATH_RESOLVER_REGISTRY_KEYS),
        initial='attachmentunit',
    )
    source_id = forms.CharField(required=False, widget=forms.HiddenInput())
    destination_registry_key = forms.ChoiceField(
        label='Destination Type',
        choices=tuple((key, get_registry_label(key)) for key in PATH_RESOLVER_REGISTRY_KEYS),
        initial='attachmentunit',
    )
    destination_id = forms.CharField(required=False, widget=forms.HiddenInput())
    plane_id = _build_dynamic_choice_field(
        queryset=FabricPlane.objects.none(),
        label='Plane Filter',
        required=False,
        help_text='Optional fabric plane to constrain the resolved path.',
    )
    resolution = forms.ChoiceField(
        label='Resolution',
        choices=tuple(
            (value, label)
            for value, label in GraphResolutionChoices.CHOICES
            if value in {'attachment_unit', 'signal_lane'}
        ),
        initial=DEFAULT_RESOLUTION,
    )
    max_depth = forms.IntegerField(
        label='Max Depth',
        required=False,
        min_value=1,
        initial=128,
        help_text='Maximum traversal depth before the resolver stops searching.',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        for registry_key in PATH_RESOLVER_REGISTRY_KEYS:
            for role in ('source', 'destination'):
                field_name = path_resolver_selector_field_name(role, registry_key)
                self.fields[field_name] = _build_registry_object_field(registry_key=registry_key, role=role.title())
                self.fields[field_name].queryset = get_registry_model(registry_key)._default_manager.all()

        self.fields['plane_id'].queryset = FabricPlane.objects.select_related('fabric').order_by(
            'fabric__name', 'plane_number', 'pk'
        )


class FabricOnboardForm(forms.Form):
    """Single-page wizard form for onboarding a new Fabric."""
    name = forms.CharField(max_length=200, label='Fabric Name')
    description = forms.CharField(max_length=200, required=False, label='Description (optional)')
    tenant = _build_dynamic_choice_field(
        queryset=Tenant.objects.none(),
        label='Tenant',
        required=False,
        help_text='Optional tenancy.Tenant to associate with this fabric.',
    )
    site = _build_dynamic_choice_field(
        queryset=Site.objects.none(),
        label='Site',
        required=False,
        selector=True,
        help_text='Optional dcim.Site to associate with this fabric.',
    )
    location = _build_dynamic_choice_field(
        queryset=Location.objects.none(),
        label='Location',
        required=False,
        query_params={'site_id': '$site'},
        help_text='Optional dcim.Location to associate with this fabric.',
    )
    expected_plane_count = forms.IntegerField(
        min_value=1,
        max_value=32,
        initial=4,
        label='Expected Plane Count',
    )
    tier_depth = forms.IntegerField(
        min_value=1,
        max_value=10,
        initial=3,
        label='Tier Depth',
    )
    disjointness_policy = forms.ChoiceField(
        choices=DisjointnessChoices.CHOICES,
        label='Disjointness Policy',
    )
    trigger_initial_rebuild = forms.BooleanField(
        required=False,
        initial=False,
        label='Trigger Initial Graph Rebuild',
        help_text='If checked, kicks off a graph rebuild immediately after creation.',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tenant'].queryset = Tenant.objects.order_by('name', 'pk')
        self.fields['site'].queryset = Site.objects.order_by('name', 'pk')
        self.fields['location'].queryset = Location.objects.order_by('name', 'pk')


class AuditTriageFilterForm(forms.Form):
    """Filter form for the Audit Finding Triage Queue."""
    fabric_id = forms.IntegerField(required=False, label='Fabric')
    severity = forms.CharField(required=False, label='Severity')
    finding_type = forms.CharField(required=False, label='Finding Type')


class DisjointnessExceptionRequestForm(forms.Form):
    """Guided form for requesting a disjointness policy exception."""

    fabric = _build_dynamic_choice_field(
        queryset=Fabric.objects.none(),
        label='Fabric',
        required=True,
        help_text='The fabric this exception applies to.',
    )
    exception_type = forms.ChoiceField(
        choices=DisjointnessExceptionTypeChoices.CHOICES,
        label='Exception Type',
        help_text='The type of disjointness violation this exception covers.',
    )
    plane_a = _build_dynamic_choice_field(
        queryset=FabricPlane.objects.none(),
        label='Plane A',
        required=True,
        query_params={'fabric': '$fabric'},
        help_text='First fabric plane of the crossing pair.',
    )
    plane_b = _build_dynamic_choice_field(
        queryset=FabricPlane.objects.none(),
        label='Plane B',
        required=True,
        query_params={'fabric': '$fabric'},
        help_text='Second fabric plane of the crossing pair.',
    )
    scope_kind = forms.ChoiceField(
        choices=DisjointnessExceptionScopeChoices.CHOICES,
        label='Scope Kind',
        help_text='The granularity at which the exception is scoped.',
    )
    reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
        label='Reason / Justification',
        help_text='Explain why this exception is necessary.',
    )
    expires_at = forms.DateTimeField(
        required=False,
        label='Expires At (optional)',
        help_text='If set, the exception will automatically expire at this date/time.',
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['fabric'].queryset = Fabric.objects.order_by('name')
        plane_qs = FabricPlane.objects.select_related('fabric').order_by('fabric__name', 'plane_number')
        self.fields['plane_a'].queryset = plane_qs
        self.fields['plane_b'].queryset = plane_qs

    def clean(self):
        cleaned_data = super().clean()
        fabric = cleaned_data.get('fabric')
        plane_a = cleaned_data.get('plane_a')
        plane_b = cleaned_data.get('plane_b')
        if plane_a and fabric and plane_a.fabric_id != fabric.pk:
            self.add_error('plane_a', 'Plane A must belong to the selected fabric.')
        if plane_b and fabric and plane_b.fabric_id != fabric.pk:
            self.add_error('plane_b', 'Plane B must belong to the selected fabric.')
        if plane_a and plane_b and plane_a == plane_b:
            self.add_error('plane_b', 'Plane A and Plane B must be distinct.')
        return cleaned_data
