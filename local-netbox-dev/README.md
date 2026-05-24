# Local NetBox 4.2.3 Development Instance

Production-like local NetBox runtime with auth plugins intentionally omitted.

## Runtime

- NetBox base image: `netboxcommunity/netbox:v4.2.3-3.2.0`
- Plugins: `netbox_dns`, `netbox_prometheus_sd`, `netbox_plant_graph`,
  `netbox_power_plant`
- Auth: local username/password only
- URL: `http://localhost:8000`
- User: `admin`
- Password: `admin`

The local source directories for `netbox_multiplanar_fabrics` and
`netbox_power_plant` are installed editable in the image and bind-mounted at
runtime so ordinary Python/template edits are visible inside the containers.
The local image raises NGINX Unit's request body limit to 3 GiB so CAD/DWG
packages can be uploaded through the `netbox_power_plant` Madison underlay UI;
the local Django `DATA_UPLOAD_MAX_MEMORY_SIZE` is raised to the same ceiling, and
the image patches NetBox's Django settings to expose
`DATA_UPLOAD_MAX_NUMBER_FILES`, which is raised to 500 for direct multi-file CAD
selection. The plugin enforces its own CAD package limits after the request
reaches Django.

## Start

```bash
./scripts/up.sh
```

## Stop

```bash
./scripts/down.sh
```

## Optional Database Import

Place one prod/dev dump in `data/` before the first start, for example:

```bash
cp /path/to/YYYY_MM_DD_netbox_dump.sql.gz data/
./scripts/up.sh
```

Postgres imports files from `data/` only when the local database volume is new.
To force a fresh import:

```bash
./scripts/down.sh --volumes
./scripts/up.sh
```

## Madison Seed Data

The scripts below stage local-dev Madison data:

```bash
.venv/bin/python scripts/report_madison_workbook_manifest.py
.venv/bin/python scripts/report_madison_device_placements.py
docker compose exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < scripts/seed_madison_device_definitions.py
docker compose exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < scripts/seed_madison_workbook_rack_gaps.py
docker compose exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < scripts/seed_madison_row_id_tags.py
docker compose exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < scripts/seed_madison_rack_reconciliation.py
docker compose exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < scripts/seed_madison_devices_from_workbook.py
.venv/bin/python scripts/report_madison_shuffle_box_placements.py
docker compose exec -T -e MADISON_SHUFFLE_APPLY=1 netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < scripts/seed_madison_shuffle_containment_from_elevations.py
docker compose exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < scripts/sync_madison_power_ports_from_templates.py
docker compose exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < scripts/seed_madison_apdu11450me_pdus.py
docker compose exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < scripts/seed_madison_power_handoff_points.py
docker compose exec -T -e MADISON_POWER_HANDOFF_APPLY=1 netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < scripts/seed_madison_power_handoff_points.py
docker compose exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < scripts/seed_madison_conventional_pdu_power_cables.py
docker compose exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < scripts/seed_madison_nvl72_internal_busbars.py
.venv/bin/python scripts/report_madison_fiber_bom.py
docker compose exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell -c "script='/opt/netbox/netbox/scripts/seed_madison_fiber_bom_plan.py'; exec(open(script).read(), {'__name__': '__main__', '__file__': script})"
docker compose exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell -c "script='/opt/netbox/netbox/scripts/seed_madison_fabric_endpoint_units.py'; exec(open(script).read(), {'__name__': '__main__', '__file__': script})"
docker compose exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < scripts/report_madison_fiber_path_resolution.py
docker compose exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < scripts/seed_madison_fiber_paths_from_resolution.py
```

`report_madison_workbook_manifest.py` parses the workbook-authoritative `NC SU
Mapping` sheet and writes a JSON/CSV physical rack-slot manifest under
`data/generated/`. It is read-only with respect to NetBox and should be run
before any rack reconciliation or device instantiation.

`report_madison_device_placements.py` parses the workbook row-elevation sheets
and writes a JSON/CSV device/RU placement manifest under `data/generated/`. It
is read-only with respect to NetBox and is the source of truth for rack-unit
positions before device instantiation.

`seed_madison_device_definitions.py` creates or updates manufacturers, rack
roles, device roles, device types, and conservative component templates sourced
from the Madison layout workbook, RoCE shuffle Notion page, and fiber BOM. It
checks that the `Device` count is unchanged before it exits.

`seed_madison_workbook_rack_gaps.py` adds the ten workbook-authoritative BE leaf
racks present in `NC SU Mapping` but absent from the imported electrical
rack/circuit model: `B11`, `B12`, `F11`, `F12`, `Q11`, `Q12`, `R11`, `R12`,
`V11`, and `V12`. These racks are intentionally left without electrical delivery
points until the updated electrical design accounts for them.

`seed_madison_row_id_tags.py` leaves rack names untouched and applies one
`nscale-row-id-*` tag to each rack so workbook row/slot coordinates such as
`Q1`, `A13`, and `M4` remain available as a modeling key.

`seed_madison_rack_reconciliation.py` uses those row-id tags as the workbook
join key and reconciles tenant `nscale`, rack role, 48U height, planned status,
and `nv_su_*` tags. It intentionally does not rename racks or alter rack
delivery points.

`seed_madison_devices_from_workbook.py` uses the generated row-elevation
placement manifest to instantiate MAD-1 devices in planned state under tenant
`nscale`. It skips the workbook rows marked `(Moved to MMR) Nscale OOB Edge #1`.
NetBox-native rack positions are applied to non-zero-U devices. Workbook shuffle
labels are initially staged as placeholder cassette devices and are later
converted into 1RU shuffle boxes by the elevation migration.

`report_madison_shuffle_box_placements.py` extracts the physical shuffle-box
placements from the row-elevation labels. A label such as
`14 2x (2x2)Shuffle 8-NIC3A` is treated as one physical 1RU shuffle box with
14 populated cassette positions, logical shuffle-box number `8`, NIC group `3`,
and side `A`.

`seed_madison_shuffle_containment_from_elevations.py` applies the physical
shuffle containment from the elevation manifest: each workbook shuffle label
becomes a racked 1RU shuffle box with the populated cassette devices parented
under the box. The script is dry-run by default and applies only with
`MADISON_SHUFFLE_APPLY=1`; rerun `seed_madison_fabric_endpoint_units.py` after
changing shuffle containment.

`sync_madison_power_ports_from_templates.py` creates or updates concrete
NetBox power ports on the instantiated MAD-1 devices from their device-type
templates. Passive fiber panels and shuffle cassettes are intentionally skipped.

`seed_madison_apdu11450me_pdus.py` creates APDU11450ME rack-mounted PDU pairs
for the conventional non-NVL72 racks only. These are modeled as 0U vertical rack
devices, with one 560P6/IEC 60309 60A 3P+N+PE input, 21 C13/C15 outlets,
21 C13/C15/C19/C21 combination outlets, and one 1000BASE-T management interface
per PDU. If non-NVL72 `PowerHandoffPoint` rows already exist, they are rebound
from rack-level targets to the corresponding PDU input ports.

`seed_madison_power_handoff_points.py` reconciles drawing-derived cabinet
circuit endpoints to NetBox-native power ports. It maps electrical rack IDs such
as `GB300-P1-R1-C16` to workbook rack slots, binds conventional `CKT1/CKT2`
handoffs to APDU A/B `input` ports, and binds NVL72 `CKT1..CKT8` handoffs to
PS33 `facility-input` ports ordered by rack RU. It is dry-run by default; set
`MADISON_POWER_HANDOFF_APPLY=1` to create/update `PowerHandoffPoint` rows.

`seed_madison_conventional_pdu_power_cables.py` creates planned NetBox power
cables from conventional-rack APDU11450ME outlets to downstream device power
ports. It reserves combo outlets for C20/C22 loads first and validates that every
powered device in those racks is split across the A and B PDUs.

`seed_madison_nvl72_internal_busbars.py` creates one shared internal `NVL72`
busbar per NVL72 rack. Each busbar attaches the eight power-shelf
`busbar-output-1` ports as source attachments and the GB300 compute-tray,
NVLink-switch, and `SN2201_M` busbar ports as load attachments.

`report_madison_fiber_bom.py` parses the Madison fiber BOM workbook selected
from `data/source/` or `MADISON_FIBER_BOM_WORKBOOK` and writes a normalized
Madison fiber BOM manifest under `data/generated/`. The
manifest separates BOM lots for node-to-shuffle, shuffle-to-leaf,
shuffle-to-spine, node-to-leaf, leaf-to-spine, and material alignment rows.

`seed_madison_fiber_bom_plan.py` stages the normalized BOM in the
`netbox_plant_graph` plugin planning layer. It creates the `GS001 RoCE Fabric`,
four fabric planes, reusable MPO8 assembly/breakout templates for 96f, 72f,
64f, and single-MPO patch assemblies, plus one planned `FabricNode` BOM lot per
manifest row. Native NetBox `Cable` objects are intentionally avoided for
modeled RoCE fabric paths.

`seed_madison_fabric_endpoint_units.py` creates the plugin graph endpoint
surface needed for fiber worksheet resolution. It creates `FabricNode`,
`Endpoint`, `ConnectorPosition`, `OpticalLane`, and cassette `TransferMap`
objects for OSFP-bearing active devices and passive MPO cassette devices.

`report_madison_fiber_path_resolution.py` resolves the corrected one-SU fiber
worksheet pattern against instantiated MAD-1 GB300 trays, BE leaf switches,
plugin graph endpoints, and candidate shuffle boxes. The generated CSV
records which rows are fully resolvable and which rows are blocked by the
remaining worksheet-to-elevation shuffle-box crosswalk ambiguity.

`seed_madison_fiber_paths_from_resolution.py` creates plugin-native
`CableAssembly`, `FiberSegment`, `FiberStrand`, and `StrandTermination` rows for
the unambiguous fiber worksheet paths only. It currently stamps GB300-to-shuffle
and shuffle-to-leaf MPO8 paths for rows whose worksheet shuffle-box number maps
to exactly one physical shuffle box.
