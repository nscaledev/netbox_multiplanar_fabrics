# V2 Ground-Up Rewrite Implementation Plan

Status: implementation-complete execution record for `codex-mencken/v2-ground-up-rewrite`
Last updated: 2026-05-20

## Goal

Ship a working V2 kernel that can:

1. Stamp the RoCE 4-plane mini architecture.
2. Anchor active topology elements to real NetBox `Device` and `Interface` objects.
3. Model passive fiber/shuffle connectivity natively in plugin tables.
4. Resolve per-lambda optical paths end-to-end from source lane to destination lane.

NetBox `Cable`, `CablePath`, `CableTermination`, and `PortMapping` remain forbidden as modeled-fabric source of truth.

## Current State Snapshot

Completed in code:

- V2 model layer and greenfield migration.
- Resolver for arbitrary-hop path traversal over connector-position graph.
- Mini proof architecture fixture and hybrid stamp executor dispatch.
- Idempotent mini-proof stamping.
- Registry-generated standard object/API pages.
- Hand-wired workflow pages for home, path query, seed, and stamp execution.
- Template-driven source-binding fields in stamp workflow.
- Source anchoring to NetBox `Device` and `Interface`.
- Stamp-run provenance with managed-object IDs and counts.

Completed for MVP:

- Strict template validation at the boundary.
- NetBox device create-or-bind mode from execution payload.
- End-to-end acceptance test for workflow stamping and path resolution.

## Execution Rules

- Implement slices in numeric order unless blocked.
- Keep tests focused per slice; defer full-suite runs until after Slice 6.
- Workflow pages remain hand-wired.
- Standard CRUD/API pages stay registry-driven.

## Ordered Implementation Queue

### Slice 0: Baseline (Completed)

Status: done.

Delivered:

- In-place replacement strategy in `netbox_plant_graph`.
- V2 boot path and foundational test discovery.

### Slice 1: V2 Data Kernel (Completed)

Status: done.

Delivered:

- Core V2 tables in [models.py](/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/models.py).
- Initial migration in [0001_initial.py](/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/migrations/0001_initial.py).

### Slice 2: Resolver Kernel (Completed)

Status: done.

Delivered:

- BFS path resolver in [resolver.py](/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/services/resolver.py).
- Coverage in [test_v2_resolver.py](/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/tests/test_v2_resolver.py).

### Slice 3: Executable 4-Plane Fixture (Completed)

Status: done for mini proof.

Delivered:

- Architecture fixture in [architecture.py](/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/services/architecture.py).
- Hybrid stamping primitive in [stamping.py](/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/services/stamping.py).

### Slice 4: Stamping Engine MVP Hardening (Completed)

Status: done.

#### 4.1 Template validation at execution boundary

Objective:

- Reject malformed `StampTemplate.template` payloads before mutation begins.

Completed:

1. Added a validation module for schema and semantic checks.
2. Enforced required keys for template identity, executor, planes, role groups, proof paths, and source bindings.
3. Enforced cross-field rules for proof path bounds, plane assignment consistency, and source binding linkage.
4. Wired validation at the start of `execute_stamp_template()`.
5. Added explicit error messages and failure-path tests.

Primary files:

- [stamping.py](/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/services/stamping.py)
- [architecture.py](/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/services/architecture.py)
- [test_v2_stamping.py](/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/tests/test_v2_stamping.py)

Verification:

- `python3 -m compileall netbox_plant_graph/services/stamping.py netbox_plant_graph/tests/test_v2_stamping.py`
- `manage.py test netbox_plant_graph.tests.test_v2_stamping --verbosity 2 --noinput`

Exit criteria:

- Invalid templates fail before any DB writes.
- Existing valid mini proof template still stamps successfully.

#### 4.2 NetBox device creation mode

Objective:

- Support stamping active roles by creating NetBox devices when requested by template parameters.

Completed:

1. Added creation parameters to execution payload.
2. Implemented deterministic create-or-bind behavior for active nodes and OSFP interfaces.
3. Recorded created NetBox device and interface IDs in `StampRun.result.netbox_created_objects`.
4. Preserved explicit source binding behavior and merge precedence.

Primary files:

- [stamping.py](/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/services/stamping.py)
- [forms.py](/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/forms.py)
- [views.py](/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/views.py)
- [test_v2_stamping.py](/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/tests/test_v2_stamping.py)
- [test_v2_ui.py](/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/tests/test_v2_ui.py)

Verification:

- Focused stamping tests for create path and bind path.
- Focused UI test for execution workflow submission with create mode.

Exit criteria:

- We can stamp with no pre-existing GPU or leaf devices when create mode is selected.
- Re-run remains idempotent.

### Slice 5: Operator Workflow Completion (Completed)

Status: done.

Completed:

1. Added stamp run summary message with managed object counts and failure count.
2. Added links to stamp run detail and stamped fabric in result flow.
3. Added fabric filtering support on the path query page while preserving lane-pair resolution behavior.

Primary files:

- [views.py](/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/views.py)
- [stamp_template_execute.html](/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/templates/netbox_plant_graph/stamp_template_execute.html)
- [path_query.html](/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/templates/netbox_plant_graph/path_query.html)
- [test_v2_ui.py](/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/tests/test_v2_ui.py)

Exit criteria:

- Operator can stamp, see summary, click into outputs, and query paths without using admin or shell.

### Slice 6: Acceptance Gate (Completed)

Status: done.

Acceptance test sequence:

1. Stamp one mini fabric via workflow.
2. Resolve all expected source-to-destination lane paths.
3. Verify no modeled-fabric `Cable` rows are created.
4. Verify source anchors and managed-object provenance are present.

Primary files:

- [test_v2_ui.py](/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/tests/test_v2_ui.py)
- [test_v2_stamping.py](/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/tests/test_v2_stamping.py)

Exit criteria:

- End-to-end acceptance test passes in a clean DB.
- Acceptance assertions include path resolution, no NetBox cable creation, source anchoring, and provenance checks.

### Slice 7: Cutover Decision Pack (Completed)

Status: done.

Completed:

1. Finalized migration policy from V1 data: no in-place migration for MVP.
2. Finalized publication path: in-place package replacement for `netbox_plant_graph`.
3. Added cutover decisions and an operator runbook.

Primary files:

- [v2_ground_up_rewrite_plan.md](/Users/mencken/github-repos/netbox_multiplanar_fabrics/docs/v2_ground_up_rewrite_plan.md)
- [v2_cutover_decisions.md](/Users/mencken/github-repos/netbox_multiplanar_fabrics/docs/v2_cutover_decisions.md)
- [v2_cutover_runbook.md](/Users/mencken/github-repos/netbox_multiplanar_fabrics/docs/v2_cutover_runbook.md)

## Active Backlog Board

Now:

- `MVP complete`.

Next:

- Full-suite regression pass before production rollout.

Then:

- Optional post-MVP enhancements from out-of-scope backlog.

## Test Strategy

Per-slice focused commands:

1. `manage.py test netbox_plant_graph.tests.test_v2_stamping --verbosity 2 --noinput`
2. `manage.py test netbox_plant_graph.tests.test_v2_ui --verbosity 2 --noinput`
3. `manage.py test netbox_plant_graph.tests.test_v2_registry netbox_plant_graph.tests.test_v2_api --verbosity 2 --noinput`

Deferred until Slices 4-6 are complete:

- Broader plugin suite and regression passes.

## Out Of Scope Until After MVP

- Audit/suppression lifecycle systems.
- Floorplan and spatial integration.
- Lane workspace and policy dashboards.
- Madison-specific operational scripts as plugin core behavior.
- Expanded GraphQL beyond minimal path query needs.
