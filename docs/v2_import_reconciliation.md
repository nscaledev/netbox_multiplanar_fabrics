# V2 Import Reconciliation

This engine gives Madison and lab scripts a common dry-run/apply path for plugin-native V2 objects without embedding object mutation logic in each script.

The implementation lives in `netbox_plant_graph.services.imports` and currently supports:

- `cable_assembly`
- `fiber_strand_cable`
- `endpoint`
- `transport_channel`
- `transport_channel_position_map`
- `strand_termination`

It reports one stable row-level outcome per item:

- `create`: the object does not exist and can be created.
- `update`: the object exists and one or more managed fields differ.
- `skip`: the desired state already exists.
- `conflict`: the row is malformed, references a missing prerequisite, or would violate model integrity.

## Command Usage

Dry-run is the default:

```bash
python manage.py mpf_import_reconcile /path/to/import.json
```

Apply requires an explicit flag:

```bash
python manage.py mpf_import_reconcile /path/to/import.json --apply
```

Apply runs are transactional. If any row reports `conflict`, all creates and updates from that apply run are rolled back and the result still reports the per-row outcomes that would have occurred inside the transaction.

Machine-readable output is available when a script wants to inspect counts and row messages:

```bash
python manage.py mpf_import_reconcile /path/to/import.json --json
```

Use `--fail-on-conflict` when a CI job or wrapper script should exit non-zero if any row reports `conflict`.

## Operator UI

The plugin includes an `Import Preview` page under Build & Run. Operators can
paste JSON or upload a `.json` file, run a browser-native dry run, inspect
row-level outcomes, field diffs, conflicts, and messages, then apply only after
typing `APPLY` in the confirmation field. Apply uses the same transactional
service path as the command; if conflicts remain, no changes are committed.

## JSON Shape

The file is a JSON object with an `items` list. Each item has a `kind` plus natural-key fields and optional managed fields. Top-level `payload_version` and `source_label` are optional and are echoed in the result for audit/provenance.

Payloads may also include an optional top-level architecture hint. The import
engine uses this as a preflight gate before row reconciliation:

```json
{
  "architecture": {
    "slug": "roce-4-plane-gb300-2x2-shuffle",
    "version": "v2",
    "schema_contract_version": "v2",
    "channel_map_matrix": [
      {"subinterface_index": 1, "mpo_index": 1, "positions": [1, 12, 2, 11]},
      {"subinterface_index": 2, "mpo_index": 1, "positions": [3, 10, 4, 9]},
      {"subinterface_index": 3, "mpo_index": 2, "positions": [1, 12, 2, 11]},
      {"subinterface_index": 4, "mpo_index": 2, "positions": [3, 10, 4, 9]}
    ],
    "mpo_position_count": 12,
    "dark_positions": [5, 6, 7, 8]
  },
  "items": []
}
```

Supported aliases include top-level `architecture_slug`,
`architecture_version`, `schema_contract_version`, `channel_map_matrix`,
`mpo_position_count`, and `dark_positions`. If an architecture ID or
slug/version pair resolves to a persisted `FabricArchitecture`, the gate also
compares that row against the built-in RoCE V2 schema contract.

Incompatible hints produce a single `architecture_gate` conflict at row `-1`
and stop before row-level reconciliation or writes. Warning-only drift remains
visible in `plan.architecture_gate` and does not block normal row diffing.

Items can also carry row-level provenance:

- `source_system`
- `source_document`
- `source_row`
- `external_id`
- `idempotency_key`

These fields are optional and existing payloads without them remain valid.

```json
{
  "payload_version": "v2.1",
  "source_label": "madison-workbook-2026-05-20",
  "items": [
    {
      "kind": "cable_assembly",
      "site": "import-site",
      "cable_id": "TRUNK-001",
      "manufacturer": "Example Fiber",
      "model_id": "MPO-12",
      "source_system": "madison-workbook",
      "source_document": "NVL72 fiber BOM.xlsx",
      "source_row": 214,
      "external_id": "fiber-bom-row-214",
      "idempotency_key": "madison:fiber-bom:TRUNK-001",
      "metadata": {
        "source": "madison"
      }
    },
    {
      "kind": "fiber_strand_cable",
      "fabric": "mad-1-roce-fabric",
      "segment": "Jumper-1",
      "strand_index": 1,
      "cable_site": "import-site",
      "cable_id": "TRUNK-001"
    },
    {
      "kind": "endpoint",
      "fabric": "mad-1-roce-fabric",
      "node": "GPU-1",
      "address": "GPU-1.OSFP-1",
      "name": "OSFP-1",
      "endpoint_kind": "plugin_port",
      "connector_kind": "osfp",
      "source_system": "madison-workbook",
      "source_document": "endpoint-map.xlsx",
      "source_row": "Ports!42",
      "external_id": "endpoint-row-42",
      "idempotency_key": "madison:endpoint:GPU-1.OSFP-1"
    },
    {
      "kind": "transport_channel",
      "fabric": "mad-1-roce-fabric",
      "endpoint": "GPU-1.OSFP-1",
      "channel_index": 1,
      "plane": 1,
      "speed_gbps": 200
    },
    {
      "kind": "transport_channel_position_map",
      "fabric": "mad-1-roce-fabric",
      "channel_endpoint": "GPU-1.OSFP-1",
      "channel_index": 1,
      "mpo_endpoint": "GPU-1.OSFP-1.MPO-1",
      "mpo_position": 1
    },
    {
      "kind": "strand_termination",
      "fabric": "mad-1-roce-fabric",
      "segment": "Jumper-1",
      "strand_index": 1,
      "mpo_endpoint": "GPU-1.OSFP-1.MPO-1",
      "mpo_position": 1,
      "termination_index": 1,
      "source_system": "madison-workbook",
      "source_document": "termination-map.xlsx",
      "source_row": 998,
      "external_id": "termination-row-998",
      "idempotency_key": "madison:termination:Jumper-1:1"
    }
  ]
}
```

Natural-key references are intentionally plugin-native:

- Sites use `dcim.Site.slug`.
- Fabrics use `Fabric.slug`.
- Nodes use `FabricNode.address` within a fabric.
- Endpoints use `Endpoint.address` within a fabric.
- Planes use `Plane.plane_number` or label within a fabric.
- Fiber strands use `FiberSegment.name` plus `strand_index` within a fabric.
- MPO positions use `Endpoint.address` plus `ConnectorPosition.position_number`.

## Provenance And Idempotency

When row-level provenance is present, every returned `ImportDiff` includes it in
`details.provenance`. Conflict details keep their existing `code` and dependency
payloads, with provenance added beside them so an operator can trace the failing
row back to a source workbook, document, row, or external system ID.

For target models with JSON metadata, the same provenance is merged into
`metadata.import_reconciliation`. This currently applies to the supported V2
model targets, including `cable_assembly`, `endpoint`, and
`strand_termination`. Existing metadata keys are preserved when the import item
does not explicitly provide a replacement `metadata` object.

Example stored metadata:

```json
{
  "source": "madison",
  "import_reconciliation": {
    "source_system": "madison-workbook",
    "source_document": "NVL72 fiber BOM.xlsx",
    "source_row": 214,
    "external_id": "fiber-bom-row-214",
    "idempotency_key": "madison:fiber-bom:TRUNK-001"
  }
}
```

`idempotency_key` is intentionally not a global lookup key. Reconciliation still
finds objects by the natural keys for each supported kind, then stores and
compares the idempotency key as part of that target object's metadata. Reapplying
the same payload with the same natural key and provenance is therefore
deterministic and should converge to `skip`; changing the key on the same natural
object is reported as a normal metadata update.

## Bundle Planning

Before reconciling rows, the service builds an import bundle with original item indexes, optional payload metadata, produced natural keys, and obvious dependency references. Dependency planning currently recognizes:

- `fiber_strand_cable` -> `cable_assembly`
- `cable_assembly.parent_cable` -> `cable_assembly`
- `endpoint.parent` -> `endpoint`
- `transport_channel` -> `endpoint`
- `transport_channel_position_map` -> `transport_channel`
- `transport_channel_position_map` -> MPO `endpoint`
- `strand_termination` -> MPO `endpoint`

When a prerequisite is included in the same payload, apply order is topologically sorted so the prerequisite is reconciled first even if it appears later in the file. Rows without bundle dependencies keep deterministic file-order evaluation. The returned plan keeps `diffs` keyed to the original row indexes and adds:

- `payload_version`
- `source_label`
- `apply_order`
- `dependency_edges`
- `committed`
- `transactional`

Missing bundle-aware prerequisites produce a `conflict` with structured `details`, including `code: "missing_prerequisite"` and a `missing_prerequisites` list with the field, reference kind, lookup, and edge status. Dependency cycles produce `code: "dependency_cycle"` details with the blocked item indexes and dependency edges.

## Current Boundaries

This is a reconciliation layer for existing V2 topology scaffolding, not a full fabric stamper. Scripts should create or stamp these prerequisites before calling it:

- `Fabric`
- `Plane`
- `FabricNode`
- `FiberSegment`
- `ConnectorPosition`

`fiber_strand_cable` updates the nullable `FiberStrand.cable_site` and `FiberStrand.cable_id` linkage. It does not create `FiberSegment` rows.

`transport_channel_position_map` and `strand_termination` require the target MPO `ConnectorPosition` to already exist.

Provenance does not create prerequisites, bypass natural-key matching, or perform
cross-object idempotency lookup. If a future supported kind lacks a metadata
field, provenance should still be returned in `ImportDiff.details` even though it
cannot be persisted on the target object.

## Calling From Madison Scripts

Scripts should produce normalized item dictionaries, dry-run them, and only apply after the operator accepts the diff:

```python
from netbox_plant_graph.services.imports import reconcile_import_payload

payload = {"items": items}
dry_run = reconcile_import_payload(payload)
for diff in dry_run.diffs:
    print(diff.message)

if apply_enabled and not dry_run.has_conflicts:
    applied = reconcile_import_payload(payload, apply=True)
    for diff in applied.diffs:
        print(diff.message)
```

The returned plan has `summary`, `diffs`, `has_changes`, `has_conflicts`, dependency metadata, `architecture_gate`, and `to_dict()` for script-friendly reporting. In JSON output, the existing `applied`, `summary`, and `diffs` keys remain stable; the bundle and architecture-gate fields are additive.
