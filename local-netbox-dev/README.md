# Local NetBox 4.2.3 Development Instance

Production-like local NetBox runtime with auth plugins intentionally omitted.

## Runtime

- NetBox base image: `netboxcommunity/netbox:v4.2.3-3.2.0`
- Plugins: `netbox_dns`, `netbox_prometheus_sd`, `netbox_floorplan==0.6.0`,
  `netbox_plant_graph`, `netbox_power_plant`
- Auth: local username/password only
- URL: `http://localhost:8000`
- User: `admin`
- Password: `admin`

The local source directories for `netbox_multiplanar_fabrics` and
`netbox_power_plant` are installed editable in the image and bind-mounted at
runtime so ordinary Python/template edits are visible inside the containers.

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
docker compose exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < scripts/migrate_madison_shuffle_boxes_from_elevations_fast.py
docker compose exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < scripts/sync_madison_power_ports_from_templates.py
docker compose exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < scripts/bind_madison_gb300_power_shelf_delivery_points.py
docker compose exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < scripts/seed_madison_apdu11450me_pdus.py
docker compose exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < scripts/seed_madison_conventional_pdu_power_cables.py
docker compose exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < scripts/seed_madison_nvl72_internal_busbars.py
.venv/bin/python scripts/report_madison_fiber_bom.py
docker compose exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell -c "script='/opt/netbox/netbox/scripts/seed_madison_fiber_bom_plan.py'; exec(open(script).read(), {'__name__': '__main__', '__file__': script})"
docker compose exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell -c "script='/opt/netbox/netbox/scripts/seed_madison_fabric_endpoint_units.py'; exec(open(script).read(), {'__name__': '__main__', '__file__': script})"
docker compose exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < scripts/seed_madison_shuffle_full_cassette_capacity.py
docker compose exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell -c "script='/opt/netbox/netbox/scripts/seed_madison_fabric_endpoint_units.py'; exec(open(script).read(), {'__name__': '__main__', '__file__': script})"
docker compose exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < scripts/report_madison_fiber_path_resolution.py
docker compose exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < scripts/seed_madison_fiber_paths_from_resolution.py
docker compose exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < scripts/seed_power_floorplans.py
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

`migrate_madison_shuffle_boxes_from_elevations_fast.py` corrects the physical
shuffle hierarchy from the elevation manifest: each workbook shuffle label
becomes a racked 1RU shuffle box, every box gets three tray bays, every tray gets
six cassette bays, and populated cassette devices with four front MPOs and four
rear MPOs are created under those trays. It also removes the stale
one-box-per-rack hierarchy and deletes plant-graph endpoint objects tied to the placeholder
shuffle cassette devices; rerun `seed_madison_fabric_endpoint_units.py`
afterward.

`sync_madison_power_ports_from_templates.py` creates or updates concrete
NetBox power ports on the instantiated MAD-1 devices from their device-type
templates. Passive fiber panels and shuffle cassettes are intentionally skipped.

`bind_madison_gb300_power_shelf_delivery_points.py` binds GB300 rack delivery
points to the `facility-input` power ports on the eight GB300 power shelves in
each NVL72/GB300 rack. It requires an exact 8:8 delivery-point-to-power-shelf
match per rack and maps `CKT1..CKT8` to power shelves ordered by rack RU.

`seed_madison_apdu11450me_pdus.py` creates APDU11450ME rack-mounted PDU pairs
for the conventional non-NVL72 racks only. These are modeled as 0U vertical rack
devices, with one 560P6/IEC 60309 60A 3P+N+PE input, 21 C13/C15 outlets,
21 C13/C15/C19/C21 combination outlets, and one 1000BASE-T management interface
per PDU. Existing non-NVL72 rack delivery points are rebound from rack-level
targets to the corresponding PDU input ports.

`seed_madison_conventional_pdu_power_cables.py` creates planned NetBox power
cables from conventional-rack APDU11450ME outlets to downstream device power
ports. It reserves combo outlets for C20/C22 loads first and validates that every
powered device in those racks is split across the A and B PDUs.

`seed_madison_nvl72_internal_busbars.py` creates one shared internal `NVL72`
busbar per NVL72 rack. Each busbar attaches the eight power-shelf
`facility-input` ports as source attachments and the GB300 compute-tray plus
NVLink-switch `nvl72-busbar` ports as load attachments.

`report_madison_fiber_bom.py` parses `Nscale NC 18k Fiber BOM v1.4.xlsx` and
writes a normalized Madison fiber BOM manifest under `data/generated/`. The
manifest separates BOM lots for node-to-shuffle, shuffle-to-leaf,
shuffle-to-spine, node-to-leaf, leaf-to-spine, and material alignment rows.

`seed_madison_fiber_bom_plan.py` stages the normalized BOM in the
`netbox_plant_graph` plugin planning layer. It creates the `MAD-1 RoCE Fabric`,
four fabric planes, reusable MPO8 assembly/breakout templates for 96f, 72f,
64f, and single-MPO patch assemblies, plus one planned PlantNode BOM lot per
manifest row. Native NetBox `Cable` objects are intentionally deferred until
exact per-device/per-cassette terminations are resolved.

`seed_madison_fabric_endpoint_units.py` creates the plugin graph endpoint
surface needed for fiber worksheet resolution. It creates PlantNodes for MAD-1
OSFP-bearing active devices and passive MPO devices, TerminationPoints for OSFP
interfaces plus cassette/panel front/rear MPO ports, and AttachmentUnits that
expand each 800G OSFP into two MPO logical slices while preserving each passive
MPO as one passive group.

`seed_madison_shuffle_full_cassette_capacity.py` fills every empty cassette bay
under the Madison shuffle boxes so all 18 physical cassette positions are
addressable. The corrected fiber worksheet references the full 18-position box
capacity, even where row-elevation labels carry smaller leading numbers.

`report_madison_fiber_path_resolution.py` resolves the corrected one-SU fiber
worksheet pattern against instantiated MAD-1 GB300 trays, BE leaf switches,
Plant Graph attachment units, and candidate shuffle boxes. The generated CSV
records which rows are fully resolvable and which rows are blocked by the
remaining worksheet-to-elevation shuffle-box crosswalk ambiguity.

`seed_madison_fiber_paths_from_resolution.py` creates Plant Graph `CoarseEdge`
and `FineEdge` rows for the unambiguous fiber worksheet paths only. It currently
stamps GB300-to-shuffle and shuffle-to-leaf MPO8 graph edges for rows whose
worksheet shuffle-box number maps to exactly one physical shuffle box.
