from django import forms
from netbox.forms import NetBoxModelFilterSetForm, NetBoxModelForm

from .object_registry import FILTER_FORM_OBJECT_SPECS, FORM_OBJECT_SPECS


def build_form_class(spec):
    meta_class = type('Meta', (), {'model': spec.model, 'fields': spec.form.fields})
    return type(spec.form.class_name, (NetBoxModelForm,), {'__module__': __name__, 'Meta': meta_class})


def build_filter_form_class(spec):
    attrs = {
        '__module__': __name__,
        'model': spec.model,
        'q': forms.CharField(required=False, label='Search'),
    }
    for field_name in spec.filter_form.fields:
        attrs[field_name] = forms.CharField(required=False)
    return type(spec.filter_form.class_name, (NetBoxModelFilterSetForm,), attrs)


for object_spec in FORM_OBJECT_SPECS:
    globals()[object_spec.form.class_name] = build_form_class(object_spec)

for object_spec in FILTER_FORM_OBJECT_SPECS:
    globals()[object_spec.filter_form.class_name] = build_filter_form_class(object_spec)
