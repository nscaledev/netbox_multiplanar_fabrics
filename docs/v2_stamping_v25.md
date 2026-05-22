# V2.5 Stamping

V2.5 adds a safer operator-facing layer around the existing RoCE mini-proof stamp executor. The MVP executor still owns
the actual object reconciliation; V2.5 adds dry-run preview, validation, retry shape, name-pattern inspection, and an
explicit rollback contract.

## Service Entry Points

- `preview_stamp_template_v25(...)` reads the template and database, returns a `StampOperationPreview`, and does not
  mutate state.
- `apply_stamp_template_v25(...)` builds the same preview, blocks on validation errors, then calls the existing
  idempotent stamp executor.
- `rollback_stamp_run_v25(...)` returns a `StampRollbackPlan` preview by default. Passing `apply=True` executes the
  constrained compensation plan when every manifest object is clearly plugin-owned and dependency checks pass.
- `classify_stamp_retry_v25(...)` classifies an existing `StampRun` as `retryable`, `blocked`, or
  `already-converged`.

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
- `architecture_gate`: a lightweight architecture preflight summary with fixture validity, inferred persisted
  architecture target, persisted schema validity, and compatibility status.

Preview is a dry run. It may query existing rows to classify `create` vs. `update`, but it must not create fabrics,
NetBox devices, interfaces, stamp runs, or plugin topology rows.

When `creation_options.enabled` is true and the required Site, DeviceType, and DeviceRole objects are supplied, preview
also includes NetBox-side changes for:

- GB300 tray and leaf switch `Device` rows.
- Physical OSFP `Interface` rows.
- 200G child sub-interfaces generated from `channel_subinterfaces.name_pattern`.

This lets operators inspect generated names and collision risk before apply mutates NetBox inventory.

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

## Validation Hooks

Before apply, V2.5 checks:

- The built-in architecture schema helper validates successfully.
- The persisted architecture target validates when it can be inferred from the template FK or from a unique
  `architecture_slug`/`architecture_version` template hint.
- The persisted architecture target is compatible with the built-in RoCE V2 contract when persisted validation passes.
- The selected template targets the supported built-in architecture/version.
- Required architecture roles still exist in the database.
- The template channel map matches the architecture channel map and still has complete MPO/subinterface coverage.
- Creation options include required Site, DeviceType, and DeviceRole selections when NetBox object creation is enabled.
- Planned fabric/device names do not collide with unrelated existing objects.
- Source bindings use the expected NetBox object types.

Validation failures are returned in preview and raise `StampValidationError` from apply. Apply does not partially mutate
when validation fails.

Architecture gate issue prefixes:

- `architecture_schema.*`: built-in fixture validation, persisted architecture validation, or template channel-map schema
  failures. Persisted architecture paths are prefixed with `architecture.` in stamp preview issues.
- `architecture_compatibility.*`: persisted architecture compatibility drift. Error-severity issues block apply; warning
  issues, such as a missing `metadata.schema_contract_version`, remain visible but do not block apply.
- `unsupported_architecture` and `unsupported_architecture_version`: template hints target an architecture outside the
  conservative V2.5 runner contract.

Name collisions are conservative: an existing generated device name is acceptable only when its site, device type, and
role match the requested creation options. A matching existing object is treated as an idempotent update; a mismatch is
reported as `name_collision_risk`.

## Idempotent Apply

The safe subset relies on existing `update_or_create` reconciliation in the MVP executor. Reapplying the same V2.5
operation should reuse the fabric and managed topology rows; only a new `StampRun`/audit record is expected for the
operator action itself.

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
- The fabric has no other stamp runs, because repeated idempotent applies make per-run creation ownership ambiguous.
- Every manifest object still exists or can be skipped as already absent, and every existing object has a clear ownership
  marker. Most plugin rows use `metadata.fixture == true`; connector positions inherit ownership from their endpoint;
  cable assemblies require `metadata.fixture == true` plus `metadata.fabric_id`; fabrics require fixture, template slug,
  and executor metadata.
- Django delete collection does not reveal non-manifest downstream objects that would be cascaded. Audit events are
  allowed to cascade; operator-owned dependencies such as suppressions block rollback.

`rollback_stamp_run_v25(stamp_run=run)` is preview-only. `rollback_stamp_run_v25(stamp_run=run, apply=True)` deletes in
child-to-parent order and records `result.rollback`/`metadata.rollback` with the applied manifest and deleted counts.

## Retry Classification

`classify_stamp_retry_v25(...)` returns a `StampRetryPlan`:

- `retryable`: failed or incomplete run with enough parameters to pass the current V2.5 preview gates.
- `blocked`: missing retry parameters, failed preview gates, or a run that has already been rolled back.
- `already-converged`: completed run; a fresh apply is idempotent, but it is not treated as a retry of a failed run.
