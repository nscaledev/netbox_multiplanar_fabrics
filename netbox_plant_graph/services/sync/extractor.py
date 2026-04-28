from dataclasses import dataclass

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q

from dcim.models import Cable, CablePath, CableTermination, Device, FrontPort, Interface, PortMapping, RearPort

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


def _resolve_scope(scope):
    fabric = _scope_value(scope, 'fabric')
    if fabric is None and isinstance(scope, Fabric):
        fabric = scope

    # Explicit scope parameters win over the fabric's stored scope defaults.
    site = _scope_value(scope, 'site')
    if site is None:
        site = getattr(fabric, 'scope_site', None)

    location = _scope_value(scope, 'location')
    if location is None:
        location = getattr(fabric, 'scope_location', None)

    return fabric, site, location


def extract_source_bundle(*, scope=None) -> SourceBundle:
    fabric, site, location = _resolve_scope(scope)

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

    if device_ids:
        # Filter cables at the DB level — avoid loading the entire cable table into memory.
        iface_ct = ContentType.objects.get_for_model(Interface)
        fp_ct = ContentType.objects.get_for_model(FrontPort)
        rp_ct = ContentType.objects.get_for_model(RearPort)

        all_iface_ids = set(interface_queryset.values_list('pk', flat=True))
        all_fp_ids = set(FrontPort.objects.filter(device_id__in=device_ids).values_list('pk', flat=True))
        all_rp_ids = set(RearPort.objects.filter(device_id__in=device_ids).values_list('pk', flat=True))

        ct_conditions = Q(termination_type=iface_ct, termination_id__in=all_iface_ids)
        if all_fp_ids:
            ct_conditions |= Q(termination_type=fp_ct, termination_id__in=all_fp_ids)
        if all_rp_ids:
            ct_conditions |= Q(termination_type=rp_ct, termination_id__in=all_rp_ids)

        relevant_cable_ids = set(
            CableTermination.objects.filter(ct_conditions).values_list('cable_id', flat=True)
        )
        cables = tuple(
            Cable.objects.filter(pk__in=relevant_cable_ids)
            .prefetch_related('terminations__termination')
        )

        # CablePath endpoints: use Interface._path_id FK (direct, indexed).
        # FrontPort/_path and RearPort/_path are only present in some NetBox versions;
        # guard with hasattr to avoid FieldError on versions that lack them.
        path_ids = set(
            Interface.objects.filter(pk__in=all_iface_ids, _path__isnull=False)
            .values_list('_path_id', flat=True)
        )
        if all_fp_ids and hasattr(FrontPort, '_path'):
            path_ids |= set(
                FrontPort.objects.filter(pk__in=all_fp_ids, _path__isnull=False)
                .values_list('_path_id', flat=True)
            )
        if all_rp_ids and hasattr(RearPort, '_path'):
            path_ids |= set(
                RearPort.objects.filter(pk__in=all_rp_ids, _path__isnull=False)
                .values_list('_path_id', flat=True)
            )
        cable_paths = tuple(CablePath.objects.filter(pk__in=path_ids)) if path_ids else ()
    else:
        cables = ()
        cable_paths = ()

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
