# V2 Next High-Value Improvements Plan

Status: implementation complete; focused integration verification passed
Last updated: 2026-05-22

## Objective

Move the plugin from a strong operator-facing V2 implementation toward a safer,
more reusable, more automatable product surface.

The next work should prioritize semantic hardening and import/reconciliation
before broadening visual/workflow features. The goal is to make stamped or
imported fabric data difficult to corrupt and easy to validate before operators
trust trace and blast-radius output.

## Current Status

- Item 1, Architecture Schema Hardening: completed by Cicero and reviewed.
- Item 2, Topology Integrity Audit Pack: completed by Newton, reviewed, and
  coordinator-polished for cable-reference and duplicate-map edge cases.
- Item 3, Generic Import/Reconciliation Pipeline: completed by Lagrange and
  reviewed.
- Item 4, Stamping V2.5: completed by Cicero and reviewed.
- Item 5, Operational Impact Modeling: completed by Newton and reviewed.
- Item 6, Visual Trace Reusable Component: completed by Lagrange and reviewed.
- Item 7, External Contract Cleanup: completed by Lagrange.

## Completed First Wave

### Item 1: Architecture Schema Hardening

Owner: sub-agent Cicero

Status: completed

Scope:

- `netbox_plant_graph/services/architecture_schema.py`
- `netbox_plant_graph/tests/test_v2_architecture_schema.py`
- `docs/v2_architecture_schema.md`
- minimal `netbox_plant_graph/services/architecture.py` exposure only if needed

Expected result:

- Versioned validation helpers for architecture definitions.
- Structured validation errors for roles, transfer patterns, allocation rules,
  channel maps, active/dark MPO positions, and 2x2 shuffle invariants.
- Tests proving the built-in `roce-4-plane-gb300-2x2-shuffle` fixture validates
  and representative malformed definitions fail clearly.

### Item 2: Topology Integrity Audit Pack

Owner: sub-agent Newton

Status: completed

Scope:

- `netbox_plant_graph/services/topology_integrity.py`
- `netbox_plant_graph/management/commands/mpf_audit_integrity.py`
- `netbox_plant_graph/tests/test_v2_topology_integrity.py`
- `docs/v2_topology_integrity.md`

Expected result:

- Reusable checks for dark MPO position usage, fiber strand termination
  coherence, cable assembly reference validity, transport channel position map
  completeness/disjointness, transfer map sanity, and optical lane anchor
  consistency.
- Structured report with severity, code, object reference, message, and
  remediation hint.
- Management command with text and JSON output.

### Item 3: Generic Import/Reconciliation Pipeline

Owner: sub-agent Lagrange

Status: completed

Scope:

- `netbox_plant_graph/services/import_reconciliation.py` or
  `netbox_plant_graph/services/imports/`
- `netbox_plant_graph/management/commands/mpf_import_reconcile.py`
- `netbox_plant_graph/tests/test_v2_import_reconciliation.py`
- `docs/v2_import_reconciliation.md`

Expected result:

- Dry-run/apply import primitives for a safe initial subset:
  `CableAssembly`, `FiberStrand` cable linkage, `Endpoint`,
  `TransportChannel`, `TransportChannelPositionMap`, and `StrandTermination`.
- Create/update/skip/conflict diff outcomes.
- Idempotent apply for implemented safe actions.
- JSON-file management command with dry-run default and explicit apply flag.

## Follow-On Assignment Queue

Assign these as workers complete items 1-3. Prefer assigning the next item to
the first worker whose result is reviewed and integrated cleanly.

### Item 4: Stamping V2.5

Owner: sub-agent Cicero

Status: completed

Goal:

- Turn stamping from a mini-proof executor into a stronger fabric
  instantiation/reconciliation engine.

Likely scope:

- stamp preview/diff improvements,
- explicit create/bind/reconcile execution modes,
- stronger template validation UI/API,
- rollback plan clarity,
- name-pattern testing,
- partial rerun support for failed slices.

Suggested owner after completion:

- Prefer the architecture-schema worker if item 1 lands cleanly, because schema
  validation is the natural input to stronger stamping.

### Item 5: Operational Impact Modeling

Owner: sub-agent Newton

Status: completed

Goal:

- Make blast-radius output more useful to operators and adjacent automation.

Likely scope:

- severity tiers,
- impacted transport channels and optical lanes,
- impacted remote devices grouped by cabinet/device/interface,
- saved/exportable reports,
- scenario comparisons for cable cut vs OSFP unseat vs connector unplug.

Suggested owner after completion:

- Prefer the topology-integrity worker if item 2 lands cleanly, because the same
  structured reporting patterns should carry over.

### Item 6: Visual Trace Reusable Component

Owner: sub-agent Lagrange

Status: completed

Goal:

- Consolidate the visual path renderer shared by `Path Query` and
  `Interface Fanout Trace` into a reusable, testable component.

Likely scope:

- shared JavaScript/layout module,
- deterministic SVG export tests,
- visual regression/golden fixture strategy,
- performance guardrails for larger fanouts,
- object hyperlink coverage for rendered elements.

Suggested owner after completion:

- Assign to whichever worker is free after item 4 or 5, unless the coordinator
  is actively modifying the same templates.

### Item 7: External Contract Cleanup

Owner: sub-agent Lagrange

Status: completed

Goal:

- Make automation contracts boring and explicit.

Likely scope:

- REST endpoint documentation,
- GraphQL examples,
- sample automation recipes,
- stable/experimental labeling,
- explicit mutation-vs-query guidance.

Suggested owner after completion:

- Assign to the import/reconciliation worker if item 3 lands cleanly, because
  import automation and public contracts should be documented together.

## Integration Rules

1. Keep standard object surfaces registry-driven.
2. Keep workflow pages hand-wired when they have custom operator behavior.
3. Do not reintroduce NetBox-native cable/path reliance for modeled fabrics.
4. Do not reintroduce `netbox_floorplan` runtime coupling.
5. Keep each worker slice focused and reviewable.
6. Run focused tests per slice; defer full-suite testing until several slices
   have landed.
7. Preserve unrelated local-dev and Madison script changes unless explicitly
   asked to clean them up.

## Review Gates

Before integrating each worker result:

1. Confirm changed files stay inside assigned ownership.
2. Confirm no unrelated user/local changes were reverted.
3. Run or review focused tests named by the worker.
4. Check `git diff --check`.
5. Verify docs explain the new behavior at operator/developer level.
6. Decide whether to assign the worker item 4, 5, 6, or 7.

## Verification Snapshot

Focused integration checks passed after all seven slices landed:

- `./devrun/test.sh netbox_plant_graph.tests.test_v2_architecture_schema netbox_plant_graph.tests.test_v2_import_reconciliation netbox_plant_graph.tests.test_v2_topology_integrity netbox_plant_graph.tests.test_v2_stamping_v25 netbox_plant_graph.tests.test_v2_impact_modeling netbox_plant_graph.tests.test_v2_ui.V2UITestCase.test_path_query_resolves_selected_lanes netbox_plant_graph.tests.test_v2_ui.V2UITestCase.test_interface_fanout_trace_renders_visual_path_for_selected_osfp netbox_plant_graph.tests.test_v2_ui.V2UITestCase.test_interface_fanout_trace_can_consolidate_by_200g_subinterface`
  passed 29 tests.
- `python3 -m py_compile` passed for the new/changed V2 service and management
  command files.
- `node --check netbox_plant_graph/static/netbox_plant_graph/fanout_trace.js`
  passed.
- `git diff --check` passed for the coordinated slice files.

Full plugin suite check after integration:

- `./devrun/test.sh netbox_plant_graph` passed 127 tests.
