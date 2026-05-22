# V2 Cutover Runbook

Last updated: 2026-05-22

## Preconditions

1. Branch includes the V2 registry, data model, workflow pages, and migrations.
2. Focused V2 tests pass for the areas being cut over.
3. Operator account can access plugin workflow pages.
4. NetBox has `netbox_plant_graph` enabled.
5. No `netbox_floorplan` plugin is required.

## Seed Architecture And Confirm Built-ins

Seed the persisted GB300 four-plane architecture fixture and its default stamp
template:

```bash
python manage.py mpf_seed_v2 --architecture-only
```

Expected persisted architecture:

- slug: `roce-4-plane-gb300-2x2-shuffle`
- version: `v2`
- planes: 4
- stamp template: `roce-4-plane-mini-proof`

The in-process blueprint registry also exposes the H100 direct-attach and GB300
eight-plane built-ins for V2.5 preview/apply and import preflight:

- `roce-4-plane-h100-direct-attach` `v2`
- `roce-8-plane-gb300-2x2-shuffle` `v2`

Validate in the UI:

```text
Multi-planar v2 -> Model Inventory -> Architectures
```

Open the architecture detail page and confirm the role definitions, transfer
patterns, allocation rules, channel-map matrix, and shuffle transform are
visible.

## Menu Smoke Check

The top-level menu should be `Multi-planar v2` with these groups:

1. `Operate`
2. `Build & Run`
3. `Audit`
4. `Model Inventory`

The expected primary operator pages are:

- Fabric Overview
- Interface Fanout Trace
- Path Query
- Physical Cable Blast Radius
- Onboard Fabric
- Operations Center
- Import Preview
- Impact Reports
- Audit Dashboard
- Audit Triage
- Exception Requests
- Fabrics
- Architectures
- Cable Assemblies
- Lane Inventory
- Model Catalog

## Functional Validation

1. Stamp or load a V2 fabric.
   - Confirm `Fabric`, `Plane`, `FabricNode`, `Endpoint`,
     `TransportChannel`, `ConnectorPosition`, `CableAssembly`, `FiberSegment`,
     `FiberStrand`, `StrandTermination`, `OpticalLane`, and `TransferMap`
     objects are present as expected.

2. Confirm source anchoring.
   - Open stamped `FabricNode` and `Endpoint` detail pages.
   - Verify `Source` fields anchor to the intended NetBox devices/interfaces.
   - Open a NetBox interface detail page and confirm the multiplanar context
     extension appears when the interface participates in the fabric.

3. Confirm cable independence.
   - Verify no NetBox-native cable rows were introduced for modeled-fabric
     semantics.
   - Verify fiber strands can resolve to plugin `CableAssembly` rows when
     cable assignment is known.

4. Validate Path Query.
   - Navigate to `Multi-planar v2 -> Operate -> Path Query`.
   - Select source/destination optical lanes.
   - Confirm the resolver returns path steps.
   - Confirm the visual path trace renders below the query results and SVG
     export is available.

5. Validate Interface Fanout Trace.
   - Navigate to `Multi-planar v2 -> Operate -> Interface Fanout Trace`.
   - Select a device and physical OSFP interface.
   - Confirm expanded mode shows all optical lanes.
   - Confirm consolidated mode groups by 200gbps transport channel.
   - Confirm shuffle-cassette transforms and cable assemblies are rendered.

6. Validate Physical Cable Blast Radius.
   - Navigate to `Multi-planar v2 -> Operate -> Physical Cable Blast Radius`.
   - Filter to a site/device and select an attached cable assembly.
   - Run cable cut/disconnect.
   - Run OSFP transceiver unseat for the same device/interface.
   - Confirm impacted endpoints/devices are grouped and drill-down links are
     present where paths can be resolved.
   - Save one modeled result as an impact report and confirm it appears under
     `Build & Run -> Impact Reports`.

7. Validate import preview and reports.
   - Navigate to `Multi-planar v2 -> Build & Run -> Import Preview`.
   - Dry-run a small import payload and confirm row outcomes, conflicts, and
     architecture gate details render.
   - Save the dry-run report, export JSON, and confirm replay/apply actions are
     confirmation-gated.

8. Validate stamp recovery workflow.
   - Open a saved `StampRun`.
   - Confirm V2.5 retry classification and rollback preview/apply controls are
     visible when the run status and manifest allow them.

9. Validate audit workflow.
   - Open Audit Dashboard and Audit Triage.
   - Confirm persisted `AuditEvent` records and suppression/exception lifecycle
     actions are visible.
   - Exercise an exception request only in a disposable or lab fabric unless
     production approval exists.

## API Validation

1. Confirm standard registry-backed API endpoints are present under the plugin
   API root.
2. Confirm operational REST endpoints are reachable for:
   - `path-query/`
   - `stamps/preview/`
   - `stamp-templates/<pk>/execute/`
   - `stamp-runs/<pk>/rollback/`
   - workflow finding lifecycle actions
   - disjointness exception lifecycle actions
   - `operation-runs/`
   - `impact/cable-assembly-cut/`
   - `impact/mpo-connector-unplug/`
   - `impact/osfp-transceiver-unseat/`
3. Confirm GraphQL returns `graphql_contract_version == "2.0.0"`.

## Rollback

1. Disable `netbox_plant_graph` in NetBox plugin configuration and restart
   NetBox.
2. Restore the previous plugin commit/tag.
3. Re-enable and restart.

V2 owns separate plugin tables and does not overwrite NetBox-native cable/path
state for modeled fabrics. Rollback risk is therefore concentrated in plugin
schema/data and any NetBox interface anchors created during stamping.

## Post-Cutover Monitoring

1. Review new `StampRun`, `OperationRun`, and `AuditEvent` entries for failures.
2. Spot-check path queries and fanout traces across stamped fabrics.
3. Run blast-radius checks for representative cable assemblies and OSFP
   endpoints.
4. Track operator-reported UX gaps for the next implementation slice.
