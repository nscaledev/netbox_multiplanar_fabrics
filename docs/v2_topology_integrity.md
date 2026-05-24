# V2 Topology Integrity Audit

The V2 topology integrity audit checks plugin-native modeled fabric data before
operators trust path resolution, blast-radius output, or channel coverage
reports. It is intentionally separate from model `clean()` validation because
bulk imports, scripts, and direct updates can save rows that bypass form/model
validation.

## Operator Use

Run the audit globally:

```bash
python manage.py mpf_audit_integrity
```

Run it for one fabric by ID or slug:

```bash
python manage.py mpf_audit_integrity --fabric gs001-roce-fabric
python manage.py mpf_audit_integrity --fabric 12
```

Emit JSON for automation:

```bash
python manage.py mpf_audit_integrity --fabric gs001-roce-fabric --format json
```

Persist the audit as an immutable `OperationRun` snapshot:

```bash
python manage.py mpf_audit_integrity --fabric gs001-roce-fabric --persist-operation-run
```

Update an existing `OperationRun` row instead of creating a new one:

```bash
python manage.py mpf_audit_integrity --fabric gs001-roce-fabric --operation-run-id 123
```

Fail a script when findings meet a threshold:

```bash
python manage.py mpf_audit_integrity --fabric gs001-roce-fabric --fail-on error
```

## Report Shape

The service returns a structured report with:

- `scope`: global or fabric-scoped audit target.
- `ok`: true when there are no `critical` or `error` findings.
- `summary`: total findings by severity.
- `grouped_summary`: finding counts grouped by object family, with workflow
  flags, code counts, and `path_blocking_count`.
- `checked`: counts of V2 topology objects inspected.
- `catalog_version`: version of the topology integrity finding catalog used by
  this report.
- `finding_catalog`: full finding-code catalog keyed by code.
- `findings`: severity, code, message, primary object, related objects,
  remediation hint, and check-specific details.

Finding objects use NetBox model labels such as
`netbox_plant_graph.opticallane` plus the object ID and display label.
Finding objects also include additive catalog metadata:

- `object_family` and `object_family_label`.
- `affected_workflows` and boolean `workflow_flags`.
- `path_blocking`: true when the finding makes path tracing unsafe to trust.
- `catalog`: the finding-code catalog entry used for the finding.

The original top-level JSON envelope remains compatible: existing fields keep
their meaning and new catalog/grouping fields are additive.

## Persisted OperationRun Snapshots

`--persist-operation-run` stores the report in the existing `OperationRun`
model without migrations. The row uses:

- `profile`: `topology_integrity`
- `status`: `completed` when the audit execution succeeds, even if the report
  contains findings.
- `parameters.operation_kind`: `topology_integrity_audit`
- `result.summary`, `result.grouped_summary`, `result.finding_count`,
  `result.path_blocking_count`, and `result.report`.
- `metadata.report_schema`, `metadata.catalog_version`,
  `metadata.workflow_flags`, and `metadata.report_hash`.

The Audit Dashboard exposes the persisted snapshots for operators:

- a status table shows the latest integrity run by fabric, including finding,
  warning, error, and path-blocking counts;
- the selected fabric shows grouped findings with affected workflows and
  remediation-oriented finding rows;
- a `Run Topology Integrity Audit` action runs the service and persists a new
  `OperationRun` snapshot without mutating topology data.

## Integrity Gate Helper

Slice 2C adds a service-level gate for workflows that need a compact "safe
enough to proceed?" answer without wiring UI or import/stamp behavior yet:

```python
from netbox_plant_graph.services.topology_integrity import integrity_gate_for_fabric

gate = integrity_gate_for_fabric(fabric, fail_on='error')
if not gate.ok:
    raise RuntimeError(gate.as_dict())
```

`integrity_gate_for_fabric(...)` returns a `TopologyIntegrityGateResult` with:

- `status`: `pass`, `warn`, or `fail`.
- `ok`: true for non-blocking results (`pass` and `warn`), false for `fail`.
- `summary` and `grouped_summary`: the normalized report summaries.
- `blocking_findings`, `blocking_count`, and `path_blocking_count`.
- `fail_threshold` and `fail_threshold_count`.
- `source`: `fresh_audit`, `supplied_report`, `supplied_operation_run`,
  `operation_run`, `latest_operation_run`, or `no_report`.
- `operation_run_id` when the result came from a persisted run.
- `as_dict()`: a JSON-serializable envelope with schema
  `v2.topology_integrity.gate/1`.

By default the gate runs a fresh audit and fails only when path-blocking
findings exist. Non-blocking `warning` or `error` findings return `warn`, so
operator surfaces can show caution without stopping the workflow. Automation can
opt into stricter fail-hard behavior by passing `fail_on='warning'`,
`fail_on='error'`, or another severity threshold. When that threshold is met,
the result is `fail`.

The helper can also consume an existing report-like payload:

```python
report = audit_topology_integrity(fabric=fabric)
gate = integrity_gate_for_fabric(fabric, report=report.as_dict())
```

Or read the latest persisted topology-integrity `OperationRun` for the fabric:

```python
gate = integrity_gate_for_fabric(fabric, latest=True, run_audit=False)
```

If no latest persisted run exists and `run_audit=False`, the helper returns a
non-blocking `warn` result with `source='no_report'`. Leaving `run_audit=True`
lets it fall back to a fresh audit.

Path Query, Interface Fanout Trace, and Physical Cable Blast Radius read the
latest persisted integrity gate for the selected fabric. When the latest run is
`warn` or `fail`, they render a non-blocking banner and continue the workflow.
When no persisted audit exists, the pages render normally without a warning.
Stamp apply and import apply remain service/API preflight follow-ups.

## Severity

- `critical`: reserved for checks that prove report output is unsafe across a
  broad scope. The initial audit pack does not emit critical findings.
- `error`: data violates topology semantics and can make path or blast-radius
  output wrong.
- `warning`: data is usable but ambiguous, weakly normalized, or likely to
  produce confusing operator displays.
- `info`: reserved for future advisory checks.

Operators should treat any `error` as a blocker for trusting path and
blast-radius results for the affected fabric.

## Finding Catalog

Object families are `architecture`, `cable_plant`, `endpoints`, `channels`,
`lanes`, and `transfer_maps`. Workflow flags are `path_tracing`,
`blast_radius`, `channel_coverage`, `import_reconciliation`,
`stamp_preflight`, and `operator_display`.

Current transceiver coverage is indirect. The audit validates the plugin
endpoint, connector-position, transport-channel, lane, cable, and transfer-map
rows that transceiver bindings depend on, but it does not yet emit dedicated
findings for missing NetBox modules, unmapped module types, absent
`TransceiverConnector` rows, or cable/transceiver polish mismatches. Those
checks are tracked as the next transceiver readiness increment and are described
in `docs/v2_transceiver_modeling_plan.md`.

### Architecture

| Code | Severity | Path blocking | Affected workflows | Remediation |
| --- | --- | --- | --- | --- |
| `dark_mpo_position_usage` | error | yes | path tracing, blast radius, channel coverage, import reconciliation, stamp preflight | Move modeled objects to active MPO positions or update the architecture channel map. |

### Cable Plant

| Code | Severity | Path blocking | Affected workflows | Remediation |
| --- | --- | --- | --- | --- |
| `fiber_segment_endpoint_invalid` | error | yes | path tracing, blast radius, import reconciliation, operator display | Point segment endpoints at two distinct MPO endpoints. |
| `fiber_segment_endpoint_fabric_mismatch` | error | yes | path tracing, blast radius, import reconciliation, operator display | Move the segment to the endpoint fabric or replace the mismatched endpoint. |
| `fiber_strand_termination_count` | error | yes | path tracing, blast radius, import reconciliation, operator display | Ensure each strand has exactly two terminations. |
| `strand_termination_position_mismatch` | error | yes | path tracing, blast radius, import reconciliation, operator display | Align `mpo_endpoint` with the owner of `mpo_position`. |
| `strand_termination_fabric_mismatch` | error | yes | path tracing, blast radius, import reconciliation, operator display | Move the termination to an endpoint in the strand fabric. |
| `fiber_strand_segment_endpoint_mismatch` | error | yes | path tracing, blast radius, import reconciliation, operator display | Terminate the strand on the parent segment endpoint pair. |
| `strand_termination_index_incoherent` | warning | no | operator display | Normalize termination indexes to one `1` and one `2`. |
| `cable_reference_incomplete` | error | no | blast radius, import reconciliation, operator display | Set or clear `cable_site` and `cable_id` together. |
| `cable_reference_missing` | error | no | blast radius, import reconciliation, operator display | Create the referenced `CableAssembly` or correct the strand reference. |
| `cable_parent_self_reference` | error | no | blast radius, operator display | Clear `parent_cable` or point it at a distinct cable assembly. |
| `cable_parent_site_mismatch` | error | no | blast radius, operator display | Use a parent cable assembly from the same site. |

### Endpoints

| Code | Severity | Path blocking | Affected workflows | Remediation |
| --- | --- | --- | --- | --- |
| `channel_position_map_missing_mpo_endpoint` | error | yes | path tracing, blast radius, channel coverage, import reconciliation, stamp preflight | Create the expected MPO child endpoint or correct the channel map matrix. |

### Channels

| Code | Severity | Path blocking | Affected workflows | Remediation |
| --- | --- | --- | --- | --- |
| `channel_position_map_fabric_mismatch` | error | yes | path tracing, blast radius, channel coverage, import reconciliation | Move the map to an MPO endpoint in the channel fabric. |
| `channel_position_map_position_mismatch` | error | yes | path tracing, blast radius, channel coverage, import reconciliation | Align `mpo_endpoint` with the owner of `mpo_position`. |
| `channel_position_map_parent_mismatch` | error | yes | path tracing, blast radius, channel coverage, import reconciliation | Map the channel to an MPO child of the channel endpoint. |
| `channel_position_map_not_disjoint` | error | yes | path tracing, blast radius, channel coverage, import reconciliation | Keep each local MPO position in exactly one channel map. |
| `channel_position_map_incomplete` | error | yes | path tracing, blast radius, channel coverage, import reconciliation | Create all expected active position map rows. |
| `channel_position_map_extra` | error | yes | path tracing, blast radius, channel coverage, import reconciliation | Remove extra map rows or update the architecture matrix. |

### Transfer Maps

| Code | Severity | Path blocking | Affected workflows | Remediation |
| --- | --- | --- | --- | --- |
| `transfer_map_owner_invalid` | error | yes | path tracing, blast radius, import reconciliation, operator display | Set exactly one owner: `owner_node` or `owner_segment`. |
| `transfer_map_owner_fabric_mismatch` | error | yes | path tracing, blast radius, import reconciliation, operator display | Select an owner in the transfer map fabric. |
| `transfer_map_same_position` | error | yes | path tracing, blast radius, import reconciliation, operator display | Select two distinct connector positions. |
| `transfer_map_position_fabric_mismatch` | error | yes | path tracing, blast radius, import reconciliation, operator display | Replace positions with ones in the transfer map fabric. |
| `transfer_map_position_owner_mismatch` | error | yes | path tracing, blast radius, import reconciliation, operator display | Use positions that belong to the owner node or segment endpoint pair. |
| `transfer_map_pattern_architecture_mismatch` | warning | no | stamp preflight, operator display | Use a pattern from the fabric architecture or clear it for custom maps. |
| `transfer_map_pattern_kind_mismatch` | warning | no | stamp preflight, operator display | Align `map_kind` with the selected transfer pattern. |

### Optical Lanes

| Code | Severity | Path blocking | Affected workflows | Remediation |
| --- | --- | --- | --- | --- |
| `optical_lane_endpoint_fabric_mismatch` | error | yes | path tracing, blast radius, channel coverage, operator display | Select an endpoint in the lane fabric. |
| `optical_lane_anchor_fabric_mismatch` | error | yes | path tracing, blast radius, channel coverage, operator display | Select a local MPO endpoint in the lane fabric. |
| `optical_lane_anchor_position_mismatch` | error | yes | path tracing, blast radius, channel coverage, operator display | Align the local MPO endpoint and position. |
| `optical_lane_anchor_parent_mismatch` | error | yes | path tracing, blast radius, channel coverage, operator display | Anchor the lane on an MPO child of the lane endpoint. |
| `optical_lane_plane_fabric_mismatch` | error | yes | path tracing, blast radius, channel coverage, operator display | Select a plane in the lane fabric or clear it. |
| `optical_lane_local_mpo_index_mismatch` | error | yes | path tracing, blast radius, channel coverage, operator display | Correct `local_mpo_index` or endpoint MPO metadata. |
| `optical_lane_missing_channel` | warning | no | channel coverage, operator display | Assign a channel when the lane should participate in channel-scoped audits. |
| `optical_lane_channel_fabric_mismatch` | error | yes | path tracing, blast radius, channel coverage, operator display | Select a channel in the lane fabric. |
| `optical_lane_channel_endpoint_mismatch` | error | yes | path tracing, blast radius, channel coverage, operator display | Select a channel whose endpoint matches the lane endpoint. |
| `optical_lane_channel_plane_mismatch` | error | yes | path tracing, blast radius, channel coverage, operator display | Align the lane plane and channel plane. |
| `optical_lane_channel_position_missing` | error | yes | path tracing, blast radius, channel coverage, operator display | Add the missing channel position map or move the lane. |
| `optical_lane_channel_index_mismatch` | error | yes | path tracing, blast radius, channel coverage, operator display | Move the lane to the expected channel for its MPO index and position. |

## Checks

### Dark MPO Position Usage

For the built-in RoCE 4-plane GB300 2x2 shuffle architecture, active MPO-12
positions are `1,12,2,11` and `3,10,4,9`; positions `5-8` are dark. The audit
flags dark-position use by:

- `OpticalLane.local_mpo_position`
- `StrandTermination.mpo_position`
- `TransportChannelPositionMap.mpo_position`
- `TransferMap.src_position` and `TransferMap.dst_position`

Remediation is to move the modeled object to an active position or update the
architecture channel map if the position is intentionally active.

### Fiber Strand Terminations

Each `FiberStrand` must have exactly two `StrandTermination` rows. The audit
also checks that termination positions belong to their declared MPO endpoints,
termination endpoints belong to the strand fabric, and the two terminations
match the parent `FiberSegment` endpoint pair. Duplicate or invalid
`termination_index` values are warnings.

### Cable Assembly References

`FiberStrand` stores cable assembly identity as `cable_site` plus `cable_id`.
The audit checks that the two fields are set together and resolve to an existing
`CableAssembly`. It also checks referenced cable assemblies for self-parenting
and parent site mismatches.

### Transport Channel Position Maps

For fabrics with a channel map matrix, each `TransportChannel` is checked for
complete expected MPO position maps. The audit flags:

- missing expected map rows,
- extra map rows outside the architecture matrix,
- a local MPO position assigned to multiple channels on the same endpoint,
- maps whose declared MPO endpoint, position, parent endpoint, or fabric do not
  agree.

### Transfer Maps

Each `TransferMap` must have exactly one owner, either `owner_node` or
`owner_segment`. Source and destination positions must be distinct, belong to
the transfer map fabric, and be coherent with the owner:

- node-owned maps should reference positions on endpoints owned by that node;
- segment-owned maps should reference positions on the segment endpoint pair.

The audit also warns when a referenced `TransferPattern` belongs to another
architecture or has a different kind from `TransferMap.map_kind`.

### Optical Lane Anchors And Channels

`OpticalLane` is checked as a transceiver-local anchor. The lane endpoint,
local MPO endpoint, local MPO position, plane, and channel must agree on fabric
and local ownership. When a channel is present, the lane position must be
covered by a `TransportChannelPositionMap`, and the architecture matrix must
map that MPO index and position to the lane channel index.
