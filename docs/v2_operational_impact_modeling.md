# V2 Operational Impact Modeling

The operational impact modeling service provides a structured core for the
Physical Cable Blast Radius UI and future APIs. It models common physical
failure scenarios against the plugin-native V2 topology graph and returns
operator-ready objects instead of display-only strings.

## Scenarios

The service supports:

- `cable_assembly_cut`: a cable assembly is cut, disconnected, or otherwise
  unavailable. Member `FiberStrand` rows become failed components.
- `mpo_connector_unplug`: one or more MPO connector endpoints are unplugged.
  All `ConnectorPosition` rows on those endpoints become unavailable.
- `osfp_transceiver_unseat`: one or more OSFP-facing NetBox `Interface`
  objects are selected as the operator handle for an unseated transceiver. The
  modeled failure includes the plugin endpoint anchored to that interface, child
  MPO endpoints, mapped transport channels, and any bound
  `TransceiverConnector` rows for the installed NetBox module.

Entry points:

```python
from netbox_plant_graph.services.impact_modeling import (
    compare_operational_impact_reports,
    model_cable_assembly_cut_impact,
    model_mpo_connector_unplug_impact,
    model_operational_impact,
    model_osfp_transceiver_unseat_impact,
    persist_operational_impact_report,
)
```

Each function accepts an optional `selected_fabric` to constrain the result to a
single fabric.

## Report Shape

`OperationalImpactReport.as_dict()` returns:

- `scenario`: scenario type, label, target object references, selected fabric,
  and scenario-specific IDs.
- `scope`: fabric IDs and slugs covered by the simulated components.
- `summary`: impacted path/lane/channel/endpoint/device counts and counts by
  severity.
- `simulated_components`: failed physical or modeled-fabric objects such as
  cable assemblies, fiber strands, connector positions, endpoints, interfaces,
  or transceiver connector faces.
- `impacted_paths`: resolved path hooks with `source_lane_id`,
  `destination_lane_id`, `source_channel_id`, `destination_channel_id`, matched
  failed components, and path step references.
- `impacted_lanes`, `impacted_channels`, `impacted_endpoints`,
  `impacted_devices`: grouped object impacts with stable IDs, related objects,
  remediation text, and path/lane/channel/endpoint ID sets.
- `hierarchy`: site -> rack -> device/plugin-node -> endpoint -> channel ->
  lane tree for UI rendering.

Object references include `model`, `id`, `label`, and `url` when the object has
a resolvable NetBox URL.

## REST Impact Preview API

Automation can request non-mutating impact previews through scenario-specific
REST endpoints:

- `POST /api/plugins/plant-graph/impact/cable-assembly-cut/`
- `POST /api/plugins/plant-graph/impact/mpo-connector-unplug/`
- `POST /api/plugins/plant-graph/impact/osfp-transceiver-unseat/`

All three endpoints return the `OperationalImpactReport.as_dict()` shape
directly. The stable top-level keys are:

```text
scenario
scope
summary
simulated_components
impacted_paths
impacted_lanes
impacted_channels
impacted_endpoints
impacted_devices
hierarchy
```

Common request fields:

- `fabric`: optional fabric ID used as `selected_fabric`.
- `max_depth`: optional path resolver depth, default `64`, maximum `256`.

Scenario target fields:

- Cable cut: `cable_assembly_id` or `cable_assembly_ids`.
- MPO unplug: `connector_endpoint_id` or `connector_endpoint_ids`.
- OSFP unseat: `interface_id` or `interface_ids`. This is currently an
  interface-based selector for operator ergonomics; when a NetBox `Module` is
  installed in a matching module bay, the report also includes the installed
  module's plugin `TransceiverConnector` faces as failed components.

Examples:

```bash
curl -sS -X POST \
  -H "Authorization: Token $NETBOX_TOKEN" \
  -H "Content-Type: application/json" \
  "$NETBOX_URL/api/plugins/plant-graph/impact/cable-assembly-cut/" \
  --data '{"cable_assembly_id": 123, "fabric": 45}'
```

```bash
curl -sS -X POST \
  -H "Authorization: Token $NETBOX_TOKEN" \
  -H "Content-Type: application/json" \
  "$NETBOX_URL/api/plugins/plant-graph/impact/mpo-connector-unplug/" \
  --data '{"connector_endpoint_id": 678, "fabric": 45}'
```

```bash
curl -sS -X POST \
  -H "Authorization: Token $NETBOX_TOKEN" \
  -H "Content-Type: application/json" \
  "$NETBOX_URL/api/plugins/plant-graph/impact/osfp-transceiver-unseat/" \
  --data '{"interface_id": 901, "fabric": 45}'
```

Validation failures return HTTP `400` with field-keyed messages. Missing target
fields, unknown target IDs, endpoints without connector positions, interfaces
without modeled OSFP endpoints, and unknown fabric IDs are rejected before the
impact model runs. A missing installed module or missing transceiver profile
does not block the OSFP-unseat preview; it simply means the report will be
driven by the interface/plugin-endpoint graph rather than richer transceiver
connector bindings.

## Saved Snapshots

`persist_operational_impact_report(report, report_name=..., actor=...,
parameters=...)` stores an immutable JSON snapshot in an `OperationRun` row. It
does not require a migration or mutate topology state.

The persisted run uses:

- `profile`: `operational_impact`
- `status`: `completed`
- `parameters`: operation kind, report name, scenario type, target object refs,
  scope, and caller-supplied parameters.
- `result`: report schema, report name, scenario type, target object refs,
  summary, scope, stable report hash, persisted timestamp, and the full
  `OperationalImpactReport.as_dict()` payload.
- `metadata`: operation kind, report schema, report hash, scenario type, target
  object count, impacted device count, and impacted path count.

If an existing `OperationRun` or ID is supplied through `operation_run`, the
helper updates that row in the same style as topology integrity persistence.
When the report scope contains exactly one fabric, the run is linked to that
fabric; multi-fabric reports remain global.

The `Impact Reports` UI lists saved operational impact `OperationRun` snapshots,
links back to the run detail, exposes a JSON export endpoint for each report,
and can compare two saved reports using
`compare_operational_impact_reports(...)`. The first-pass comparison surface
shows report counts, common impacts, severity deltas, and device deltas without
mutating topology state.

## Scenario Comparison

`compare_operational_impact_reports(report_a, report_b, ...)` accepts two or
more `OperationalImpactReport` objects, raw `as_dict()` payloads, or persisted
run result dictionaries containing a nested `report`. It returns an
`OperationalImpactComparison` object with `as_dict()`.

The comparison payload contains:

- `reports`: deterministic scenario labels, target refs, scope, and summaries.
- `common_impacts`: simulated components, paths, lanes, channels, endpoints, and
  devices present in every report, with per-report severities.
- `scenario_specific_impacts`: impacts that appear in only one report.
- `severity_deltas`: count deltas from report 0 to each later report, plus
  per-impact severity rank changes where the same impact changes tier.
- `device_deltas`: added, removed, common, and severity-changed impacted devices
  from report 0 to each later report.

The comparison is deterministic and JSON-serializable, and it does not mutate
topology.

## Severity Tiers

- `failed` / `FAILED`: directly unavailable component, lane, channel, endpoint,
  interface, or device-facing grouping.
- `degraded` / `DEGRADED`: not directly removed, but an end-to-end path crosses
  the simulated failure.
- `at_risk` / `AT_RISK`: parent device or plugin node contains impacted
  endpoints and should be inspected in the operator workflow.
- `low` / `LOW`: site/rack grouping context for downstream impact.

The tiers intentionally distinguish physical failure from logical reachability
impact. For example, a cable cut can make the source-side lane `FAILED` while
the far-side lane is `DEGRADED` because its transceiver is still seated but its
resolved path is broken.

## Path And Drilldown Hooks

Impact paths carry lane/channel identifiers and path steps so UI/API consumers
can link to existing path resolver and lane drilldown workflows without parsing
messages. Path IDs are deterministic within a report:

```text
path:<source_lane_id>:<destination_lane_id-or-unresolved>
```

Matched components identify why the path is impacted, such as the failed
`FiberStrand` for cable cuts, failed `ConnectorPosition` rows for MPO unplug
scenarios, or failed `TransceiverConnector`/interface/endpoint objects for OSFP
unseat scenarios.

## Intended Use

Use this service before maintenance or incident response to answer:

- Which optical lanes and transport channels are directly failed?
- Which peer lanes and endpoints become degraded through resolved paths?
- Which device/interface hierarchy should an operator inspect?
- Which modeled components explain the impact?

The service does not mutate topology state and does not replace integrity
audits. Run topology integrity checks first when imported or bulk-edited data may
be inconsistent.

## Current UI Surfaces

The `Physical Cable Blast Radius` page is the primary operator workflow. It
lets operators filter by site, device type, role, rack label, rack row, rack
elevation, and partial device name; select a modeled device; choose the failure
scenario; select one or more cable assemblies, connector endpoints, or OSFP
interfaces; calculate impact; and save the result as an impact report. For OSFP
unseat scenarios, the selector remains the NetBox interface today, but the
result includes bound transceiver connector faces whenever the selected
interface has a matching module bay/module/profile binding.

The `Impact Reports` page lists saved operational-impact `OperationRun`
snapshots, exports each report as JSON, links back to the run detail, and
compares two saved reports without mutating topology state.

## Remaining Polish

The impact model is operational. The remaining high-value polish is mostly
presentation depth:

- richer saved-report detail pages with expandable hierarchy and path drilldown
  links,
- side-by-side scenario comparison affordances directly from Blast Radius after
  multiple calculations,
- a module-native OSFP unseat selector for operators who want to select the
  installed transceiver object directly instead of starting from the interface,
- clearer grouping by fabric tier as additional blueprint families introduce
  spine/super-spine roles.
