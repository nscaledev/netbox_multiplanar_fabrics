from dataclasses import dataclass

from dcim.models import Cable, CablePath, Device, FrontPort, Interface, PortMapping, RearPort

from netbox_plant_graph.models import Fabric


@dataclass(frozen=True)
class SourceBundle:
    fabric: Fabric | None = None
    devices: tuple = ()
    interfaces: tuple = ()
    front_ports: tuple = ()
    rear_ports: tuple = ()
    child_interfaces: tuple = ()
    cable_paths: tuple = ()
    cables: tuple = ()
    port_mappings: tuple = ()


def _scope_value(scope, name):
    if scope is None:
        return None
    if isinstance(scope, dict):
        return scope.get(name)
    return getattr(scope, name, None)


def _relevant_device_id(obj):
    if obj is None:
        return None
    if isinstance(obj, Device):
        return obj.pk
    return getattr(obj, 'device_id', None)


def _path_is_relevant(path, device_ids: set[int]) -> bool:
    for step in path.path_objects:
        for obj in step:
            if _relevant_device_id(obj) in device_ids:
                return True
    return False


def _cable_is_relevant(cable, device_ids: set[int]) -> bool:
    for cable_termination in cable.terminations.all():
        if _relevant_device_id(cable_termination.termination) in device_ids:
            return True
    return False


def extract_source_bundle(*, scope=None) -> SourceBundle:
    fabric = _scope_value(scope, 'fabric')
    site = _scope_value(scope, 'site') or getattr(fabric, 'scope_site', None)
    location = _scope_value(scope, 'location') or getattr(fabric, 'scope_location', None)

    device_queryset = Device.objects.all().select_related('site', 'location', 'device_type', 'role')
    if site is not None:
        device_queryset = device_queryset.filter(site=site)
    if location is not None:
        device_queryset = device_queryset.filter(location=location)

    devices = tuple(device_queryset)
    device_ids = {device.pk for device in devices}

    interface_queryset = Interface.objects.filter(device_id__in=device_ids).select_related('device', 'parent')
    interfaces = tuple(interface_queryset.filter(parent__isnull=True))
    child_interfaces = tuple(interface_queryset.filter(parent__isnull=False))
    front_ports = tuple(FrontPort.objects.filter(device_id__in=device_ids).select_related('device'))
    rear_ports = tuple(RearPort.objects.filter(device_id__in=device_ids).select_related('device'))
    port_mappings = tuple(PortMapping.objects.filter(device_id__in=device_ids).select_related('device', 'front_port', 'rear_port'))

    cable_paths = tuple(CablePath.objects.all())
    cables = tuple(Cable.objects.all().prefetch_related('terminations__termination'))

    if device_ids:
        cable_paths = tuple(path for path in cable_paths if _path_is_relevant(path, device_ids))
        cables = tuple(cable for cable in cables if _cable_is_relevant(cable, device_ids))

    return SourceBundle(
        fabric=fabric,
        devices=devices,
        interfaces=interfaces,
        front_ports=front_ports,
        rear_ports=rear_ports,
        child_interfaces=child_interfaces,
        cable_paths=cable_paths,
        cables=cables,
        port_mappings=port_mappings,
    )
