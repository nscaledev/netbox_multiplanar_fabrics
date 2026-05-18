#!/usr/bin/env python3
"""
Execute the RoCE fabric runbook via Django ORM.

This models a physically disjoint four-plane RoCE fabric:
- four plane-dedicated leaves per compute hall
- eight plane-dedicated spines in the network hall (two per plane)
- one passive shuffle module per GPU parent NIC port between host and leaf
- one passive shuffle module per leaf uplink between leaf and its plane spine pair

The executable model collapses the physical MPO-12 strand map down to four
logical channels per 800G host port. The runbook markdown carries the full
strand-level shuffle documentation; this script instantiates the corresponding
topology in the smallest NetBox shape that the plugin can rebuild today.
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("NETBOX_PLANT_GRAPH_ENABLE", "1")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "netbox.settings")

import django

django.setup()

from django.contrib.contenttypes.models import ContentType

from dcim.models import (
    Cable,
    CableTermination,
    Device,
    DeviceRole,
    DeviceType,
    FrontPort,
    Interface,
    Location,
    Manufacturer,
    Rack,
    RearPort,
    Site,
)
from dcim.utils import create_cablepaths
from extras.models import CustomField
from tenancy.models import Tenant

from netbox_plant_graph.breakout_profiles import (
    ensure_breakout_profile_custom_field,
    get_breakout_profile_field_name,
    set_plugin_breakout_profile_for_cable,
)
from netbox_plant_graph.models import BreakoutProfile, Fabric, FabricPlane
from netbox_plant_graph.port_mapping_compat import PortMapping


HALL_CODES = ("a", "b", "c")
PLANES = (1, 2, 3, 4)
SPINE_SIDES = ("a", "b")
GPU_RACK_CODES = ("a1", "a2", "b1", "b2")
GPU_SEQUENCES = (("001", 1), ("002", 3))


def ensure_interface(device, name, **defaults):
    iface, _ = Interface.objects.get_or_create(device=device, name=name, defaults=defaults)
    changed = False
    for field, value in defaults.items():
        if field == "custom_field_data":
            existing = iface.custom_field_data or {}
            if any(existing.get(key) != item for key, item in value.items()):
                iface.custom_field_data = {**existing, **value}
                changed = True
            continue
        if getattr(iface, field) != value:
            setattr(iface, field, value)
            changed = True
    if changed:
        iface.save()
    return iface


def ensure_front_port(device, name, **defaults):
    front_port, _ = FrontPort.objects.get_or_create(device=device, name=name, defaults=defaults)
    changed = False
    for field, value in defaults.items():
        if getattr(front_port, field) != value:
            setattr(front_port, field, value)
            changed = True
    if changed:
        front_port.save()
    return front_port


def ensure_rear_port(device, name, **defaults):
    rear_port, _ = RearPort.objects.get_or_create(device=device, name=name, defaults=defaults)
    changed = False
    for field, value in defaults.items():
        if getattr(rear_port, field) != value:
            setattr(rear_port, field, value)
            changed = True
    if changed:
        rear_port.save()
    return rear_port


def make_rack(name, site, location, tenant=None, u=42):
    rack, _ = Rack.objects.get_or_create(
        name=name,
        site=site,
        defaults={"location": location, "tenant": tenant, "u_height": u, "status": "active"},
    )
    return rack


def make_device(name, device_type, role, site, rack, position, tenant=None):
    device, _ = Device.objects.get_or_create(
        name=name,
        defaults={
            "device_type": device_type,
            "role": role,
            "site": site,
            "rack": rack,
            "position": position,
            "face": "front",
            "status": "active",
            "tenant": tenant,
        },
    )
    return device


def make_cable(termination_a, termination_b, *, breakout_profile=None):
    if getattr(termination_a, "cable", None) is not None:
        cable = termination_a.cable
        if breakout_profile is not None:
            set_plugin_breakout_profile_for_cable(cable, breakout_profile)
        return cable

    cable = Cable(
        status="connected",
        a_terminations=[termination_a],
        b_terminations=[termination_b],
    )
    cable.full_clean()
    cable.save()
    if breakout_profile is not None:
        set_plugin_breakout_profile_for_cable(cable, breakout_profile)
    create_cablepaths([termination_a])
    create_cablepaths([termination_b])
    return cable


print("=== Phase 1: Tenants ===")
tenant_alpha, _ = Tenant.objects.get_or_create(
    slug="tenant-alpha",
    defaults={"name": "Tenant Alpha", "description": "GPU workload consumer"},
)
tenant_beta, _ = Tenant.objects.get_or_create(
    slug="tenant-beta",
    defaults={"name": "Tenant Beta", "description": "GPU workload consumer"},
)
tenant_by_rack_code = {"a1": tenant_alpha, "a2": tenant_alpha, "b1": tenant_beta, "b2": tenant_beta}
print(f"  tenant-alpha pk={tenant_alpha.pk}  tenant-beta pk={tenant_beta.pk}")

print("=== Phase 2: Site + Locations ===")
site_dc, _ = Site.objects.get_or_create(slug="dc-alpha", defaults={"name": "DC Alpha", "status": "active"})
loc_building, _ = Location.objects.get_or_create(
    slug="building-1",
    site=site_dc,
    defaults={"name": "Building 1", "site": site_dc},
)
hall_locations = {}
for hall_code, hall_name in (
    ("a", "Hall Compute A"),
    ("b", "Hall Compute B"),
    ("c", "Hall Compute C"),
    ("net", "Hall Network"),
):
    slug = f"hall-compute-{hall_code}" if hall_code != "net" else "hall-network"
    hall_locations[hall_code], _ = Location.objects.get_or_create(
        slug=slug,
        site=site_dc,
        defaults={"name": hall_name, "site": site_dc, "parent": loc_building},
    )
print(
    f"  site={site_dc.pk}  halls: "
    f"a={hall_locations['a'].pk} b={hall_locations['b'].pk} c={hall_locations['c'].pk} net={hall_locations['net'].pk}"
)

print("=== Phase 3: Device Roles ===")
role_gpu, _ = DeviceRole.objects.get_or_create(slug="gpu-server", defaults={"name": "GPU Server", "color": "00bcd4"})
role_roce_leaf, _ = DeviceRole.objects.get_or_create(
    slug="roce-leaf-switch",
    defaults={"name": "RoCE Leaf Switch", "color": "4caf50"},
)
role_roce_spine, _ = DeviceRole.objects.get_or_create(
    slug="roce-spine-switch",
    defaults={"name": "RoCE Spine Switch", "color": "8bc34a"},
)
role_shuffle, _ = DeviceRole.objects.get_or_create(
    slug="gpu-leaf-shuffle-board",
    defaults={"name": "GPU-Leaf Shuffle Board", "color": "607d8b"},
)
role_leaf_spine_shuffle, _ = DeviceRole.objects.get_or_create(
    slug="leaf-spine-shuffle-board",
    defaults={"name": "Leaf-Spine Shuffle Board", "color": "455a64"},
)
role_fs_leaf, _ = DeviceRole.objects.get_or_create(
    slug="frontside-leaf-switch",
    defaults={"name": "Frontside Leaf Switch", "color": "ff9800"},
)
role_fs_spine, _ = DeviceRole.objects.get_or_create(
    slug="frontside-spine-switch",
    defaults={"name": "Frontside Spine Switch", "color": "ff5722"},
)
role_mgmt, _ = DeviceRole.objects.get_or_create(
    slug="management-switch",
    defaults={"name": "Management Switch", "color": "9e9e9e"},
)
role_edge, _ = DeviceRole.objects.get_or_create(
    slug="edge-router",
    defaults={"name": "Edge Router", "color": "795548"},
)
print(
    "  roles created: "
    f"gpu={role_gpu.pk} gpu-shuffle={role_shuffle.pk} "
    f"leaf-spine-shuffle={role_leaf_spine_shuffle.pk} "
    f"roce-leaf={role_roce_leaf.pk} roce-spine={role_roce_spine.pk}"
)

print("=== Phase 5: Manufacturer + Device Types ===")
mfr, _ = Manufacturer.objects.get_or_create(slug="generic", defaults={"name": "Generic"})
dt_gpu, _ = DeviceType.objects.get_or_create(
    slug="gpu-server-8x800g",
    manufacturer=mfr,
    defaults={"model": "GPU-Server-8x800G", "u_height": 2},
)
dt_roce_leaf, _ = DeviceType.objects.get_or_create(
    slug="roce-leaf-200g-plane",
    manufacturer=mfr,
    defaults={"model": "RoCE-Leaf-Plane-200G", "u_height": 1},
)
dt_roce_spine, _ = DeviceType.objects.get_or_create(
    slug="roce-spine-200g-plane",
    manufacturer=mfr,
    defaults={"model": "RoCE-Spine-Plane-200G", "u_height": 1},
)
dt_shuffle, _ = DeviceType.objects.get_or_create(
    slug="gpu-leaf-shuffle-1x4",
    manufacturer=mfr,
    defaults={"model": "GPU-Leaf-Shuffle-1x4", "u_height": 1},
)
dt_leaf_spine_shuffle, _ = DeviceType.objects.get_or_create(
    slug="leaf-spine-shuffle-1x4",
    manufacturer=mfr,
    defaults={"model": "Leaf-Spine-Shuffle-1x4", "u_height": 1},
)
dt_generic, _ = DeviceType.objects.get_or_create(
    slug="generic-1u",
    manufacturer=mfr,
    defaults={"model": "Generic 1U", "u_height": 1},
)
print(
    "  device types: "
    f"gpu={dt_gpu.pk} gpu-shuffle={dt_shuffle.pk} "
    f"leaf-spine-shuffle={dt_leaf_spine_shuffle.pk} "
    f"roce-leaf={dt_roce_leaf.pk} roce-spine={dt_roce_spine.pk}"
)

print("=== Phase 6: Racks ===")
gpu_racks = {}
shuffle_racks = {}
leaf_racks = {}
leaf_spine_shuffle_racks = {}
for hall_code in HALL_CODES:
    hall_location = hall_locations[hall_code]
    hall_upper = hall_code.upper()
    for rack_code in GPU_RACK_CODES:
        tenant = tenant_by_rack_code[rack_code]
        rack_name = f"RACK-{hall_upper}-GPU-{rack_code.upper()}"
        gpu_racks[(hall_code, rack_code)] = make_rack(rack_name, site_dc, hall_location, tenant)
    shuffle_racks[hall_code] = make_rack(f"RACK-{hall_upper}-SHUFFLE-1", site_dc, hall_location)
    for plane in PLANES:
        leaf_racks[(hall_code, plane)] = make_rack(
            f"RACK-{hall_upper}-ROCE-LEAF-P{plane}",
            site_dc,
            hall_location,
        )

spine_racks = {}
for plane in PLANES:
    leaf_spine_shuffle_racks[plane] = make_rack(
        f"RACK-NET-ROCE-SHUFFLE-P{plane}",
        site_dc,
        hall_locations["net"],
    )
    for side in SPINE_SIDES:
        spine_racks[(plane, side)] = make_rack(
            f"RACK-NET-ROCE-SPINE-P{plane}-{side.upper()}",
            site_dc,
            hall_locations["net"],
        )
make_rack("RACK-NET-FS-SPINE-1", site_dc, hall_locations["net"])
make_rack("RACK-NET-FS-SPINE-2", site_dc, hall_locations["net"])
make_rack("RACK-NET-MGMT-1", site_dc, hall_locations["net"])
make_rack("RACK-NET-EDGE-1", site_dc, hall_locations["net"])
print(
    "  racks created: "
    f"gpu={len(gpu_racks)} hall-shuffle={len(shuffle_racks)} leaf={len(leaf_racks)} "
    f"net-shuffle={len(leaf_spine_shuffle_racks)} spine={len(spine_racks)}"
)

print("=== Phase 7: Devices ===")
gpu_servers = []
for hall_code in HALL_CODES:
    for rack_code in GPU_RACK_CODES:
        rack = gpu_racks[(hall_code, rack_code)]
        tenant = tenant_by_rack_code[rack_code]
        for seq, position in GPU_SEQUENCES:
            device_name = f"gpu-{hall_code}-{rack_code}-{seq}"
            device = make_device(device_name, dt_gpu, role_gpu, site_dc, rack, position, tenant)
            gpu_servers.append({"device": device, "hall": hall_code, "rack_code": rack_code})
print(f"  GPU servers created: {len(gpu_servers)}")

leaf_switches = {}
for hall_code in HALL_CODES:
    hall_suffix = f"h{hall_code}"
    for plane in PLANES:
        leaf_switches[(hall_code, plane)] = make_device(
            f"roce-leaf-{hall_suffix}-p{plane}",
            dt_roce_leaf,
            role_roce_leaf,
            site_dc,
            leaf_racks[(hall_code, plane)],
            1,
        )

spine_switches = {}
for plane in PLANES:
    for side in SPINE_SIDES:
        spine_switches[(plane, side)] = make_device(
            f"roce-spine-net-p{plane}-{side}",
            dt_roce_spine,
            role_roce_spine,
            site_dc,
            spine_racks[(plane, side)],
            1,
        )
print(f"  leaf switches created: {len(leaf_switches)}  spines created: {len(spine_switches)}")

print("=== Phase 8.1: fabric_plane custom field check ===")
cf_exists = CustomField.objects.filter(name="fabric_plane").exists()
if cf_exists:
    print("  fabric_plane CF already exists (auto-created by plugin post_migrate signal)")
else:
    print("  WARNING: fabric_plane CF not found — GAP #4 not closed!")

print("=== Phase 8.2: Breakout profile and cable custom field ===")
breakout_field, _, _ = ensure_breakout_profile_custom_field()
print(f"  breakout CF name={get_breakout_profile_field_name()} pk={breakout_field.pk}")
breakout_profile, _ = BreakoutProfile.objects.get_or_create(
    slug="800g-4x200g",
    defaults={
        "name": "800G-4x200G",
        "description": "Logical four-channel breakout for GPU parent ports",
        "parent_speed_gbps": 800,
        "child_count": 4,
        "child_speed_gbps": 200,
        "mapping_mode": "sequential",
    },
)
print(f"  BreakoutProfile pk={breakout_profile.pk}")

print("=== Phase 8.3: Interfaces on GPU servers ===")
for item in gpu_servers:
    device = item["device"]
    for port_name in ("NIC0", "NIC1"):
        parent = ensure_interface(device, port_name, type="800gbase-cr8", speed=800_000_000)
        for plane in PLANES:
            ensure_interface(
                device,
                f"{port_name}.plane{plane}",
                type="200gbase-cr4",
                speed=200_000_000,
                parent=parent,
                custom_field_data={"fabric_plane": plane},
            )
print(f"  Created/verified GPU parent interfaces: {len(gpu_servers) * 2} (+ 4 children each)")

print("=== Phase 8.4: Interfaces on plane-dedicated RoCE leaves ===")
for (hall_code, plane), leaf in leaf_switches.items():
    for port_num in range(1, 17):
        ensure_interface(
            leaf,
            f"Eth1/{port_num}",
            type="200gbase-cr4",
            speed=200_000_000,
            custom_field_data={"fabric_plane": plane},
        )
    uplink_parent = ensure_interface(
        leaf,
        "Eth1/49",
        type="800gbase-cr8",
        speed=800_000_000,
    )
    for channel in range(1, 5):
        ensure_interface(
            leaf,
            f"Eth1/49.ch{channel}",
            type="200gbase-cr4",
            speed=200_000_000,
            parent=uplink_parent,
            custom_field_data={"fabric_plane": plane},
        )
print("  Leaf downlinks/uplinks created")

print("=== Phase 8.5: Interfaces on plane-dedicated RoCE spines ===")
for (plane, _side), spine in spine_switches.items():
    for hall_index in range(1, 4):
        for offset in range(2):
            port_num = ((hall_index - 1) * 2) + 1 + offset
            ensure_interface(
                spine,
                f"Eth1/{port_num}",
                type="200gbase-cr4",
                speed=200_000_000,
                custom_field_data={"fabric_plane": plane},
            )
print("  Spine interfaces created")

print("=== Phase 8.6: Passive shuffle modules ===")
shuffle_devices = []
leaf_port_counters = {(hall_code, plane): 1 for hall_code in HALL_CODES for plane in PLANES}
shuffle_position_counters = {hall_code: 1 for hall_code in HALL_CODES}
for item in gpu_servers:
    device = item["device"]
    hall_code = item["hall"]
    rack_code = item["rack_code"]
    for port_name in ("NIC0", "NIC1"):
        shuffle_name = f"gpu-leaf-shuffle-{hall_code}-{rack_code}-{device.name.split('-')[-1]}-{port_name.lower()}"
        shuffle_device = make_device(
            shuffle_name,
            dt_shuffle,
            role_shuffle,
            site_dc,
            shuffle_racks[hall_code],
            shuffle_position_counters[hall_code],
        )
        shuffle_position_counters[hall_code] += 1
        shuffle_devices.append(shuffle_device)

        rear_port = ensure_rear_port(shuffle_device, "bp1", type="mpo", positions=4)
        front_ports = {}
        for plane in PLANES:
            front_port = ensure_front_port(shuffle_device, f"fp{plane}", type="mpo", positions=1)
            front_ports[plane] = front_port
            PortMapping.objects.get_or_create(
                device=shuffle_device,
                front_port=front_port,
                front_port_position=1,
                rear_port=rear_port,
                rear_port_position=plane,
            )

        gpu_parent = Interface.objects.get(device=device, name=port_name)
        make_cable(gpu_parent, rear_port, breakout_profile=breakout_profile)

        for plane in PLANES:
            leaf = leaf_switches[(hall_code, plane)]
            leaf_port_number = leaf_port_counters[(hall_code, plane)]
            leaf_interface = Interface.objects.get(device=leaf, name=f"Eth1/{leaf_port_number}")
            make_cable(front_ports[plane], leaf_interface)
            leaf_port_counters[(hall_code, plane)] += 1
print(f"  Shuffle modules created: {len(shuffle_devices)}")

print("=== Phase 8.7: Leaf-spine passive shuffle modules ===")
leaf_spine_shuffle_devices = {}
for hall_index, hall_code in enumerate(HALL_CODES, start=1):
    hall_suffix = f"h{hall_code}"
    for plane in PLANES:
        shuffle_device = make_device(
            f"leaf-spine-shuffle-{hall_suffix}-p{plane}",
            dt_leaf_spine_shuffle,
            role_leaf_spine_shuffle,
            site_dc,
            leaf_spine_shuffle_racks[plane],
            hall_index,
        )
        leaf_spine_shuffle_devices[(hall_code, plane)] = shuffle_device

        rear_port = ensure_rear_port(shuffle_device, "bp1", type="mpo", positions=4)
        for channel in range(1, 5):
            front_port = ensure_front_port(shuffle_device, f"fp{channel}", type="mpo", positions=1)
            PortMapping.objects.get_or_create(
                device=shuffle_device,
                front_port=front_port,
                front_port_position=1,
                rear_port=rear_port,
                rear_port_position=channel,
            )
print(f"  Leaf-spine shuffle modules created: {len(leaf_spine_shuffle_devices)}")

print("=== Phase 9: Leaf-to-spine cabling ===")
cable_count = Cable.objects.count()
for hall_code in HALL_CODES:
    for plane in PLANES:
        leaf = leaf_switches[(hall_code, plane)]
        shuffle_device = leaf_spine_shuffle_devices[(hall_code, plane)]
        make_cable(
            Interface.objects.get(device=leaf, name="Eth1/49"),
            RearPort.objects.get(device=shuffle_device, name="bp1"),
            breakout_profile=breakout_profile,
        )
for hall_index, hall_code in enumerate(HALL_CODES, start=1):
    port_base = ((hall_index - 1) * 2) + 1
    for plane in PLANES:
        shuffle_device = leaf_spine_shuffle_devices[(hall_code, plane)]
        make_cable(
            FrontPort.objects.get(device=shuffle_device, name="fp1"),
            Interface.objects.get(device=spine_switches[(plane, "a")], name=f"Eth1/{port_base}"),
        )
        make_cable(
            FrontPort.objects.get(device=shuffle_device, name="fp2"),
            Interface.objects.get(device=spine_switches[(plane, "a")], name=f"Eth1/{port_base + 1}"),
        )
        make_cable(
            FrontPort.objects.get(device=shuffle_device, name="fp3"),
            Interface.objects.get(device=spine_switches[(plane, "b")], name=f"Eth1/{port_base}"),
        )
        make_cable(
            FrontPort.objects.get(device=shuffle_device, name="fp4"),
            Interface.objects.get(device=spine_switches[(plane, "b")], name=f"Eth1/{port_base + 1}"),
        )
print(f"  Total cables present after RoCE setup: {Cable.objects.count() - cable_count + 0}")

print("=== Phase 11-12: Plugin Fabric + Planes ===")
fabric, _ = Fabric.objects.get_or_create(
    name="ROCE-FABRIC-ALPHA",
    defaults={
        "description": "Multi-tenant disjoint-plane RoCEv2 fabric for DC Alpha",
        "expected_plane_count": 4,
        "tier_depth": 2,
        "disjointness_policy": "full",
        "scope_site": site_dc,
        "tier_role_map": {
            "roce-leaf-switch": 0,
            "roce-spine-switch": 1,
        },
    },
)
print(f"  Fabric pk={fabric.pk}")

for plane_num in PLANES:
    FabricPlane.objects.get_or_create(
        fabric=fabric,
        plane_number=plane_num,
        defaults={"description": f"RoCE Plane {plane_num}"},
    )
print(f"  FabricPlanes: {FabricPlane.objects.filter(fabric=fabric).count()}")

print()
print("=== Phases 1-12 complete. Ready for Phase 13: rebuild. ===")
print(
    "Summary: "
    f"{len(gpu_servers)} GPU servers, {len(shuffle_devices)} shuffle modules, "
    f"{len(leaf_spine_shuffle_devices)} leaf-spine shuffle modules, "
    f"{len(leaf_switches)} plane-dedicated leaves, {len(spine_switches)} plane-dedicated spines."
)
