# V2.5 Stamping

V2.5 is the operator-facing stamp path for current V2 fabrics. It adds
registry-backed blueprint selection, dry-run preview, validation, retry
classification, name-pattern inspection, and explicit rollback preview/apply
around the idempotent object reconciliation layer.

Low-level reconciliation still lives in `services/stamping.py`, but it is no
longer GB300-only: the executor registry currently supports the bundled GB300
4-plane shuffle, GB300 8-plane shuffle, and H100 direct-attach mini-proof
templates.

## Service Entry Points

- `preview_stamp_template_v25(...)` reads the template, selects a registered or
  persisted blueprint, validates parameters and compatibility, returns a
  `StampOperationPreview`, and does not mutate state.
- `apply_stamp_template_v25(...)` builds the same preview, blocks on validation
  errors, then calls the registry-aware idempotent stamp executor and persists
  the resulting `StampRun`.
- `rollback_stamp_run_v25(...)` returns a `StampRollbackPlan` preview by default. Passing `apply=True` executes the
  constrained compensation plan when every manifest object is clearly plugin-owned and dependency checks pass.
- `classify_stamp_retry_v25(...)` classifies an existing `StampRun` as `retryable`, `blocked`, or
  `already-converged`.

## Supported Bundled Executors

The default blueprint registry and stamp executor registry currently wire these
operator-ready templates:

- `roce-4-plane-gb300-2x2-shuffle` `v2` with primitive
  `roce_4plane_mini_proof`.
- `roce-8-plane-gb300-2x2-shuffle` `v2` with primitive
  `roce_gb300_shuffle_mini_proof`.
- `roce-4-plane-h100-direct-attach` `v2` with primitive
  `roce_direct_attach_mini_proof`.

## Preview Model

`StampOperationPreview` contains:

- `changes`: intended creates/updates for the safe subset of fabric objects, endpoints, connector positions, transport
  channels, fibers, transfer maps, optical lanes, NetBox source devices/interfaces when device creation is enabled,
  plus the `StampRun` and stamp `AuditEvent` created by apply.
- `issues`: structured validation issues with `code`, `path`, `message`, `severity`, and optional `context`.
- `retry`: an idempotent reapply plan keyed by template, fabric slug, and executor.
- `rollback`: a compensation plan shape. Preview-time rollback remains informational because rollback is anchored to a
  persisted `StampRun`, but the same plan schema is used by `rollback_stamp_run_v25(...)`.
- `name_pattern_samples`: generated NetBox device/interface/sub-interface names, whether they already exist, and whether
  an existing object is a collision.
- `architecture_gate`: a blueprint/architecture preflight summary with selected
  blueprint source, slug, version, lifecycle, fixture validity, persisted
  architecture target, persisted schema validity, compatibility status, and
  blueprint issue count.
- `parameter_metadata`: resolved stamp-time parameters, selected phase, wavelength plan, allocation override,
  dark-position overrides, and fabric ownership lookup results.

Preview is a dry run. It may query existing rows to classify `create` vs. `update`, but it must not create fabrics,
NetBox devices, interfaces, stamp runs, or plugin topology rows.

When `creation_options.enabled` is true and the required Site, DeviceType, and DeviceRole objects are supplied, preview
also includes NetBox-side changes for:

- GB300 tray and leaf switch `Device` rows.
- Physical OSFP `Interface` rows.
- 200G child sub-interfaces generated from `channel_subinterfaces.name_pattern`.

This lets operators inspect generated names and collision risk before apply mutates NetBox inventory.

Apply now also uses those NetBox OSFP interfaces as the execution anchor for
installed transceiver modeling:

- it creates or reuses a NetBox `ModuleBay` named after the OSFP interface,
- it creates or reuses a NetBox `Module` when a matching transceiver
  `ModuleType` can be selected,
- it selects a plugin `TransceiverProfile` from explicit stamp parameters,
  module-type/profile mappings, role hints, or the built-in 4x200G OSFP
  default,
- it creates plugin `TransceiverConnector` rows linking the installed module's
  connector profiles to the stamped child MPO endpoints, and
- it records `result.transceiver_bindings`,
  `result.managed_objects.transceiver_connectors`, and any created NetBox
  module/module-bay IDs in the `StampRun`.

## Execute UI Preview

The Stamp Template Execute page renders the same V2.5 preview object used by
the service layer. Operators see:

- Architecture gate status, including fixture validity, persisted architecture
  target, persisted schema validity, and compatibility status.
- Action counts by `create`, `update`, and `skip`.
- Recovery posture: operation key, retry classification, rollback strategy, and
  rollback support.
- Validation issues with severity, code, path, and message.
- The first rows of the dry-run change plan.
- Generated name-pattern samples with collision flags.

The older lightweight count preview remains visible for quick object-count
scanning, but the V2.5 sections are the authoritative safety surface before
execution.

Saved `StampRun` views also expose the V2.5 recovery surface: retry
classification explains whether a failed/incomplete run can be retried, and the
rollback action supports preview-first compensation with an explicit apply step
when the manifest is safe to delete.

## Validation Hooks

Before apply, V2.5 checks:

- The template exposes enough architecture identity to select a registered
  blueprint, or a compatible persisted `FabricArchitecture` stores an importable
  blueprint definition.
- The selected blueprint validates against the architecture schema contract.
- Blueprint lifecycle and required NetBox `DeviceType` compatibility checks pass
  at error severity.
- Stamp-time blueprint parameters validate against the selected blueprint
  `parameter_schema`.
- The persisted architecture target validates when it can be inferred from the
  template FK or from a template `architecture_slug`/`architecture_version`
  hint.
- The persisted architecture target is compatible with the selected blueprint
  contract when persisted validation passes.
- Required architecture roles still exist in the database.
- The template channel map matches the selected blueprint channel map and still
  has complete MPO/subinterface coverage.
- Creation options include required Site, DeviceType, and DeviceRole selections
  when NetBox object creation is enabled.
- Planned fabric/device names do not collide with unrelated existing objects.
- Source bindings use the expected NetBox object types.
- Optional `topology_parameters`, `stamp_phases`, `wavelength_plan`,
  `name_patterns`, `allocation_rule_override`, `dark_position_overrides`, and
  `fabric_ownership` sections are structurally valid before preview or apply
  proceeds.

Validation failures are returned in preview and raise `StampValidationError` from apply. Apply does not partially mutate
when validation fails.

Architecture gate issue prefixes:

- `architecture_schema.*`: built-in fixture validation, persisted architecture validation, or template channel-map schema
  failures. Persisted architecture paths are prefixed with `architecture.` in stamp preview issues.
- `architecture_gate.*`: blueprint identity, registry lookup, or persisted
  blueprint-definition selection failures.
- `architecture_compatibility.*`: persisted architecture compatibility drift. Error-severity issues block apply; warning
  issues, such as a missing `metadata.schema_contract_version`, remain visible but do not block apply.
- `blueprint_parameter.*`: template parameter values that violate the selected
  blueprint `parameter_schema`.
- `blueprint_lifecycle`: deprecated or retired blueprint lifecycle status.

Name collisions are conservative: an existing generated device name is acceptable only when its site, device type, and
role match the requested creation options. A matching existing object is treated as an idempotent update; a mismatch is
reported as `name_collision_risk`.

## Stamp-Time Parameters

Templates may now opt into a conservative Tier 2 parameter block:

- `topology_parameters`: positive integer parameters for `plane_count`, `gpu_tray_count`,
  `leaf_count_per_plane`, `racks_per_pod`, and `pods_per_fabric`. Values may be simple integers or objects with
  `value`, `min`, and `max`. Legacy templates derive the same defaults they used before.
- `stamp_phases`: named phase entries with a `planes` list. `preview_stamp_template_v25(..., phase="phase-a")`,
  `apply_stamp_template_v25(..., phase="phase-a")`, and `execute_stamp_template(..., phase="phase-a")` limit stamping
  to that phase's planes and record the selected phase in `StampRun.parameters` and `StampRun.result.stamp_manifest`.
- `wavelength_plan`: optional `band`, `channel_count`, and explicit `channels` list. Optical lanes use the resolved
  channel values; absent plans keep the original 1311/1313/1315/1317 nm O-band default.
- `name_patterns`: per-kind format strings for preview samples and channel sub-interface creation. Supported variables
  are `{rack_id}`, `{tray_index}`, `{plane_index}`, `{port_index}`, `{channel_index}`, `{node_address}`, and
  `{fabric_slug}`.
- `allocation_rule_override`: selects an alternate `AllocationRuleSet` by slug. Preview errors when the slug is missing
  and warns with `allocation_rule_override_delta` when the override changes the default channel map.
- `dark_position_overrides`: maps plane numbers or labels such as `"Plane 2"` to replacement dark MPO position lists.
  Stamps record the normalized overrides in the manifest, and the topology-integrity dark-position check reads the latest
  completed stamp manifest for per-plane policy.
- `fabric_ownership`: optional `tenant_slug`, `scope_site_slug`, and `scope_location_slug`. Resolved objects are injected
  into the `Fabric`; generated NetBox devices use the resolved site/tenant/location where those Device fields are
  available. Missing references are warning-severity preview issues.
- `transceivers`: optional profile/module selection hints. Supported fields
  include `enabled`, top-level `profile_slug` or `default_profile_slug`,
  top-level `module_type_part_number`, and role-specific sections such as
  `gpu_osfp`, `h100_osfp`, `leaf_osfp`, or `spine_osfp` with
  `profile_slug`, `module_type_part_number`, `part_number`, or `role_hint`.
  `creation_options` can also pass concrete NetBox `ModuleType` objects using
  aliases such as `transceiver_module_type`, `gpu_transceiver_module_type`,
  `leaf_transceiver_module_type`, and `spine_transceiver_module_type`.

Phase-scoped rollback is conservative but no longer blocked solely by the presence of another phase's `StampRun`. When a
run records phase scope and its `managed_objects` manifest contains only objects local to that phase, the compensation
planner can roll that phase back while leaving other phase runs on the same fabric in place. Unphased runs, same-phase
reapplies, manifests containing shared objects such as the `Fabric`, and manifests that overlap another active run remain
blocked.

## Idempotent Apply

The safe subset relies on `update_or_create` reconciliation in
`services/stamping.py`. Reapplying the same V2.5 operation should reuse the
fabric and managed topology rows; only a new `StampRun`/audit record is expected
for the operator action itself.

## Rollback Compensation

Rollback is deliberately constrained. `StampRun.result.managed_objects` is converted into a manifest of concrete objects
before anything is deleted. Each manifest item has this schema:

- `model_label`: Django model label, such as `netbox_plant_graph.fabric`.
- `primary_key`: persisted object primary key.
- `natural_key`: operator-readable identity, such as `fabric-slug:endpoint-address`.
- `action`: currently `delete`.
- `ownership_marker`: the evidence used to treat the object as plugin-owned.

The planner supports rollback only when all of the following are true:

- The run status is `completed`.
- No previous rollback metadata says the run was already rolled back.
- The fabric has no other stamp runs, or the target run is phase-scoped, every other active run on the fabric is scoped
  to a different phase, and the target manifest contains only phase-local objects with no overlap against other run
  manifests. Repeated unphased applies and shared phase manifests remain ambiguous and blocked.
- Every manifest object still exists or can be skipped as already absent, and every existing object has a clear ownership
  marker. Most plugin rows use `metadata.fixture == true`; connector positions inherit ownership from their endpoint;
  transceiver connectors use their binding metadata; cable assemblies require
  `metadata.fixture == true` plus `metadata.fabric_id`; fabrics require fixture,
  template slug, and executor metadata.
- Django delete collection does not reveal non-manifest downstream objects that would be cascaded. Audit events are
  allowed to cascade; operator-owned dependencies such as suppressions block rollback.

`rollback_stamp_run_v25(stamp_run=run)` is preview-only. `rollback_stamp_run_v25(stamp_run=run, apply=True)` deletes in
child-to-parent order and records `result.rollback`/`metadata.rollback` with the applied manifest and deleted counts.

## Retry Classification

`classify_stamp_retry_v25(...)` returns a `StampRetryPlan`:

- `retryable`: failed or incomplete run with enough parameters to pass the current V2.5 preview gates.
- `blocked`: missing retry parameters, failed preview gates, or a run that has already been rolled back.
- `already-converged`: completed run; a fresh apply is idempotent, but it is not treated as a retry of a failed run.
