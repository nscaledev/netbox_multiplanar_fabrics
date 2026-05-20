from django import forms
from django.utils.text import slugify
from dcim.models import Device, Interface
from netbox.forms import NetBoxModelFilterSetForm, NetBoxModelForm

from .v2_registry import V2_OBJECT_SPECS


SOURCE_BINDING_MODELS = {
    'dcim.device': Device,
    'dcim.interface': Interface,
}
SOURCE_BINDING_PLURALS = {
    'node': 'nodes',
    'endpoint': 'endpoints',
}


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

    @classmethod
    def initial_from_template(cls, template):
        fabric_name = template.name
        if fabric_name.lower().endswith(' proof'):
            fabric_name = fabric_name
        return {
            'fabric_name': fabric_name,
            'fabric_slug': slugify(fabric_name),
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


__all__ = ('StampTemplateExecuteForm',) + tuple(
    name
    for spec in V2_OBJECT_SPECS
    for name in (spec.form_name, spec.filter_form_name)
)
