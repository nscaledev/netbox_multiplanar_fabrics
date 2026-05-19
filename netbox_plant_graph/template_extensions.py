import re
from urllib.parse import urlencode

from django.db.models import Count, Q
from django.urls import reverse
from netbox.plugins.templates import PluginTemplateExtension

from .models import AttachmentUnit, SignalLane
from .services.netbox.lookup import get_registry_key_for_object

SHUFFLE_BOX_DEVICE_TYPE_SLUG = 'shuffle-box-3tray-18cassette'
SHUFFLE_CASSETTE_DEVICE_TYPE_SLUG = 'shuffle-cassette-2x2-mpo'
SHUFFLE_DEVICE_TYPE_SLUGS = {
    SHUFFLE_BOX_DEVICE_TYPE_SLUG,
    SHUFFLE_CASSETTE_DEVICE_TYPE_SLUG,
}

CASSETTE_BAY_RE = re.compile(r'^cassette-(?P<tray>\d+)\.(?P<slot>\d+)$')


def _build_operational_url(view_name: str, params: dict[str, object]) -> str:
    return f"{reverse(view_name)}?{urlencode(params)}"


def _default_resolution(obj) -> str:
    if isinstance(obj, SignalLane):
        return 'signal_lane'
    return 'attachment_unit'


def _supports_signal_lane_mode(obj) -> bool:
    if isinstance(obj, SignalLane):
        return True
    if isinstance(obj, AttachmentUnit):
        return obj.signal_lanes.exists()
    return False


def _object_url(obj) -> str:
    if obj is None:
        return ''
    get_absolute_url = getattr(obj, 'get_absolute_url', None)
    if callable(get_absolute_url):
        return get_absolute_url()
    return ''


def _device_type_slug(device) -> str:
    return getattr(getattr(device, 'device_type', None), 'slug', '') or ''


def _parent_device(device):
    from dcim.models import Device

    if device is None:
        return None
    try:
        parent_bay = device.parent_bay
    except Device.parent_bay.RelatedObjectDoesNotExist:
        return None
    return parent_bay.device


def _containing_shuffle_box(device):
    device_type_slug = _device_type_slug(device)
    if device_type_slug == SHUFFLE_BOX_DEVICE_TYPE_SLUG:
        return device

    parent = _parent_device(device)
    if device_type_slug == SHUFFLE_CASSETTE_DEVICE_TYPE_SLUG:
        if _device_type_slug(parent) == SHUFFLE_BOX_DEVICE_TYPE_SLUG:
            return parent
        return None

    return None


def _port_counts(model, device_ids: list[int]) -> dict[int, dict[str, int]]:
    counts = {
        device_id: {'total': 0, 'linked': 0}
        for device_id in device_ids
    }
    if not device_ids:
        return counts

    for row in (
        model.objects.filter(device_id__in=device_ids, type='mpo')
        .values('device_id')
        .annotate(
            total=Count('pk'),
            linked=Count('pk', filter=Q(cable__isnull=False) | Q(mark_connected=True)),
        )
    ):
        counts[row['device_id']] = {
            'total': row['total'],
            'linked': row['linked'],
        }
    return counts


def _build_shuffle_containment_tree(box, current_device=None):
    from dcim.models import DeviceBay, FrontPort, RearPort

    current_device = current_device or box
    current_device_type_slug = _device_type_slug(current_device)
    box_bays = list(
        DeviceBay.objects.filter(device=box)
        .select_related('installed_device', 'installed_device__device_type')
        .order_by('name')
    )
    cassettes = [
        bay.installed_device
        for bay in box_bays
        if bay.installed_device_id and _device_type_slug(bay.installed_device) == SHUFFLE_CASSETTE_DEVICE_TYPE_SLUG
    ]
    cassette_ids = [cassette.pk for cassette in cassettes]
    front_counts = _port_counts(FrontPort, cassette_ids)
    rear_counts = _port_counts(RearPort, cassette_ids)

    trays_by_index = {
        str(index): {
            'index': str(index),
            'name': f'Tray {index}',
            'cassettes': [],
            'is_populated': False,
        }
        for index in range(1, 4)
    }
    populated_cassette_count = 0
    for box_bay in box_bays:
        match = CASSETTE_BAY_RE.match(box_bay.name)
        if match:
            tray_index = match.group('tray')
            slot_index = match.group('slot')
        else:
            tray_index = 'other'
            slot_index = box_bay.name
        tray_node = trays_by_index.setdefault(tray_index, {
            'index': tray_index,
            'name': 'Unclassified',
            'cassettes': [],
            'is_populated': False,
        })
        cassette = box_bay.installed_device
        cassette_node = {
            'bay': box_bay,
            'slot_index': slot_index,
            'device': cassette,
            'device_url': _object_url(cassette),
            'is_current': bool(cassette) and cassette.pk == current_device.pk,
            'is_expected_type': (
                bool(cassette) and _device_type_slug(cassette) == SHUFFLE_CASSETTE_DEVICE_TYPE_SLUG
            ),
            'front_ports': front_counts.get(getattr(cassette, 'pk', None), {'total': 0, 'linked': 0}),
            'rear_ports': rear_counts.get(getattr(cassette, 'pk', None), {'total': 0, 'linked': 0}),
        }
        if cassette_node['is_expected_type']:
            populated_cassette_count += 1
            tray_node['is_populated'] = True
        tray_node['cassettes'].append(cassette_node)

    tree = sorted(
        trays_by_index.values(),
        key=lambda tray: int(tray['index']) if str(tray['index']).isdigit() else 999,
    )
    populated_tray_count = sum(1 for tray in tree if tray['is_populated'])

    return {
        'box': box,
        'box_url': _object_url(box),
        'box_is_current': box.pk == current_device.pk,
        'current_device': current_device,
        'current_device_url': _object_url(current_device),
        'current_device_type_slug': current_device_type_slug,
        'current_role': {
            SHUFFLE_BOX_DEVICE_TYPE_SLUG: 'box',
            SHUFFLE_CASSETTE_DEVICE_TYPE_SLUG: 'cassette',
        }.get(current_device_type_slug, 'shuffle component'),
        'trays': tree,
        'populated_tray_count': populated_tray_count,
        'total_tray_bay_count': len(tree),
        'populated_cassette_count': populated_cassette_count,
        'total_cassette_bay_count': len(box_bays),
    }


class PlantGraphObjectBadges(PluginTemplateExtension):
    models = [
        'dcim.interface',
        'dcim.frontport',
        'dcim.rearport',
        'netbox_plant_graph.attachmentunit',
        'netbox_plant_graph.signallane',
    ]

    def right_page(self):
        obj = self.context['object']
        registry_key = get_registry_key_for_object(obj)
        if registry_key is None:
            return ''

        resolution = _default_resolution(obj)
        graph_links = {
            'path_resolver': _build_operational_url('plugins:netbox_plant_graph:path_resolver', {
                'source_registry_key': registry_key,
                'source_id': obj.pk,
                'resolution': resolution,
            }),
            'lane_drilldown': _build_operational_url('plugins:netbox_plant_graph:lane_drilldown', {
                'target_registry_key': registry_key,
                'target_id': obj.pk,
            }),
            'blast_radius': _build_operational_url('plugins:netbox_plant_graph:blast_radius', {
                'target_registry_key': registry_key,
                'target_id': obj.pk,
                'resolution': resolution,
            }),
        }
        if _supports_signal_lane_mode(obj):
            graph_links['signal_path_resolver'] = _build_operational_url('plugins:netbox_plant_graph:path_resolver', {
                'source_registry_key': registry_key,
                'source_id': obj.pk,
                'resolution': 'signal_lane',
            })
            graph_links['signal_blast_radius'] = _build_operational_url('plugins:netbox_plant_graph:blast_radius', {
                'target_registry_key': registry_key,
                'target_id': obj.pk,
                'resolution': 'signal_lane',
            })

        return self.render('netbox_plant_graph/includes/object_badges.html', extra_context={
            'object': obj,
            'graph_links': graph_links,
            'registry_key': registry_key,
            'default_resolution': resolution,
        })


class MadisonShuffleContainmentTree(PluginTemplateExtension):
    models = ['dcim.device']

    def left_page(self):
        obj = self.context['object']
        if _device_type_slug(obj) not in SHUFFLE_DEVICE_TYPE_SLUGS:
            return ''

        box = _containing_shuffle_box(obj)
        if box is None:
            return ''

        return self.render('netbox_plant_graph/includes/madison_shuffle_containment.html', extra_context={
            'containment': _build_shuffle_containment_tree(box, current_device=obj),
        })


template_extensions = [PlantGraphObjectBadges, MadisonShuffleContainmentTree]
