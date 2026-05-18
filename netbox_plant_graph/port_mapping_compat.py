from __future__ import annotations

from dataclasses import dataclass

from dcim.models import FrontPort

try:  # NetBox >= 4.3/4.4+ lines expose dcim.PortMapping
    from dcim.models import PortMapping as _PortMapping  # type: ignore[attr-defined]
except ImportError:  # NetBox 4.2.x fallback
    _PortMapping = None


@dataclass
class _CompatPortMappingRecord:
    pk: int
    device_id: int
    front_port_id: int
    front_port_position: int
    rear_port_id: int
    rear_port_position: int
    device: object
    front_port: object
    rear_port: object


def _iter_records():
    for front_port in FrontPort.objects.exclude(rear_port_id__isnull=True).select_related('device', 'rear_port'):
        yield _CompatPortMappingRecord(
            pk=front_port.pk,
            device_id=front_port.device_id,
            front_port_id=front_port.pk,
            front_port_position=1,
            rear_port_id=front_port.rear_port_id,
            rear_port_position=front_port.rear_port_position,
            device=front_port.device,
            front_port=front_port,
            rear_port=front_port.rear_port,
        )


def _matches(record: _CompatPortMappingRecord, **kwargs) -> bool:
    for key, value in kwargs.items():
        if key == 'device_id__in':
            if record.device_id not in value:
                return False
        elif key in ('device', 'front_port', 'rear_port'):
            if getattr(record, f'{key}_id') != getattr(value, 'pk', value):
                return False
        elif key in ('device_id', 'front_port_id', 'rear_port_id', 'front_port_position', 'rear_port_position'):
            if getattr(record, key) != value:
                return False
    return True


class _CompatPortMappingQuerySet:
    def __init__(self, records: list[_CompatPortMappingRecord]):
        self._records = records

    def __iter__(self):
        return iter(self._records)

    def filter(self, **kwargs):
        return _CompatPortMappingQuerySet([record for record in self._records if _matches(record, **kwargs)])

    def select_related(self, *_args, **_kwargs):
        return self

    def values_list(self, *fields, flat=False):
        if flat:
            return [getattr(record, fields[0]) for record in self._records]
        return [tuple(getattr(record, field) for field in fields) for record in self._records]

    def delete(self):
        for record in self._records:
            front_port = FrontPort.objects.get(pk=record.front_port_id)
            rear_port = front_port.rear_port or front_port.device.rearports.order_by('pk').first()
            if rear_port is None:
                from dcim.models import RearPort
                rear_port = RearPort.objects.create(device=front_port.device, name=f'{front_port.name}-unmapped-rear', positions=1)
            front_port.rear_port = rear_port
            current_positions = getattr(rear_port, 'positions', 1) or 1
            front_port.rear_port_position = int(current_positions) + 1000
            front_port.save(update_fields=('rear_port', 'rear_port_position'))


class _CompatPortMappingManager:
    def all(self):
        return _CompatPortMappingQuerySet(list(_iter_records()))

    def filter(self, **kwargs):
        return self.all().filter(**kwargs)

    def create(self, *, device, front_port, front_port_position, rear_port, rear_port_position, **_kwargs):
        front_port.device = device
        front_port.rear_port = rear_port
        front_port.rear_port_position = rear_port_position
        front_port.save(update_fields=('device', 'rear_port', 'rear_port_position'))
        return _CompatPortMappingRecord(
            pk=front_port.pk,
            device_id=device.pk,
            front_port_id=front_port.pk,
            front_port_position=front_port_position,
            rear_port_id=rear_port.pk,
            rear_port_position=rear_port_position,
            device=device,
            front_port=front_port,
            rear_port=rear_port,
        )

    def get_or_create(self, **kwargs):
        existing = self.filter(**kwargs)
        for record in existing:
            return record, False
        return self.create(**kwargs), True

    def bulk_create(self, mappings, batch_size=None):
        del batch_size
        created = []
        for mapping in mappings:
            created.append(self.create(
                device=mapping.device,
                front_port=mapping.front_port,
                front_port_position=mapping.front_port_position,
                rear_port=mapping.rear_port,
                rear_port_position=mapping.rear_port_position,
            ))
        return created


if _PortMapping is not None:
    PortMapping = _PortMapping
else:
    class PortMapping:  # type: ignore[no-redef]
        objects = _CompatPortMappingManager()

        def __init__(self, *, device, front_port, front_port_position, rear_port, rear_port_position):
            self.device = device
            self.front_port = front_port
            self.front_port_position = front_port_position
            self.rear_port = rear_port
            self.rear_port_position = rear_port_position
