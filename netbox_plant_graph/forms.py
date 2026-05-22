from django import forms
from django.utils import timezone
from django.utils.text import slugify
from dcim.models import Device, DeviceRole, DeviceType, Interface, Location, Site
from netbox.forms import NetBoxModelFilterSetForm, NetBoxModelForm
from tenancy.models import Tenant

from .choices import ArchitectureSourceArtifactTypeChoices, OnboardingSourceArtifactTypeChoices
from .models import FabricArchitecture
from .models import Fabric
from .models import Endpoint
from .models import OpticalLane
from .models import Plane
from .models import StampTemplate
from .v2_registry import V2_OBJECT_SPECS


SOURCE_BINDING_MODELS = {
    'dcim.device': Device,
    'dcim.interface': Interface,
}
SOURCE_BINDING_PLURALS = {
    'node': 'nodes',
    'endpoint': 'endpoints',
}

BUILDER_CONNECTOR_TYPE_CHOICES = (
    ('mpo-8', 'MPO-8'),
    ('mpo-12', 'MPO-12'),
    ('mpo-16', 'MPO-16'),
    ('mpo-24', 'MPO-24'),
    ('mtp-16', 'MTP-16'),
    ('mtp-24', 'MTP-24'),
    ('lc-duplex', 'LC Duplex'),
    ('lc-simplex', 'LC Simplex'),
    ('sc-duplex', 'SC Duplex'),
    ('custom', 'Custom'),
)


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
    create_active_devices = forms.BooleanField(
        required=False,
        label='Create Active NetBox Devices',
    )
    create_site = forms.ModelChoiceField(
        queryset=Site.objects.order_by('name', 'pk'),
        required=False,
        label='Creation Site',
    )
    create_gpu_device_type = forms.ModelChoiceField(
        queryset=DeviceType.objects.select_related('manufacturer').order_by('manufacturer__name', 'model', 'pk'),
        required=False,
        label='GPU Device Type',
    )
    create_gpu_role = forms.ModelChoiceField(
        queryset=DeviceRole.objects.order_by('name', 'pk'),
        required=False,
        label='GPU Device Role',
    )
    create_leaf_device_type = forms.ModelChoiceField(
        queryset=DeviceType.objects.select_related('manufacturer').order_by('manufacturer__name', 'model', 'pk'),
        required=False,
        label='Leaf Device Type',
        help_text='Optional override. Defaults to GPU Device Type.',
    )
    create_leaf_role = forms.ModelChoiceField(
        queryset=DeviceRole.objects.order_by('name', 'pk'),
        required=False,
        label='Leaf Device Role',
        help_text='Optional override. Defaults to GPU Device Role.',
    )
    create_name_prefix = forms.CharField(
        max_length=200,
        required=False,
        label='Device Name Prefix',
    )
    def __init__(self, *args, template=None, **kwargs):
        self.template = template
        super().__init__(*args, **kwargs)
        self.source_binding_definitions = tuple((getattr(template, 'template', {}) or {}).get('source_bindings') or ())
        self.source_binding_field_names = []
        for definition in self.source_binding_definitions:
            field_name = definition['field_name']
            self.fields[field_name] = forms.ModelChoiceField(
                queryset=self._source_binding_queryset(definition),
                required=definition.get('required', False),
                label=definition.get('label') or definition['address'],
                help_text=definition.get('help_text', ''),
            )
            self.source_binding_field_names.append(field_name)
        self.source_binding_definition_by_address = {
            definition['address']: definition
            for definition in self.source_binding_definitions
        }

    @classmethod
    def initial_from_template(cls, template):
        fabric_name = template.name
        if fabric_name.lower().endswith(' proof'):
            fabric_name = fabric_name
        return {
            'fabric_name': fabric_name,
            'fabric_slug': slugify(fabric_name),
            'create_name_prefix': slugify(fabric_name),
        }

    def _source_binding_queryset(self, definition):
        model = SOURCE_BINDING_MODELS.get(definition.get('model'))
        if model is Device:
            return model.objects.order_by('name', 'pk')
        if model is Interface:
            return model.objects.select_related('device').order_by('device__name', 'name', 'pk')
        raise ValueError(f'Unsupported source binding model: {definition.get("model")!r}.')

    @property
    def source_binding_bound_fields(self):
        return [self[field_name] for field_name in self.source_binding_field_names]

    @property
    def creation_bound_fields(self):
        field_names = (
            'create_active_devices',
            'create_site',
            'create_gpu_device_type',
            'create_gpu_role',
            'create_leaf_device_type',
            'create_leaf_role',
            'create_name_prefix',
        )
        return [self[field_name] for field_name in field_names]

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('create_active_devices'):
            required_fields = (
                'create_site',
                'create_gpu_device_type',
                'create_gpu_role',
            )
            for field_name in required_fields:
                if cleaned_data.get(field_name) is None:
                    self.add_error(field_name, 'This field is required when active device creation is enabled.')
        for definition in self.source_binding_definitions:
            device_binding_address = definition.get('device_binding_address')
            if not device_binding_address:
                continue
            endpoint_source = cleaned_data.get(definition['field_name'])
            if endpoint_source is None:
                continue
            device_definition = self.source_binding_definition_by_address.get(device_binding_address)
            if device_definition is None:
                continue
            device_source = cleaned_data.get(device_definition['field_name'])
            if device_source is None:
                continue
            if getattr(endpoint_source, 'device_id', None) != device_source.pk:
                self.add_error(
                    definition['field_name'],
                    f'Select an interface that belongs to {device_source}.',
                )
        return cleaned_data

    def creation_options(self):
        if not self.is_valid():
            return {}
        if not self.cleaned_data.get('create_active_devices'):
            return {}
        return {
            'enabled': True,
            'site': self.cleaned_data['create_site'],
            'gpu_device_type': self.cleaned_data['create_gpu_device_type'],
            'gpu_role': self.cleaned_data['create_gpu_role'],
            'leaf_device_type': self.cleaned_data.get('create_leaf_device_type'),
            'leaf_role': self.cleaned_data.get('create_leaf_role'),
            'name_prefix': self.cleaned_data.get('create_name_prefix') or self.cleaned_data['fabric_slug'],
        }

    def source_bindings(self):
        if not self.is_valid():
            return {}
        nodes = {}
        endpoints = {}
        binding_groups = {
            'nodes': nodes,
            'endpoints': endpoints,
        }
        for definition in self.source_binding_definitions:
            selected = self.cleaned_data.get(definition['field_name'])
            if selected is None:
                continue
            binding_kind = SOURCE_BINDING_PLURALS[definition['kind']]
            binding_groups[binding_kind][definition['address']] = selected
        return {
            'nodes': nodes,
            'endpoints': endpoints,
        }


class FabricOnboardForm(forms.Form):
    name = forms.CharField(max_length=200, label='Fabric Name')
    slug = forms.SlugField(max_length=200, required=False, label='Fabric Slug')
    description = forms.CharField(
        max_length=200,
        required=False,
        label='Description',
    )
    architecture = forms.ModelChoiceField(
        queryset=FabricArchitecture.objects.order_by('name', 'version', 'pk'),
        required=False,
        label='Architecture',
    )
    expected_plane_count = forms.IntegerField(
        min_value=1,
        max_value=128,
        initial=4,
        label='Expected Plane Count',
    )
    tenant = forms.ModelChoiceField(
        queryset=Tenant.objects.order_by('name', 'pk'),
        required=False,
        label='Tenant',
    )
    site = forms.ModelChoiceField(
        queryset=Site.objects.order_by('name', 'pk'),
        required=False,
        label='Site',
    )
    location = forms.ModelChoiceField(
        queryset=Location.objects.select_related('site').order_by('site__name', 'name', 'pk'),
        required=False,
        label='Location',
    )
    tier_depth = forms.IntegerField(
        min_value=1,
        max_value=16,
        initial=3,
        label='Tier Depth',
    )
    disjointness_policy = forms.ChoiceField(
        choices=(
            ('strict', 'Strict'),
            ('balanced', 'Balanced'),
            ('relaxed', 'Relaxed'),
        ),
        initial='strict',
        label='Disjointness Policy',
    )
    default_stamp_template = forms.ModelChoiceField(
        queryset=StampTemplate.objects.order_by('name', 'pk'),
        required=False,
        label='Default Stamp Template',
    )
    activate_fabric = forms.BooleanField(
        required=False,
        initial=True,
        label='Activate Fabric',
    )
    trigger_initial_rebuild = forms.BooleanField(
        required=False,
        initial=False,
        label='Trigger Initial Graph Rebuild',
    )

    def clean(self):
        cleaned_data = super().clean()
        name = (cleaned_data.get('name') or '').strip()
        raw_slug = (cleaned_data.get('slug') or '').strip()
        slug = raw_slug or slugify(name)

        if not slug:
            self.add_error('slug', 'Unable to derive a slug from Fabric Name.')
            return cleaned_data

        if Fabric.objects.filter(slug=slug).exists():
            self.add_error('slug', 'A fabric with this slug already exists.')
        if Fabric.objects.filter(name=name).exists():
            self.add_error('name', 'A fabric with this name already exists.')

        location = cleaned_data.get('location')
        site = cleaned_data.get('site')
        if location is not None and site is not None and location.site_id != site.pk:
            self.add_error('location', 'Location must belong to the selected site.')

        cleaned_data['name'] = name
        cleaned_data['slug'] = slug
        return cleaned_data


class FabricPlaneAssignmentActionForm(forms.Form):
    action = forms.ChoiceField(
        choices=(
            ('toggle', 'Toggle Plane Assignment'),
            ('assign_all', 'Assign All Planes'),
            ('remove_all', 'Remove All Plane Assignments'),
        ),
    )
    unit_pk = forms.IntegerField(min_value=1)
    plane_pk = forms.IntegerField(required=False, min_value=1)

    def clean(self):
        cleaned_data = super().clean()
        action = cleaned_data.get('action')
        plane_pk = cleaned_data.get('plane_pk')
        if action == 'toggle' and plane_pk is None:
            self.add_error('plane_pk', 'Plane selection is required for toggle actions.')
        return cleaned_data


class FabricOperationsActionForm(forms.Form):
    action = forms.ChoiceField(
        choices=(
            ('rebuild', 'Trigger Graph Rebuild'),
            ('audit', 'Trigger Audit'),
        ),
    )


class StampTemplateBuilderConnectorForm(forms.Form):
    side = forms.ChoiceField(
        choices=(
            ('A', 'A-Side'),
            ('B', 'B-Side'),
        ),
    )
    connector_number = forms.IntegerField(min_value=1)
    label = forms.CharField(max_length=100, required=False)
    connector_type = forms.ChoiceField(
        choices=BUILDER_CONNECTOR_TYPE_CHOICES,
        initial='mpo-12',
    )
    position_count = forms.IntegerField(min_value=1, max_value=96, initial=12)


class StampTemplateBuilderToggleMappingForm(forms.Form):
    a_connector_pk = forms.IntegerField(min_value=1)
    b_connector_pk = forms.IntegerField(min_value=1)


class StampTemplateBuilderPresetForm(forms.Form):
    preset_name = forms.ChoiceField(
        choices=(
            ('straight', 'Straight'),
            ('reversed', 'Reversed'),
            ('cross', 'Cross'),
        ),
    )


class StampTemplateDeploymentActionForm(forms.Form):
    action = forms.ChoiceField(
        choices=(
            ('execute_stamp', 'Execute Stamp'),
            ('preview_stamp', 'Preview Stamp'),
            ('skip_stamp', 'Skip Stamp'),
            ('execute_plan', 'Execute Plan'),
        ),
    )
    stamp_pk = forms.IntegerField(required=False, min_value=1)
    current_step = forms.IntegerField(required=False, min_value=0)


class StampTemplateRerunForm(forms.Form):
    fabric_name = forms.CharField(max_length=200)
    fabric_slug = forms.SlugField(max_length=200)

    @classmethod
    def initial_from_run(cls, stamp_run):
        parameters = stamp_run.parameters or {}
        fabric_name = (parameters.get('fabric_name') or '').strip() or f'Stamp run {stamp_run.pk}'
        fabric_slug = (parameters.get('fabric_slug') or '').strip() or slugify(fabric_name)
        return {
            'fabric_name': fabric_name,
            'fabric_slug': fabric_slug,
        }


class OperationExecuteForm(forms.Form):
    profile = forms.ChoiceField(
        choices=(
            ('generic_roce', 'Generic RoCE'),
            ('madison_default', 'Madison Default'),
        ),
        initial='generic_roce',
        label='Profile',
    )
    fabric = forms.ModelChoiceField(
        queryset=Fabric.objects.order_by('name', 'pk'),
        required=False,
        label='Fabric',
        help_text='Optional. If omitted, the first fabric is used.',
    )
    parameters_json = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
        label='Parameters (JSON)',
        help_text='Optional JSON payload passed to the operation profile.',
    )

    def clean_parameters_json(self):
        raw = self.cleaned_data.get('parameters_json') or ''
        if not raw.strip():
            return {}
        import json
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(f'Invalid JSON: {exc}') from exc
        if not isinstance(payload, dict):
            raise forms.ValidationError('Parameters JSON must decode to an object.')
        return payload


class SuppressionRuleRevokeForm(forms.Form):
    reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
        label='Revoke Reason',
    )


class AuditFindingActionForm(forms.Form):
    comment = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 2}),
        label='Comment',
    )


class AuditFindingAcknowledgeForm(AuditFindingActionForm):
    pass


class AuditFindingStartRemediationForm(AuditFindingActionForm):
    pass


class AuditFindingSuppressForm(AuditFindingActionForm):
    reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 2}),
        label='Suppression Reason',
    )
    expires_at = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        label='Suppress Until',
    )

    def clean_expires_at(self):
        expires_at = self.cleaned_data.get('expires_at')
        if expires_at is None:
            return None
        if expires_at <= timezone.now():
            raise forms.ValidationError('Suppression expiry must be in the future.')
        return expires_at


class AuditFindingUnsuppressForm(AuditFindingActionForm):
    reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 2}),
        label='Unsuppress Reason',
    )


class AuditFindingResolveForm(AuditFindingActionForm):
    resolution_summary = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 2}),
        label='Resolution Summary',
    )


class AuditFindingReopenForm(AuditFindingActionForm):
    reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 2}),
        label='Reopen Reason',
    )


class DisjointnessExceptionRequestForm(forms.Form):
    fabric = forms.ModelChoiceField(
        queryset=Fabric.objects.order_by('name', 'pk'),
        label='Fabric',
    )
    plane = forms.ModelChoiceField(
        queryset=Plane.objects.select_related('fabric').order_by('fabric__name', 'plane_number', 'pk'),
        required=False,
        label='Plane Scope',
    )
    optical_lane = forms.ModelChoiceField(
        queryset=OpticalLane.objects.select_related('fabric', 'plane', 'endpoint').order_by(
            'fabric__name', 'endpoint__address', 'lane_index', 'pk'
        ),
        required=False,
        label='Optical Lane Scope',
    )
    policy_key = forms.CharField(
        required=False,
        max_length=200,
        initial='disjointness',
        label='Policy Scope Key',
    )
    reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
        label='Reason',
    )
    expires_at = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        label='Expires At',
    )

    def clean(self):
        cleaned_data = super().clean()
        fabric = cleaned_data.get('fabric')
        plane = cleaned_data.get('plane')
        optical_lane = cleaned_data.get('optical_lane')
        expires_at = cleaned_data.get('expires_at')

        if plane is not None and fabric is not None and plane.fabric_id != fabric.pk:
            self.add_error('plane', 'Plane must belong to the selected fabric.')
        if optical_lane is not None and fabric is not None and optical_lane.fabric_id != fabric.pk:
            self.add_error('optical_lane', 'Optical lane must belong to the selected fabric.')
        if (
            plane is not None
            and optical_lane is not None
            and optical_lane.plane_id is not None
            and optical_lane.plane_id != plane.pk
        ):
            self.add_error('optical_lane', 'Optical lane plane must match Plane Scope when both are provided.')
        if expires_at is not None and expires_at <= timezone.now():
            self.add_error('expires_at', 'Expiry must be in the future.')

        return cleaned_data


class DisjointnessExceptionApproveForm(forms.Form):
    comment = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 2}),
        label='Approval Comment',
    )


class DisjointnessExceptionExpireForm(forms.Form):
    comment = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 2}),
        label='Expire Comment',
    )


class DisjointnessExceptionReactivateForm(forms.Form):
    comment = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 2}),
        label='Reactivation Comment',
    )
    expires_at = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        label='New Expiry (optional)',
    )

    def clean_expires_at(self):
        expires_at = self.cleaned_data.get('expires_at')
        if expires_at is None:
            return None
        if expires_at <= timezone.now():
            raise forms.ValidationError('Reactivation expiry must be in the future.')
        return expires_at


class CoordinateLayoutUpdateForm(forms.Form):
    endpoint = forms.ModelChoiceField(
        queryset=Endpoint.objects.select_related('fabric').order_by('fabric__name', 'address', 'pk'),
        label='Endpoint',
    )
    position_x = forms.DecimalField(max_digits=12, decimal_places=3, required=False)
    position_y = forms.DecimalField(max_digits=12, decimal_places=3, required=False)
    position_z = forms.DecimalField(max_digits=12, decimal_places=3, required=False)
    orientation = forms.DecimalField(max_digits=8, decimal_places=3, required=False)
    coordinate_unit = forms.ChoiceField(
        choices=(('m', 'Meters'), ('mm', 'Millimeters')),
        initial='m',
    )

    def save(self):
        endpoint = self.cleaned_data['endpoint']
        spatial = {
            'x': self.cleaned_data.get('position_x'),
            'y': self.cleaned_data.get('position_y'),
            'z': self.cleaned_data.get('position_z'),
            'orientation': self.cleaned_data.get('orientation'),
            'unit': self.cleaned_data.get('coordinate_unit') or 'm',
        }
        endpoint.metadata = {
            **(endpoint.metadata or {}),
            'spatial': spatial,
        }
        endpoint.save(update_fields=['metadata', 'last_updated'])
        return endpoint


class OnboardingSourceArtifactAttachForm(forms.Form):
    artifact_type = forms.ChoiceField(
        choices=OnboardingSourceArtifactTypeChoices.CHOICES,
        initial='api_payload',
        label='Artifact Type',
    )
    name = forms.CharField(max_length=200, label='Name')
    source_uri = forms.CharField(max_length=1000, required=False, label='Source URI')
    payload_version = forms.CharField(max_length=64, required=False, label='Payload Version')
    source_label = forms.CharField(max_length=200, required=False, label='Source Label')
    parser_key = forms.CharField(
        max_length=100,
        required=False,
        label='Parser Key',
        help_text='Leave blank to infer from artifact type and payload shape.',
    )
    raw_payload = forms.JSONField(
        required=False,
        label='JSON Payload',
        widget=forms.Textarea(attrs={'rows': 8}),
    )

    def clean_raw_payload(self):
        return self.cleaned_data.get('raw_payload') or {}


class OnboardingPrerequisiteResolveForm(forms.Form):
    resolution_mode = forms.ChoiceField(
        choices=(
            ('bind', 'Bind Existing'),
            ('create', 'Create From Planned Payload'),
            ('defer', 'Defer'),
            ('not_required', 'Not Required'),
            ('unresolved', 'Mark Unresolved'),
        ),
        label='Resolution',
    )
    object_model = forms.CharField(max_length=100, required=False, label='Object Model')
    object_id = forms.IntegerField(min_value=1, required=False, label='Object ID')
    planned_create = forms.JSONField(
        required=False,
        label='Planned Create Payload',
        widget=forms.Textarea(attrs={'rows': 3}),
    )
    defer_reason = forms.CharField(
        required=False,
        label='Defer Reason',
        widget=forms.Textarea(attrs={'rows': 2}),
    )

    def clean(self):
        cleaned_data = super().clean()
        mode = cleaned_data.get('resolution_mode')
        if mode == 'bind' and not cleaned_data.get('object_id'):
            self.add_error('object_id', 'Object ID is required when binding a prerequisite.')
        if mode == 'defer' and not cleaned_data.get('defer_reason'):
            self.add_error('defer_reason', 'A defer reason is required.')
        return cleaned_data


class OnboardingPlanApprovalForm(forms.Form):
    acknowledge_warnings = forms.BooleanField(
        required=False,
        label='Acknowledge Warnings',
    )
    note = forms.CharField(
        required=False,
        label='Approval Note',
        widget=forms.Textarea(attrs={'rows': 2}),
    )


class ArchitectureSourceArtifactAttachForm(forms.Form):
    artifact_type = forms.ChoiceField(
        choices=ArchitectureSourceArtifactTypeChoices.CHOICES,
        initial='blueprint_bundle',
        label='Artifact Type',
    )
    name = forms.CharField(max_length=200, label='Name')
    source_uri = forms.CharField(max_length=1000, required=False, label='Source URI')
    payload_version = forms.CharField(max_length=64, required=False, label='Payload Version')
    source_label = forms.CharField(max_length=200, required=False, label='Source Label')
    parser_key = forms.CharField(
        max_length=100,
        required=False,
        label='Parser Key',
        help_text='Leave blank to infer from artifact type and payload shape.',
    )
    raw_payload = forms.JSONField(
        required=False,
        label='JSON Payload',
        widget=forms.Textarea(attrs={'rows': 8}),
    )

    def clean_raw_payload(self):
        return self.cleaned_data.get('raw_payload') or {}


class ArchitecturePublishPlanApprovalForm(forms.Form):
    acknowledge_warnings = forms.BooleanField(
        required=False,
        label='Acknowledge Warnings',
    )
    note = forms.CharField(
        required=False,
        label='Approval Note',
        widget=forms.Textarea(attrs={'rows': 2}),
    )


__all__ = (
    'StampTemplateExecuteForm',
    'FabricOnboardForm',
    'FabricPlaneAssignmentActionForm',
    'FabricOperationsActionForm',
    'StampTemplateBuilderConnectorForm',
    'StampTemplateBuilderToggleMappingForm',
    'StampTemplateBuilderPresetForm',
    'StampTemplateDeploymentActionForm',
    'StampTemplateRerunForm',
    'OperationExecuteForm',
    'SuppressionRuleRevokeForm',
    'AuditFindingActionForm',
    'AuditFindingAcknowledgeForm',
    'AuditFindingStartRemediationForm',
    'AuditFindingSuppressForm',
    'AuditFindingUnsuppressForm',
    'AuditFindingResolveForm',
    'AuditFindingReopenForm',
    'DisjointnessExceptionRequestForm',
    'DisjointnessExceptionApproveForm',
    'DisjointnessExceptionExpireForm',
    'DisjointnessExceptionReactivateForm',
    'CoordinateLayoutUpdateForm',
    'OnboardingSourceArtifactAttachForm',
    'OnboardingPrerequisiteResolveForm',
    'OnboardingPlanApprovalForm',
    'ArchitectureSourceArtifactAttachForm',
    'ArchitecturePublishPlanApprovalForm',
) + tuple(
    name
    for spec in V2_OBJECT_SPECS
    for name in (spec.form_name, spec.filter_form_name)
)
