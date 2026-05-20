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


__all__ = tuple(
    name
    for spec in V2_OBJECT_SPECS
    for name in (spec.form_name, spec.filter_form_name)
)
