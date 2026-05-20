from django.core.checks import Tags, register


@register(Tags.models)
def check_v2_kernel(app_configs, **kwargs):
    """
    V2 intentionally does not require NetBox CablePath custom fields,
    PortMapping compatibility, audit state, or floorplan integration.
    """
    return []
