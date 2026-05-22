# V2 External Automation Contracts

Contract Date: `2026-05-22`

This document defines which V2 surfaces external scripts and operators may rely
on. Anything not named here is an implementation detail, even if it is reachable
from Python, HTML, JavaScript, or local Madison scripts.

## Contract Labels

Use these labels when writing automation:

- `Stable`: external consumers may depend on the named route, command, option,
  top-level payload shape, and documented field names. Breaking changes require
  a contract version bump or explicit migration note.
- `Experimental`: useful for operators and lab automation, but payload details
  can change inside the V2 line. Gate usage with smoke tests.
- `Non-contract`: do not depend on it from external automation.

| Surface | Label | Contract |
| --- | --- | --- |
| REST registry object endpoints | Stable for reads, cautious for direct writes | `/api/plugins/plant-graph/<resource>/`, standard NetBox list/detail pagination, and fields from the V2 registry. Prefer stamp or import/reconcile for graph-building writes. |
| REST workflow endpoints | Stable | Path query, stamp preview/execute/rollback, workflow finding lifecycle, disjointness exception lifecycle, operation summaries. |
| REST operational impact endpoints | Stable envelope, experimental nested details | `/api/plugins/plant-graph/impact/<scenario>/` returns `OperationalImpactReport.as_dict()` top-level keys for cable cut, MPO unplug, and OSFP unseat previews. Nested detail objects may gain fields. |
| GraphQL minimal query set | Stable | `graphql_contract_version`, `v2_status`, `fabrics`, `optical_lanes`, `optical_lane_path`, `stamp_runs`, `suppression_rules`, `audit_events`, `operation_runs`. |
| GraphQL expanded operational JSON | Experimental | `lane_drilldown`, `lane_compare`, `blast_radius`, audit/policy dashboards, contamination domains, deployment summary, stamp template preview. |
| Import/reconcile engine | Stable nucleus, experimental extended kinds | Stable for `cable_assembly` and `fiber_strand_cable` plan/apply shape. Endpoint/channel/map/termination kinds are available but should be smoke-tested before production use. |
| Topology integrity audit | Stable envelope, experimental finding codes | `mpf_audit_integrity` command, `--format json`, and top-level report keys are stable. Individual finding codes/details may expand. |
| Visual path/fanout/blast pages | Operator-stable, machine-experimental | URL entry points are intended for operators. Do not scrape DOM, SVG, embedded JSON, CSS classes, or static asset versions. |
| Private services and local scripts | Non-contract | Python helpers under `services/`, `views.py` private functions, and `local-netbox-dev/scripts/*` can change without external notice. |

## Versioning And Deprecation Policy

Stable contracts are managed as operational interfaces, not incidental code
paths. Any PR touching a stable route, command option, GraphQL field, or
documented top-level envelope key must include a contract-impact review.

### GraphQL Contract Version

Current GraphQL contract version is `2.0.0`.

Change `graphql_contract_version` when any stable GraphQL behavior changes in a
way that can break a consumer:

- Removing or renaming a stable field.
- Renaming an argument, changing an argument type, or changing required vs
  optional behavior for a stable field.
- Removing, renaming, or changing the meaning/type of a documented stable
  top-level JSON key returned by a stable field.
- Changing pagination, ordering, or filter semantics that stable consumers may
  rely on.

Do not bump the version for purely additive fields, additional experimental
payload keys, bug fixes that restore the documented behavior, or documentation
clarifications. Additive fields must remain optional for consumers.

### REST Routes And Aliases

Stable REST paths must keep their route names and URL paths. If a route must be
renamed or moved:

1. Add the replacement route and keep the old route as an alias.
2. Mark the old route deprecated in this document with the replacement, first
   deprecated contract date, and earliest removal date.
3. Keep the alias for at least one published minor release and at least 90 days,
   whichever is longer. If there is no release train during that window, keep
   the alias through the rest of the V2 line.
4. Keep smoke tests covering both the preferred route and the deprecated alias
   while both are supported.

Adding a new REST route is non-breaking. Removing a stable route, changing a
stable method, or removing/renaming a documented stable request or response key
is breaking and requires an explicit migration note.

### Payload Envelopes

Stable top-level keys are append-only by default. Automation should ignore
unknown keys, and the plugin may add new keys without a version bump.

Breaking payload changes include removing a documented stable key, renaming it,
changing its type, or changing its meaning. Nested keys marked experimental may
expand or change inside the V2 line, but stable envelopes must continue to carry
the documented top-level keys.

### Command Options

Documented command options for `mpf_audit_integrity` and
`mpf_import_reconcile` are stable. New options are additive. Renaming or
removing a documented option requires a deprecated alias for the same minimum
window as REST route aliases and a migration note in this document.

### Stable-Contract Release Checklist

For every stable-contract change:

- Update this document and any affected `docs/examples/*` recipe.
- Add or update smoke tests for route existence, command option strings,
  GraphQL contract version/field availability, or stable envelope keys.
- State whether the change is additive, deprecated, or breaking.
- For deprecations, name the replacement and earliest removal date.
- For breaking GraphQL changes, bump `graphql_contract_version`.
- Run the focused external contract tests before merge.

## REST Contract

The REST root for this plugin is:

```text
/api/plugins/plant-graph/
```

Use normal NetBox API authentication:

```bash
curl -H "Authorization: Token $NETBOX_TOKEN" \
  "$NETBOX_URL/api/plugins/plant-graph/fabrics/?slug=mad-1-roce-fabric"
```

### Registry Resources

The generated registry endpoints use the same pattern:

- `GET /api/plugins/plant-graph/<resource>/`
- `POST /api/plugins/plant-graph/<resource>/`
- `GET /api/plugins/plant-graph/<resource>/<id>/`
- `PATCH /api/plugins/plant-graph/<resource>/<id>/`
- `DELETE /api/plugins/plant-graph/<resource>/<id>/`

Resource catalog:

| Resource | Model |
| --- | --- |
| `architectures` | `FabricArchitecture` |
| `architecture-roles` | `ArchitectureRole` |
| `transfer-patterns` | `TransferPattern` |
| `allocation-rule-sets` | `AllocationRuleSet` |
| `fabrics` | `Fabric` |
| `planes` | `Plane` |
| `nodes` | `FabricNode` |
| `endpoints` | `Endpoint` |
| `connector-positions` | `ConnectorPosition` |
| `transport-channels` | `TransportChannel` |
| `transport-channel-position-maps` | `TransportChannelPositionMap` |
| `fiber-segments` | `FiberSegment` |
| `cable-assemblies` | `CableAssembly` |
| `fiber-strands` | `FiberStrand` |
| `strand-terminations` | `StrandTermination` |
| `optical-lanes` | `OpticalLane` |
| `transfer-maps` | `TransferMap` |
| `path-intents` | `PathIntent` |
| `stamp-templates` | `StampTemplate` |
| `stamp-runs` | `StampRun` |
| `suppression-rules` | `SuppressionRule` |
| `audit-events` | `AuditEvent` |
| `operation-runs` | `OperationRun` |

Example read:

```bash
curl -H "Authorization: Token $NETBOX_TOKEN" \
  "$NETBOX_URL/api/plugins/plant-graph/cable-assemblies/?site=12"
```

Direct registry writes are supported by NetBox permissions, but they are not the
preferred contract for building topology graphs. Use stamp execution for whole
fabric scaffolds and import/reconcile for repeatable script-fed updates.

### REST Query Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/path-query/?source_lane=<id>&destination_lane=<id>` | Resolve a plugin-native optical path. `destination_lane` is optional. |
| `GET` | `/suppression-summary/?fabric=<id>` | List recent suppression rules. |
| `GET` | `/audit-timeline/?fabric=<id>` | List recent audit events. |
| `GET` | `/workflow/summary/?fabric=<id>&plane=<id>` | Summarize audit workflow state. |
| `GET` | `/workflow/findings/?fabric=<id>&status=<status>` | Search workflow findings. |
| `GET` | `/workflow/findings/<id>/` | Fetch one finding and transition history. |
| `GET` | `/workflow/runs/?fabric=<id>&limit=<n>` | List operation runs. |
| `GET` | `/operation-runs/?fabric=<id>&limit=<n>` | Alias for operation run summaries. |

Example path query:

```bash
curl -H "Authorization: Token $NETBOX_TOKEN" \
  "$NETBOX_URL/api/plugins/plant-graph/path-query/?source_lane=101&destination_lane=202"
```

Stable response keys are `path_found`, `source_lane_id`,
`destination_lane_id`, `error`, and `steps`. Each step has `step_type`,
`object_type`, `object_id`, `label`, and `metadata`.

### REST Impact Preview Endpoints

These POST endpoints are non-mutating previews. The top-level response envelope
is stable and matches `OperationalImpactReport.as_dict()`:

- `scenario`
- `scope`
- `summary`
- `simulated_components`
- `impacted_paths`
- `impacted_lanes`
- `impacted_channels`
- `impacted_endpoints`
- `impacted_devices`
- `hierarchy`

Nested entries are JSON objects owned by the operational-impact report schema.
Automation may read documented IDs and severity fields, but should tolerate
additional nested fields.

| Method | Path | Required target field |
| --- | --- | --- |
| `POST` | `/impact/cable-assembly-cut/` | `cable_assembly_id` or `cable_assembly_ids` |
| `POST` | `/impact/mpo-connector-unplug/` | `connector_endpoint_id` or `connector_endpoint_ids` |
| `POST` | `/impact/osfp-transceiver-unseat/` | `interface_id` or `interface_ids` |

Optional request fields:

- `fabric`: fabric ID scope.
- `max_depth`: resolver depth, default `64`, maximum `256`.

Validation failures return HTTP `400` with field-keyed messages for missing
targets, unknown IDs, endpoints without connector positions, interfaces without
modeled OSFP endpoints, and unknown fabric IDs.

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

### REST Mutation Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/stamps/preview/` | Preview a V2 stamp template. Also registered under the `stamp-preview` and `stamps-preview` route names. |
| `POST` | `/stamp-templates/<id>/execute/` | Execute a stamp template and create a `StampRun`. |
| `POST` | `/stamp-runs/<id>/rollback/` | Roll back a stamp run. |
| `POST` | `/workflow/findings/<id>/acknowledge/` | Mark a finding acknowledged. |
| `POST` | `/workflow/findings/<id>/start-remediation/` | Mark a finding in progress. |
| `POST` | `/workflow/findings/<id>/suppress/` | Suppress a finding. |
| `POST` | `/workflow/findings/<id>/unsuppress/` | Remove finding suppression. |
| `POST` | `/workflow/findings/<id>/resolve/` | Resolve a finding. |
| `POST` | `/workflow/findings/<id>/reopen/` | Reopen a finding. |
| `POST` | `/disjointness-exceptions/request/` | Request a disjointness exception. |
| `POST` | `/disjointness-exceptions/<id>/approve/` | Approve an exception. |
| `POST` | `/disjointness-exceptions/<id>/expire/` | Expire an exception. |
| `POST` | `/disjointness-exceptions/<id>/reactivate/` | Reactivate an exception. |

Stamp preview example:

```bash
curl -X POST -H "Authorization: Token $NETBOX_TOKEN" \
  -H "Content-Type: application/json" \
  "$NETBOX_URL/api/plugins/plant-graph/stamps/preview/" \
  -d '{
    "template_type": "stamp_template",
    "template_id": 10,
    "parameters": {
      "fabric_name": "Madison Preview",
      "fabric_slug": "madison-preview"
    }
  }'
```

Stamp execute example:

```bash
curl -X POST -H "Authorization: Token $NETBOX_TOKEN" \
  -H "Content-Type: application/json" \
  "$NETBOX_URL/api/plugins/plant-graph/stamp-templates/10/execute/" \
  -d '{
    "fabric_name": "Madison Fabric",
    "fabric_slug": "madison-fabric",
    "source_bindings": {},
    "creation_options": {}
  }'
```

Finding lifecycle example:

```bash
curl -X POST -H "Authorization: Token $NETBOX_TOKEN" \
  -H "Content-Type: application/json" \
  "$NETBOX_URL/api/plugins/plant-graph/workflow/findings/55/suppress/" \
  -d '{"reason": "approved maintenance window", "days": 7}'
```

## GraphQL Contract

GraphQL is read-oriented. It has no V2 mutation contract. Use REST for workflow
state changes and import/reconcile for script-fed data updates.

Current contract version:

```graphql
query ContractVersion {
  graphql_contract_version
}
```

Minimal stable query examples:

```graphql
query FabricAndLaneInventory($fabric_id: ID!) {
  graphql_contract_version
  v2_status
  fabrics
  optical_lanes(fabric_id: $fabric_id, direction: "send")
}
```

```graphql
query ResolveLanePath($source_id: ID!, $destination_id: ID) {
  optical_lane_path(source_id: $source_id, destination_id: $destination_id)
}
```

See `docs/v2_graphql_contract_v2.md` for known V2 breaks from the previous
baseline and the current minimal supported field set.

## Import/Reconcile Contract

The import/reconcile command is the preferred path for Madison/lab scripts that
already know the desired plugin-native state:

```bash
python manage.py mpf_import_reconcile import.json
python manage.py mpf_import_reconcile import.json --apply
python manage.py mpf_import_reconcile import.json --json --fail-on-conflict
```

Stable plan/result keys are `applied`, `summary`, and `diffs`. Stable outcomes
are `create`, `update`, `skip`, and `conflict`.

`architecture_gate` is an additive preflight envelope. Scripts may provide
architecture hints in the import payload; incompatible hints produce a blocking
`architecture_gate` conflict before row-level reconciliation. Existing clients
that ignore unknown top-level result keys remain compatible.

The stable nucleus is:

- `cable_assembly`
- `fiber_strand_cable`

The following kinds exist but remain experimental for external automation until
they have production Madison soak time:

- `endpoint`
- `transport_channel`
- `transport_channel_position_map`
- `strand_termination`

See `docs/v2_import_reconciliation.md` for JSON item shapes.

## Topology Audit Contract

Use topology integrity audits before trusting path, visual trace, or
blast-radius output:

```bash
python manage.py mpf_audit_integrity --fabric mad-1-roce-fabric
python manage.py mpf_audit_integrity --fabric mad-1-roce-fabric --format json
python manage.py mpf_audit_integrity --fabric mad-1-roce-fabric --fail-on error
```

Stable JSON envelope keys are:

- `scope`
- `ok`
- `summary`
- `checked`
- `findings`

Individual finding `code` values, `details`, and remediation copy are
experimental and may expand as new integrity checks are added.

## Visual And Blast-Radius Surfaces

Operator UI entry points:

- `/plugins/plant-graph/path-query/`
- `/plugins/plant-graph/interface-fanout-trace/`
- `/plugins/plant-graph/blast-radius/`

The page routes and query parameters used by the UI are operator-stable.
Automation must not scrape HTML structure, data attributes, SVG geometry,
static asset versions, or embedded JSON. Use REST `path-query/` for stable path
resolution and REST `impact/<scenario>/` endpoints for machine impact modeling.
Treat GraphQL `blast_radius` as experimental unless covered by a local snapshot
test.

## Automation Recipes

Runnable starter fragments live in `docs/examples/`. Stable examples are safe
to adapt for automation against the documented surfaces. Experimental examples
must be guarded with local smoke or snapshot tests.

### Stamp, Validate, Audit

1. Preview:
   `POST /api/plugins/plant-graph/stamps/preview/`
2. Execute:
   `POST /api/plugins/plant-graph/stamp-templates/<id>/execute/`
3. Confirm object surfaces:
   `GET /api/plugins/plant-graph/fabrics/?slug=<slug>`
4. Validate topology:
   `python manage.py mpf_audit_integrity --fabric <slug> --fail-on error`
5. Review run state:
   `GET /api/plugins/plant-graph/workflow/runs/?fabric=<id>`

Rollback is explicit:

```text
POST /api/plugins/plant-graph/stamp-runs/<id>/rollback/
```

### Import And Re-Audit

1. Generate import JSON from the external source of truth.
2. Dry-run:
   `python manage.py mpf_import_reconcile import.json --json`
3. Stop on any `conflict`.
4. Apply:
   `python manage.py mpf_import_reconcile import.json --apply --json`
5. Run topology audit with `--fail-on error`.
6. Spot-check one path with REST `path-query/`.

### Path Query And Visual Review

1. Find lanes through REST or stable GraphQL inventory.
2. Resolve the path with:
   `GET /api/plugins/plant-graph/path-query/?source_lane=<id>&destination_lane=<id>`
3. For operator review, open:
   `/plugins/plant-graph/path-query/?source_lane=<id>&destination_lane=<id>`
4. Do not treat the visual trace DOM/SVG as a machine contract.

### Blast Radius

1. Run topology audit first.
2. For automation, call the REST impact preview endpoint for the scenario:
   `POST /api/plugins/plant-graph/impact/cable-assembly-cut/`,
   `POST /api/plugins/plant-graph/impact/mpo-connector-unplug/`, or
   `POST /api/plugins/plant-graph/impact/osfp-transceiver-unseat/`.
3. For operator review, use `/plugins/plant-graph/blast-radius/`.
4. Treat GraphQL `blast_radius` as experimental behind a version check and local
   snapshot tests.
5. Do not use private Python helpers or UI embedded JSON for production
   automation.

## Non-Contract Internals

These are explicitly outside the external contract:

- `netbox_plant_graph.services.*` helper signatures except documented import
  entry points.
- Private view helpers in `views.py`.
- `local-netbox-dev/scripts/*`.
- Model `metadata` keys unless a contract document names them.
- UI template block names, CSS classes, DOM IDs, JavaScript events, and SVG
  layout.
- GraphQL experimental JSON nested keys beyond the stable top-level query
  availability.
