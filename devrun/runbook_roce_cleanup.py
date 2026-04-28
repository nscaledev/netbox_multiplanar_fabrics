#!/usr/bin/env python3
"""
Remove the objects created by docs/runbook-roce-fabric-modeling.md from a
local NetBox dev instance.

Defaults to dry-run. Pass --execute to delete.
"""

import argparse
import json
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("NETBOX_PLANT_GRAPH_ENABLE", "1")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "netbox.settings")

import django

django.setup()

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from dcim.models import Cable, CableTermination, Device, DeviceRole, DeviceType, Interface, Location, Manufacturer, Rack, Site
from tenancy.models import Tenant, TenantGroup

from netbox_plant_graph.breakout_profiles import get_breakout_profile_field_name
from netbox_plant_graph.models import (
    AttachmentUnit,
    AuditFinding,
    AuditRun,
    BreakoutProfile,
    CoarseEdge,
    DisjointnessException,
    Fabric,
    FabricPlane,
    FineEdge,
    GraphBuildRun,
    PlaneMembership,
    PlantNode,
    SignalLane,
    TerminationPoint,
    UnresolvedStateObservation,
    UnresolvedStateSummary,
)


RUNBOOK_SITE_SLUG = "dc-alpha"
RUNBOOK_FABRIC_NAME = "ROCE-FABRIC-ALPHA"
RUNBOOK_BREAKOUT_PROFILE_SLUG = "800g-4x200g"
RUNBOOK_TENANT_GROUP_NAME = "GPU Workload Tenants"
RUNBOOK_TENANT_SLUGS = ("tenant-alpha", "tenant-beta")
RUNBOOK_DEVICE_ROLE_SLUGS = (
    "gpu-server",
    "gpu-leaf-shuffle-board",
    "leaf-spine-shuffle-board",
    "roce-leaf-switch",
    "roce-spine-switch",
    "frontside-leaf-switch",
    "frontside-spine-switch",
    "management-switch",
    "edge-router",
)
RUNBOOK_DEVICE_TYPE_SLUGS = (
    "gpu-server-8x800g",
    "gpu-leaf-shuffle-1x4",
    "leaf-spine-shuffle-1x4",
    "roce-leaf-200g-plane",
    "roce-spine-200g-plane",
    "roce-leaf-64x800g",
    "roce-spine-128x800g",
    "generic-1u",
)
RUNBOOK_MANUFACTURER_SLUG = "generic"


def _site_interface_ids(site):
    if site is None:
        return []
    return list(Interface.objects.filter(device__site=site).values_list("pk", flat=True))


def _site_cable_ids(site):
    if site is None:
        return []
    interface_ids = _site_interface_ids(site)
    if not interface_ids:
        return []
    interface_ct = ContentType.objects.get_for_model(Interface)
    return sorted(
        set(
        CableTermination.objects.filter(
            termination_type=interface_ct,
            termination_id__in=interface_ids,
        )
        .values_list("cable_id", flat=True)
        )
    )


def collect_summary():
    site = Site.objects.filter(slug=RUNBOOK_SITE_SLUG).first()
    fabric = Fabric.objects.filter(name=RUNBOOK_FABRIC_NAME).first()
    breakout_profile = BreakoutProfile.objects.filter(slug=RUNBOOK_BREAKOUT_PROFILE_SLUG).first()
    manufacturer = Manufacturer.objects.filter(slug=RUNBOOK_MANUFACTURER_SLUG).first()
    breakout_profile_field_name = get_breakout_profile_field_name()

    return {
        "site": {
            "slug": RUNBOOK_SITE_SLUG,
            "present": site is not None,
            "locations": 0 if site is None else site.locations.count(),
            "racks": 0 if site is None else site.racks.count(),
            "devices": 0 if site is None else Device.objects.filter(site=site).count(),
            "interfaces": 0 if site is None else Interface.objects.filter(device__site=site).count(),
            "cables": len(_site_cable_ids(site)),
        },
        "fabric": {
            "name": RUNBOOK_FABRIC_NAME,
            "present": fabric is not None,
            "planes": 0 if fabric is None else FabricPlane.objects.filter(fabric=fabric).count(),
            "graph_build_runs": 0 if fabric is None else GraphBuildRun.objects.filter(fabric=fabric).count(),
            "plant_nodes": 0 if fabric is None else PlantNode.objects.filter(fabric=fabric).count(),
            "termination_points": 0 if fabric is None else TerminationPoint.objects.filter(plant_node__fabric=fabric).count(),
            "attachment_units": 0 if fabric is None else AttachmentUnit.objects.filter(
                termination_point__plant_node__fabric=fabric
            ).count(),
            "coarse_edges": 0 if fabric is None else CoarseEdge.objects.filter(a_tp__plant_node__fabric=fabric).count(),
            "fine_edges": 0 if fabric is None else FineEdge.objects.filter(parent_coarse_edge__a_tp__plant_node__fabric=fabric).count(),
            "signal_lanes": 0 if fabric is None else SignalLane.objects.filter(
                attachment_unit__termination_point__plant_node__fabric=fabric
            ).count(),
            "plane_memberships": 0 if fabric is None else PlaneMembership.objects.filter(plane__fabric=fabric).count(),
            "audit_runs": 0 if fabric is None else AuditRun.objects.filter(fabric=fabric).count(),
            "audit_findings": 0 if fabric is None else AuditFinding.objects.filter(fabric=fabric).count(),
            "disjointness_exceptions": 0 if fabric is None else DisjointnessException.objects.filter(fabric=fabric).count(),
            "unresolved_summaries": 0 if fabric is None else UnresolvedStateSummary.objects.filter(fabric=fabric).count(),
            "unresolved_observations": 0 if fabric is None else UnresolvedStateObservation.objects.filter(summary__fabric=fabric).count(),
        },
        "shared_primitives": {
            "tenants": list(Tenant.objects.filter(slug__in=RUNBOOK_TENANT_SLUGS).values_list("slug", flat=True)),
            "tenant_groups": list(
                TenantGroup.objects.filter(name=RUNBOOK_TENANT_GROUP_NAME).values_list("name", flat=True)
            ),
            "device_roles": list(
                DeviceRole.objects.filter(slug__in=RUNBOOK_DEVICE_ROLE_SLUGS).values_list("slug", flat=True)
            ),
            "device_types": list(
                DeviceType.objects.filter(slug__in=RUNBOOK_DEVICE_TYPE_SLUGS).values_list("slug", flat=True)
            ),
            "breakout_profiles": [] if breakout_profile is None else [breakout_profile.slug],
            "manufacturer_present": manufacturer is not None,
            "manufacturer_device_types": 0 if manufacturer is None else DeviceType.objects.filter(
                manufacturer=manufacturer
            ).count(),
        },
        "plugin_infrastructure": {
            "fabric_plane_custom_field_present": bool(
                django.apps.apps.get_model("extras", "CustomField").objects.filter(name="fabric_plane").exists()
            ),
            "breakout_profile_custom_field_present": bool(
                django.apps.apps.get_model("extras", "CustomField").objects.filter(name=breakout_profile_field_name).exists()
            ),
        },
    }


def print_summary(label, summary):
    print(f"=== {label} ===")
    print(json.dumps(summary, indent=2, sort_keys=True))
    print()


def log(message):
    print(message, flush=True)


def delete_runbook_objects():
    site = Site.objects.filter(slug=RUNBOOK_SITE_SLUG).first()
    fabric = Fabric.objects.filter(name=RUNBOOK_FABRIC_NAME).first()

    log(
        "Cleanup runs inside one database transaction. "
        "Other sessions may still see the runbook objects until the final commit completes."
    )
    with transaction.atomic():
        if fabric is not None:
            log(f"Deleting Fabric {fabric.name} and all derived plugin graph objects")
            fabric.delete()

        cable_ids = _site_cable_ids(site)
        if cable_ids:
            log(f"Deleting {len(cable_ids)} cables attached to site {RUNBOOK_SITE_SLUG}")
            Cable.objects.filter(pk__in=cable_ids).delete()

        if site is not None:
            device_count = Device.objects.filter(site=site).count()
            if device_count:
                log(f"Deleting {device_count} devices in site {site.slug}")
                Device.objects.filter(site=site).delete()

            rack_count = Rack.objects.filter(site=site).count()
            if rack_count:
                log(f"Deleting {rack_count} racks in site {site.slug}")
                Rack.objects.filter(site=site).delete()

            location_qs = Location.objects.filter(site=site).order_by("-parent_id", "-pk")
            location_count = location_qs.count()
            if location_count:
                log(f"Deleting {location_count} locations in site {site.slug}")
                for location in location_qs:
                    location.delete()

            log(f"Deleting Site {site.slug}")
            site.delete()

        tenant_group_qs = TenantGroup.objects.filter(name=RUNBOOK_TENANT_GROUP_NAME)
        if tenant_group_qs.exists():
            log(f"Deleting tenant group {RUNBOOK_TENANT_GROUP_NAME}")
            tenant_group_qs.delete()

        breakout_profile_qs = BreakoutProfile.objects.filter(slug=RUNBOOK_BREAKOUT_PROFILE_SLUG)
        breakout_profile = breakout_profile_qs.first()
        if breakout_profile_qs.exists() and not Cable.objects.filter(
            **{f"custom_field_data__{get_breakout_profile_field_name()}": breakout_profile.pk}
        ).exists():
            log(f"Deleting BreakoutProfile {RUNBOOK_BREAKOUT_PROFILE_SLUG}")
            breakout_profile_qs.delete()

        for tenant_slug in RUNBOOK_TENANT_SLUGS:
            tenant = Tenant.objects.filter(slug=tenant_slug).first()
            if tenant is None:
                continue
            has_refs = (
                Device.objects.filter(tenant=tenant).exists()
                or django.apps.apps.get_model("dcim", "Rack").objects.filter(tenant=tenant).exists()
                or Fabric.objects.filter(tenant=tenant).exists()
            )
            if not has_refs:
                log(f"Deleting tenant {tenant.slug}")
                tenant.delete()

        for role_slug in RUNBOOK_DEVICE_ROLE_SLUGS:
            role = DeviceRole.objects.filter(slug=role_slug).first()
            if role is None:
                continue
            if not Device.objects.filter(role=role).exists():
                log(f"Deleting device role {role.slug}")
                role.delete()

        for device_type_slug in RUNBOOK_DEVICE_TYPE_SLUGS:
            device_type = DeviceType.objects.filter(slug=device_type_slug).first()
            if device_type is None:
                continue
            if not Device.objects.filter(device_type=device_type).exists():
                log(f"Deleting device type {device_type.slug}")
                device_type.delete()

        manufacturer = Manufacturer.objects.filter(slug=RUNBOOK_MANUFACTURER_SLUG).first()
        if manufacturer is not None and not DeviceType.objects.filter(manufacturer=manufacturer).exists():
            log(f"Deleting manufacturer {manufacturer.slug}")
            manufacturer.delete()


def validate_cleanup(summary):
    failures = []
    if summary["site"]["present"]:
        failures.append("site still present")
    if summary["fabric"]["present"]:
        failures.append("fabric still present")
    if summary["shared_primitives"]["tenants"]:
        failures.append("tenant records still present")
    if summary["shared_primitives"]["tenant_groups"]:
        failures.append("tenant group still present")
    if summary["shared_primitives"]["device_roles"]:
        failures.append("device roles still present")
    if summary["shared_primitives"]["device_types"]:
        failures.append("device types still present")
    if summary["shared_primitives"]["breakout_profiles"]:
        failures.append("breakout profile still present")
    if failures:
        raise SystemExit("Cleanup validation failed: " + "; ".join(failures))


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Delete the runbook dataset. Without this flag the script only reports what it would remove.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    before = collect_summary()
    print_summary("Runbook RoCE cleanup summary (before)", before)

    if not args.execute:
        log("Dry run only. Re-run with --execute to delete the runbook dataset.")
        return

    log("Executing runbook RoCE cleanup.")
    delete_runbook_objects()
    after = collect_summary()
    print_summary("Runbook RoCE cleanup summary (after)", after)
    validate_cleanup(after)
    log("Cleanup validation succeeded.")


if __name__ == "__main__":
    main()
