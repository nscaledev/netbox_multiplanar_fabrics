# V2 High-Value Deepening Plan

Status: highest-value operator hardening pass complete; focused checks only in this pass
Last updated: 2026-05-22

## Purpose

The seven highest-value next moves have landed and passed the full plugin test
suite. This document defines the next polish/hardening wave: not new headline
features, but the work needed to make those seven slices feel complete,
operator-ready, and safe enough for production automation.

The emphasis is on fleshing out weak edges, documenting contracts, adding
operator affordances, and converting useful service nuclei into well-integrated
plugin behavior.

## Execution Snapshot

Completed in the first execution pass:

- Slice 1A service nucleus: persisted `FabricArchitecture` rows can be converted
  into the architecture schema validator without throwing server errors.
- Slice 1B service nucleus: architecture compatibility results now distinguish
  compatible, warning-only drift, and incompatible persisted contracts.
- Slice 1C: the architecture schema documentation now catalogs every emitted
  validation/compatibility code, with a test that fails when new codes are added
  without docs.
- Slice 2A service nucleus: topology integrity reports can be persisted as
  `OperationRun` snapshots through service code or `mpf_audit_integrity`.
- Slice 2B service nucleus: topology findings now have a catalog, remediation
  hints, affected workflow flags, path-blocking markers, and grouped summaries.
- Slice 2C service nucleus: `integrity_gate_for_fabric(...)` returns reusable
  pass/warn/fail gate results from fresh, supplied, or latest persisted reports.
- Slice 3A: import/reconcile now understands dependency-aware bundles,
  topological apply ordering, structured conflicts, and all-or-nothing apply
  transactions.
- Slice 3C service nucleus: import items now carry optional provenance and
  idempotency metadata into diffs, conflict details, and model metadata where
  supported.
- Slice 4A service nucleus: stamp preview now includes NetBox source-object
  create/update diffs and generated name-pattern samples with collision flags.
- Slice 4A architecture gate: stamp preview now includes a structured
  architecture preflight gate for fixture validity, persisted target validation,
  compatibility drift, and unsupported template architecture hints.
- Slice 5A/5B service nucleus: operational impact reports can be persisted as
  `OperationRun` snapshots and compared for common, scenario-specific, severity,
  and device deltas.
- Slice 5C API nucleus: REST impact-preview endpoints now cover cable assembly
  cut, MPO connector unplug, and OSFP transceiver unseat scenarios.
- Slice 6A/6C lightweight harness: visual trace now has syntax, include-hook,
  browser API, export, link, tooltip, and cable-overlay smoke tests.
- Slice 7A/7B/7C: external contracts now have smoke tests, examples, and a
  documented deprecation/versioning policy.

Completed in the second execution pass:

- Slice 1A UI: architecture detail pages now render schema validation,
  compatibility, error/warning rows, and channel-map summaries for persisted
  `FabricArchitecture` rows, including malformed rows.
- Slice 2A/2C UI: Audit Dashboard now persists topology-integrity runs, shows
  latest-by-fabric status, grouped drilldown, remediation context, and
  high-risk workflow warning banners on Path Query, Interface Fanout Trace, and
  Physical Cable Blast Radius.
- Slice 3B UI: Import Preview now supports paste/upload JSON dry-run, row-level
  diffs/conflicts, and explicit `APPLY` confirmation through the shared import
  reconciliation service.
- Slice 4B/4C service: stamp recovery now has a manifest-based compensation
  rollback preview/apply path and retry classification for retryable, blocked,
  and already-converged runs.
- Slice 5A/5B UI: Physical Cable Blast Radius can save operational impact
  snapshots, and Impact Reports can list, export, and compare saved reports.
- Slice 6A/6B/6C harness: visual trace now exposes deterministic diagnostic
  helpers for payload counts, rendered SVG counts, export sanity, and render
  signatures without changing the public rendering behavior.

Follow-on polish completed:

- Import/reconcile now has an architecture-aware preflight gate. Payloads may
  carry top-level architecture hints, schema contract hints, channel-map
  matrices, MPO position counts, and dark-position contracts. Incompatible
  hints block reconciliation before row-level writes; warning-only drift remains
  visible on the returned plan.
- The Stamp Template Execute UI now renders the expanded V2.5 preview sections:
  architecture gate, action counts, recovery posture, validation issues, change
  plan, and name-pattern samples.
- Visual trace now exposes `goldenSnapshot(container)` and a Node-backed
  rendered fixture test. This is not a pixel-diff browser job yet, but it
  creates a deterministic golden signature and rendered SVG count harness for
  future browser CI.

UI exposure coordination pass completed:

- Stamp Template Execute now exposes the V2.5 safety model as an operator
  preview: summary badges, architecture gate, validation issues, change plan,
  generated name samples, and recovery posture. Apply is disabled and explained
  when preview errors are present.
- Import Preview now separates architecture preflight conflicts from row-level
  reconciliation conflicts and exposes payload provenance, dependency summary,
  apply-order preview, row provenance, and dependency detail.
- Operations Center, Fabric detail pages, and Fabric Operations pages now show
  fabric readiness summaries with topology integrity state, architecture
  contract state, and drilldown links.
- Path Query and Interface Fanout Trace now use the shared visual trace card's
  diagnostics drawer. The renderer fills in render signature, payload counts,
  rendered structure counts, cable-span counts, and golden-harness readiness.

Highest-value operator hardening pass completed:

- Stamp Template Execute now applies through `apply_stamp_template_v25(...)`
  instead of the older direct execution path. Successful UI stamps persist the
  V2.5 operation key, replayable source-binding references, creation-option
  references, and a V2.5 preview summary on the resulting `StampRun`.
- `StampRun` detail pages expose V2.5 retry classification plus compensation
  rollback preview/apply controls. Retry replays stored source/creation
  references when the run is classified retryable; rollback still requires an
  explicit `ROLLBACK` confirmation.
- Import Preview can save dry-run reports and applied/replayed reports as
  `OperationRun` artifacts. Saved reports preserve the exact payload used for
  replay/apply, expose downloadable JSON, and add conflict-remediation rows for
  architecture gates, missing dependencies, and row conflicts.
- Topology integrity audits now promote findings into triageable `AuditEvent`
  records, refresh still-present findings, and auto-resolve topology findings
  that disappear on later runs. Operations Center can run integrity audits
  directly from the readiness table.
- Operations Center now lists recent import reports and publishes a workflow
  surface support matrix that labels routes as V2-supported, experimental, or
  legacy-hidden.
- Visual trace regression protection now includes a larger synthetic fanout
  fixture that asserts render structure, object-link coverage, SVG export
  health, cable overlay count, and a loose performance ceiling.

Verification from this pass:

- `node --check netbox_plant_graph/static/netbox_plant_graph/fanout_trace.js`
- `python3 -m py_compile` on touched Python services/views/tests
- Focused import reconciliation tests
- Focused UI tests for import preview and stamp execute
- Focused visual trace component contract tests
- Focused UI exposure tests for readiness, Path Query diagnostics, Interface
  Fanout diagnostics, Import Preview preflight/provenance, and Stamp Execute
  V2.5 blocking behavior
- Focused UI/URL module checks after the operator hardening pass:
  `./devrun/test.sh netbox_plant_graph.tests.test_v2_ui netbox_plant_graph.tests.test_v2_urls`
- Focused visual trace component contract checks after the operator hardening
  pass: `./devrun/test.sh netbox_plant_graph.tests.test_v2_visual_trace_component`
- `node --check netbox_plant_graph/static/netbox_plant_graph/fanout_trace.js`
- `python3 -m py_compile` on touched Python view, URL, and test modules
- `git diff --check`

## Execution Model

Use this as a handoff-ready plan for sub-agents or focused implementation
branches. Keep each slice independently reviewable.

1. Prefer extending the service added by the original item instead of creating a
   parallel implementation.
2. Add tests at the service layer first, then add UI/API tests only where a new
   operator or automation surface is exposed.
3. Preserve plugin-native cable/path semantics. Do not reintroduce NetBox-native
   cables, paths, or floorplan runtime dependencies.
4. Keep registry-driven CRUD pages registry-driven; hand-wire workflow pages.
5. For every new automation contract, document stable vs experimental behavior
   in `docs/v2_external_contracts.md`.
6. Run focused tests per slice. Run `./devrun/test.sh netbox_plant_graph` after
   a group of related slices lands.

## Priority Map

P0 work should come first because it reduces the chance of trusting bad
topology data:

- P0-A: Architecture schema lifecycle and database preflight integration.
- P0-B: Topology audit persistence, UI surfacing, and gateable output.
- P0-C: Import/reconcile transaction bundles, dependency ordering, and conflict
  remediation.
- P0-D: Stamp V2.5 preview/apply/rollback clarity and stronger execution
  safety.

P1 work makes the operator experience more useful and reusable:

- P1-A: Operational impact modeling API/UI depth and report persistence.
- P1-B: Visual trace component testability, performance, and hyperlink
  completeness.

P2 work makes the plugin safer for external consumers:

- P2-A: External contract examples, versioning discipline, and smoke-testable
  recipes.

## Item 1: Architecture Schema Hardening Deepening

Current state:

- `netbox_plant_graph.services.architecture_schema` validates the built-in
  `roce-4-plane-gb300-2x2-shuffle` fixture as structured topology data.
- Validation covers roles, transfer patterns, allocation rules, channel maps,
  active/dark MPO positions, and 2x2 shuffle invariants.
- Documentation exists in `docs/v2_architecture_schema.md`.

Remaining gaps worth attention:

- Validation is still mostly fixture/service oriented. The database-facing
  architecture detail page and stamp/import workflows do not yet treat schema
  validation as a first-class preflight gate.
- Error codes are useful, but not yet organized into a versioned public error
  catalog.
- Future architecture definitions do not have a formal migration/versioning
  lifecycle.
- The schema contract does not yet describe compatibility between persisted
  `FabricArchitecture.schema`, stamp templates, and imported topology data.

### Slice 1A: Persisted Architecture Preflight

Goal: Make persisted `FabricArchitecture` rows visibly and programmatically
schema-valid or schema-invalid.

Implementation scope:

- Add a service wrapper that validates a `FabricArchitecture` row from persisted
  schema fields and returns the existing `ArchitectureSchemaValidationResult`.
- Add architecture detail-page sections for schema validity, error count, schema
  version, built-in fixture compatibility, and channel-map summary.
- Add a lightweight action on the architecture page or an API-readable helper
  payload that returns current validation results without mutating data.

Acceptance criteria:

- Viewing a built-in RoCE architecture shows a passing validation summary.
- A malformed architecture row can render all validation errors without throwing
  a server error.
- Stamp preview can include architecture preflight failures with the same error
  codes surfaced on the architecture detail page.

Tests:

- Service test for persisted architecture validation.
- UI test for architecture detail page pass/fail sections.
- Focused stamp preview test proving architecture errors block apply.

### Slice 1B: Architecture Compatibility Contract

Goal: Make architecture/schema version compatibility explicit before future
architectures are added.

Implementation scope:

- Introduce a small compatibility helper that compares architecture slug,
  architecture version, schema contract version, channel map matrix, and MPO
  position contract.
- Use it from stamp preview and import/reconcile where relevant.
- Document valid compatibility outcomes: compatible, warning-only drift,
  incompatible.

Acceptance criteria:

- Stamp templates targeting the wrong architecture version fail with a clear
  compatibility issue.
- Import payloads that imply an incompatible channel map can be rejected before
  row-level reconciliation.
- `docs/v2_architecture_schema.md` includes compatibility semantics.

Tests:

- Unit tests for compatible, drift, and incompatible cases.
- Import/reconcile dry-run test for an incompatible architecture hint.

### Slice 1C: Error Catalog And Extensibility Notes

Goal: Make validation failures stable enough for operators and automation.

Implementation scope:

- Add an error-code table to `docs/v2_architecture_schema.md`.
- Group codes by role, channel map, active/dark position, transfer pattern, and
  shuffle invariant.
- Add a short "Adding a new architecture" checklist that distinguishes data-only
  additions from additions that require new validator logic.

Acceptance criteria:

- Every emitted architecture schema error code appears in the docs.
- The docs state which error codes are stable enough for external automation and
  which may expand.

Tests:

- Lightweight test that all emitted built-in malformed-fixture codes are listed
  in the error catalog, or a documented manual check if test indirection would
  be more brittle than useful.

## Item 2: Topology Integrity Audit Pack Deepening

Current state:

- `netbox_plant_graph.services.topology_integrity` provides structured audits.
- `mpf_audit_integrity` emits text/JSON and can fail on a severity threshold.
- Checks cover dark MPO position use, strand terminations, cable assembly
  references, channel position maps, transfer maps, and optical lane anchors.

Remaining gaps worth attention:

- Audit output is command/service oriented; it is not yet a first-class
  operator workflow with saved runs and drill-down.
- Finding codes are experimental and not yet cataloged.
- No baseline/suppression integration exists for topology-integrity findings.
- The audit does not yet offer machine-friendly remediation grouping or
  affected-workflow hints.

### Slice 2A: Persistent Integrity Runs

Goal: Let operators compare and revisit topology integrity audit results.

Implementation scope:

- Add a persistent `OperationRun` or dedicated audit-run wrapper for topology
  integrity execution without duplicating the existing audit lifecycle models.
- Store JSON report payloads and summary counts.
- Add a UI entry point under the Audit menu for latest integrity status by
  fabric.

Acceptance criteria:

- Running integrity audit from UI or command can persist a report.
- The latest report for a fabric shows severity counts and finding links.
- JSON command output remains backward compatible.

Tests:

- Service test for persisted run creation.
- UI test for integrity summary page.
- Command test for persisted vs non-persisted mode if a new flag is added.

### Slice 2B: Finding Catalog, Remediation, And Grouping

Goal: Make findings actionable instead of just technically correct.

Implementation scope:

- Add a finding-code catalog with severity, affected object kinds, likely cause,
  remediation hint, and affected workflows.
- Group findings by object family: architecture, cable plant, endpoints,
  channels, lanes, transfer maps.
- Add helper output grouping so UI/API consumers do not reimplement grouping.

Acceptance criteria:

- Every emitted topology integrity code is documented.
- UI and JSON can show grouped findings and remediation hints.
- Findings that make path tracing unsafe are explicitly marked as path-blocking.

Tests:

- Unit test that emitted finding codes are cataloged.
- Service test for grouped summary.

### Slice 2C: Audit Gate Integration

Goal: Let stamping/import/operator workflows ask, "is this topology safe enough
to trust?"

Implementation scope:

- Add a reusable `integrity_gate_for_fabric(...)` helper that runs or reads the
  latest report and returns pass/warn/fail with blocking findings.
- Use it optionally from stamp apply, import apply, path query, fanout trace,
  and blast-radius pages as non-invasive warnings.
- Add a setting or parameter to choose fail-hard vs warn-only behavior for
  automation.

Acceptance criteria:

- Path and blast-radius pages show a clear warning if latest integrity status is
  failing.
- Import apply can be configured to stop when it would leave blocking findings.
- Existing pages keep working when no audit has been run.

Tests:

- UI test for warning banner.
- Service tests for pass/warn/fail gate behavior.

## Item 3: Generic Import/Reconciliation Pipeline Deepening

Current state:

- `netbox_plant_graph.services.imports` supports dry-run/apply for cable
  assemblies, strand cable linkage, endpoints, transport channels, channel
  position maps, and strand terminations.
- `mpf_import_reconcile` supports JSON input, dry-run default, apply, JSON
  output, and fail-on-conflict.

Remaining gaps worth attention:

- The engine now supports dependency-aware bundles and transactional apply, but
  remediation text can be more operator-prescriptive.
- Conflict messages are useful but do not yet include structured remediation.
- The operator Import Preview UI exists, including architecture preflight,
  provenance, dependency summary, and apply-order preview; dry-run report
  download/persistence is still a follow-on gap.
- Import payload versioning and source provenance exist, but need more
  end-to-end source-document examples.

### Slice 3A: Transaction Bundle And Dependency Planner

Goal: Make imports safe when payloads contain interdependent rows.

Implementation scope:

- Add an import bundle model in service code with payload version, source label,
  item order, dependency edges, and prerequisite resolution.
- Sort apply order when possible; flag impossible dependency cycles as
  conflicts.
- Wrap apply in a transaction by default with an explicit partial-apply mode
  only if needed later.

Acceptance criteria:

- A payload can create/update dependent supported kinds in one apply run when
  prerequisites are present or included earlier in the bundle.
- Missing prerequisites report structured conflicts with dependency details.
- Apply either commits all rows or none for the default mode.

Tests:

- Dependency-order test.
- Missing-prerequisite conflict test.
- Transaction rollback test when a later row fails.

### Slice 3B: Import Preview UI

Goal: Give operators a browser-native way to inspect and apply import diffs.

Implementation scope:

- Add a workflow page for uploading/pasting import JSON.
- Show summary counts, row-level create/update/skip/conflict, and grouped
  conflicts.
- Require an explicit apply confirmation.
- Persist or allow download of dry-run and applied reports.

Acceptance criteria:

- Operator can dry-run a payload without shell access.
- Conflicts are visible before apply.
- Apply path uses the same service as the command.

Tests:

- UI tests for dry-run and apply paths.
- Service tests stay authoritative for reconciliation behavior.

### Slice 3C: Source Provenance And Idempotency Keys

Goal: Make repeated imports explainable and auditable.

Implementation scope:

- Add optional payload fields: `source_system`, `source_document`, `source_row`,
  `external_id`, and `idempotency_key`.
- Store provenance in managed object metadata where appropriate.
- Include provenance in conflict/diff messages.

Acceptance criteria:

- Reapplying a payload with stable idempotency keys is deterministic.
- Operators can trace a conflicting row back to its source document/row.
- Existing payloads remain valid without provenance fields.

Tests:

- Idempotency-key test.
- Provenance propagation test for at least cable assemblies and strand
  terminations.

## Item 4: Stamping V2.5 Deepening

Current state:

- `preview_stamp_template_v25(...)` performs dry-run validation and change
  planning.
- `apply_stamp_template_v25(...)` blocks invalid previews and delegates to the
  existing idempotent stamp executor.
- `rollback_stamp_run_v25(...)` documents rollback but does not automate it.

Remaining gaps worth attention:

- Preview changes are broad but not yet a complete operator diff of all managed
  object mutations.
- Rollback is documented, but no compensation engine exists.
- Partial-rerun/resume semantics are not visible enough for failed stamp slices.
- Stamp preview UI now exposes action counts, architecture gate results,
  validation issues, recovery posture, change plan, and name-pattern samples.
  Remaining UI depth is mostly around full managed-object diff coverage and
  explicit retry/rollback actions from saved stamp runs.

### Slice 4A: Complete Preview Diff And Name Tests

Goal: Make stamp preview believable before an operator presses execute.

Implementation scope:

- Expand preview to include all object families the executor may create/update,
  including NetBox device/interface creation when enabled.
- Add a name-pattern test panel that shows sample generated names and
  collisions.
- Show architecture compatibility, creation option validity, and source binding
  validity in separate sections.

Acceptance criteria:

- Preview lists every meaningful object family touched by apply.
- Name collisions identify the conflicting existing object.
- Operators can distinguish warnings from apply-blocking errors.

Tests:

- Service test for preview object family coverage.
- UI test for name-pattern/collision display.

### Slice 4B: Managed Object Manifest And Compensation Rollback

Goal: Move rollback from "documented limitation" to a constrained, auditable
operation.

Implementation scope:

- Normalize `StampRun.result.managed_objects` into a manifest schema containing
  model label, primary key, natural key, action, and ownership marker.
- Add a compensation planner that can propose delete/revert operations only for
  plugin-owned objects created by the stamp.
- Keep destructive rollback behind explicit confirmation and block rollback
  when ownership is ambiguous or downstream objects now depend on the target.

Acceptance criteria:

- Rollback preview shows what would be deleted, skipped, or blocked.
- Apply rollback can remove clearly owned plugin-created rows.
- Rollback never deletes unrelated user-created NetBox objects.

Tests:

- Service test for rollback planning.
- Service test for dependency-blocked rollback.
- UI/API test for rollback preview and explicit apply.

### Slice 4C: Retry And Partial Rerun UX

Goal: Make failed stamps recoverable without manual database spelunking.

Implementation scope:

- Add explicit retry states: retryable, blocked, already-converged.
- Store failure slice context in `StampRun.result`.
- Add an operator action to re-preview and rerun using the original inputs.

Acceptance criteria:

- Failed stamp runs show whether retry is safe.
- Retry uses the same validation gates as a fresh apply.
- Idempotent reruns do not duplicate topology rows.

Tests:

- Failed-run retry classification tests.
- UI test for retry action visibility.

## Item 5: Operational Impact Modeling Deepening

Current state:

- `netbox_plant_graph.services.impact_modeling` models cable assembly cuts, MPO
  connector unplugs, and OSFP transceiver unseats.
- Reports include simulated components, impacted paths/lanes/channels/endpoints,
  impacted devices, and a site/rack/device hierarchy.
- The Physical Cable Blast Radius page uses the service.

Remaining gaps worth attention:

- Reports are transient; operators cannot save, compare, export, or attach them
  to maintenance records.
- Scenario comparison is not yet first-class.
- The UI can better expose why a device is failed/degraded/at-risk/low impact.
- No stable REST impact-summary endpoint is documented.

### Slice 5A: Saved Impact Reports

Goal: Preserve blast-radius analyses as operator artifacts.

Implementation scope:

- Persist report snapshots as `OperationRun` results or a dedicated impact
  report model if the existing run model is too generic.
- Add report name, scenario type, target object refs, actor, created timestamp,
  and JSON payload.
- Add detail page with summary, hierarchy, impacted object lists, and links to
  path drilldown.

Acceptance criteria:

- Operators can save a calculated impact report.
- Saved reports are immutable snapshots even if topology changes later.
- Reports can be exported as JSON.

Tests:

- Service test for report persistence.
- UI test for saved report detail page.

### Slice 5B: Scenario Comparison

Goal: Let operators compare maintenance/failure options.

Implementation scope:

- Add a comparison service that accepts two or more scenario reports and returns
  common impacts, scenario-specific impacts, and severity deltas.
- Expose comparison in the blast-radius page after multiple scenarios are run.
- Include cable cut vs OSFP unseat vs connector unplug examples.

Acceptance criteria:

- UI can show "same impact" and "additional impact" groupings.
- Comparison output is deterministic and JSON-serializable.
- Scenario comparison does not mutate topology.

Tests:

- Service tests for common-only, scenario-only, and severity-delta cases.
- UI test for comparison summary.

### Slice 5C: Stable Impact API

Goal: Give automation a supported way to request impact summaries without
scraping the operator page.

Implementation scope:

- Add REST endpoints for impact preview by scenario:
  cable assembly cut, MPO connector unplug, OSFP transceiver unseat.
- Return stable top-level keys aligned with `OperationalImpactReport.as_dict()`.
- Document the endpoint as experimental or stable in
  `docs/v2_external_contracts.md`.

Acceptance criteria:

- API can model each existing scenario.
- Payload validation returns useful 400 errors for missing/invalid targets.
- Docs include curl examples.

Tests:

- API tests for all three scenarios.
- Permission/authentication behavior follows NetBox plugin API patterns.

## Item 6: Visual Trace Reusable Component Deepening

Current state:

- Path Query and Interface Fanout Trace share reusable visual trace includes and
  `fanout_trace.js`.
- The renderer supports collapsible sections, SVG export, object hyperlinks,
  tooltips, cable assembly cylinders/braces, lane coloring, and deterministic
  render signatures.

Remaining gaps worth attention:

- The component is reusable and has deterministic diagnostic hooks plus a
  collapsed operator diagnostics drawer, but not yet full browser/golden tests.
- Large fanouts need explicit performance guardrails.
- Hyperlink coverage should be measured rather than trusted.
- SVG export should be validated for browser-independent correctness.
- The renderer is still one large JavaScript file; internal boundaries can be
  cleaned up carefully without changing UI behavior.

### Slice 6A: Browser Regression Harness

Goal: Protect the visual language from accidental layout regressions.

Implementation scope:

- Add Playwright or NetBox-compatible browser checks for Path Query and
  Interface Fanout Trace.
- Capture render signatures and a small set of structural SVG assertions:
  section count, connector count, cable-assembly cylinder count, object link
  count, and nonzero geometry.
- Avoid brittle pixel-perfect tests initially.

Acceptance criteria:

- Browser tests catch blank SVGs, missing sections, missing export button, and
  missing object links.
- Tests run against seeded fixture data or a deterministic test fixture.

Tests:

- New browser/e2e test target if the repo already has one; otherwise documented
  manual harness plus unit-level JS checks.

### Slice 6B: Renderer Internal Modules

Goal: Make `fanout_trace.js` easier to maintain without changing behavior.

Implementation scope:

- Split logical sections inside the file or into small modules if the asset
  pipeline supports it: data normalization, layout, SVG primitives, braces,
  cables, hyperlinks/tooltips, export.
- Keep the public `window.NetBoxPlantGraphVisualTrace` API intact.
- Add `node --check` and targeted pure-function tests where feasible.

Acceptance criteria:

- Existing UI behavior and render signatures remain stable.
- New code has named functions for layout rules that operators have been tuning.
- Future layout tweaks require changing a small isolated function.

Tests:

- `node --check`.
- Focused JS unit tests if a test harness is introduced.
- Existing UI tests still pass.

### Slice 6C: Hyperlink And Export Completeness

Goal: Make every rendered object/link/export behavior auditable.

Implementation scope:

- Create a renderer-side link coverage report for endpoints, connector
  positions, lanes, channels, interfaces, cable assemblies, and relevant NetBox
  devices.
- Add an SVG export sanity checker that confirms links, titles/tooltips,
  dimensions, and visible content survive export.
- Document which visual elements are intentionally not linked.

Acceptance criteria:

- Link coverage appears in test logs or a deterministic render signature.
- Exported SVG opens as standalone markup and retains object URLs.
- Missing URLs degrade gracefully without JavaScript errors.

Tests:

- UI/browser test for link coverage.
- Export markup test through the renderer public API.

## Item 7: External Contract Cleanup Deepening

Current state:

- `docs/v2_external_contracts.md` labels REST, GraphQL, import/reconcile,
  topology audit, visual pages, private services, and local scripts as stable,
  experimental, or non-contract.
- REST mutation endpoints and GraphQL contract breaks are documented.

Remaining gaps worth attention:

- Contract docs are not yet backed by contract tests.
- Examples are useful but not complete enough to become a script starter kit.
- Versioning/deprecation policy needs more explicit operational procedure.
- GraphQL and REST payload samples should be generated or checked against the
  current code.

### Slice 7A: Contract Smoke Tests

Goal: Make documented routes and top-level payload keys fail loudly when they
  drift.

Implementation scope:

- Add tests for documented REST query/mutation endpoint existence.
- Add tests for GraphQL contract version and minimal query set.
- Add command tests for documented `mpf_audit_integrity` and
  `mpf_import_reconcile` options.
- Avoid over-testing exact prose or volatile nested payload details.

Acceptance criteria:

- Removing or renaming a documented stable route fails tests.
- Contract version is asserted.
- Stable envelope keys are asserted for audit/import/path-query surfaces.

Tests:

- New `test_v2_external_contracts.py` or equivalent focused test module.

### Slice 7B: Automation Recipe Kit

Goal: Give operators ready-to-adapt examples for common external workflows.

Implementation scope:

- Add `docs/examples/` with curl examples, sample import JSON, and short Python
  snippets for stamp/validate/audit/import/impact flows.
- Add a README that states which examples are stable vs experimental.
- Ensure examples avoid local-only Madison assumptions unless clearly labeled.

Acceptance criteria:

- A new operator can follow examples to preview a stamp, run an import dry-run,
  audit integrity, and resolve a path.
- Examples use documented stable surfaces where possible.
- Experimental examples are labeled.

Tests:

- JSON examples parse.
- Shell snippets are syntax-checked where practical.

### Slice 7C: Deprecation And Versioning Policy

Goal: Prevent accidental contract breaks as V2 keeps moving.

Implementation scope:

- Define when `graphql_contract_version` changes.
- Define how REST endpoint changes are announced and how aliases are retained.
- Add a short release-note checklist for stable-contract changes.

Acceptance criteria:

- Docs say how long route aliases remain supported.
- Docs distinguish additive payload fields from breaking payload changes.
- PR/review checklist includes contract-impact review.

Tests:

- Documentation-only unless a release/checklist test pattern exists.

## Recommended Work Order

1. Item 1A and Item 2A together: architecture and integrity results become
   visible operator guardrails.
2. Item 3A and Item 4A together: import and stamp previews become trustworthy
   before mutation.
3. Item 2C after 1A/2A/3A: wire integrity gates into high-risk workflows.
4. Item 4B/4C: make stamp recovery real once preview semantics are more
   complete.
5. Item 5A/5B/5C: make impact modeling useful beyond a one-off page render.
6. Item 6A/6B/6C: lock down the visual trace work after current layout behavior
   is stable.
7. Item 7A/7B/7C: convert docs into tested contracts and operator recipes.

## Suggested Sub-Agent Assignments

These assignments preserve the affinity from the first wave:

- Architecture/Stamps worker: Items 1A, 1B, 4A, 4B, 4C.
- Audit/Impact worker: Items 2A, 2B, 2C, 5A, 5B, 5C.
- Import/Contracts/Visual worker: Items 3A, 3B, 3C, 6A, 6B, 6C, 7A, 7B, 7C.

When multiple agents are active, keep write ownership disjoint:

- Architecture/Stamps worker owns `services/architecture_schema.py`,
  `services/stamping_v25.py`, stamp templates/views/tests/docs.
- Audit/Impact worker owns `services/topology_integrity.py`,
  `services/impact_modeling.py`, integrity/blast-radius views/tests/docs.
- Import/Contracts/Visual worker owns `services/imports/`, import commands,
  visual trace includes/static assets, external contract docs/tests.

## Verification Gates

Run these checks before merging a completed group:

```bash
python3 -m py_compile \
  netbox_plant_graph/services/architecture_schema.py \
  netbox_plant_graph/services/topology_integrity.py \
  netbox_plant_graph/services/imports/reconciliation.py \
  netbox_plant_graph/services/stamping_v25.py \
  netbox_plant_graph/services/impact_modeling.py

node --check netbox_plant_graph/static/netbox_plant_graph/fanout_trace.js

./devrun/test.sh netbox_plant_graph.tests.test_v2_architecture_schema \
  netbox_plant_graph.tests.test_v2_topology_integrity \
  netbox_plant_graph.tests.test_v2_import_reconciliation \
  netbox_plant_graph.tests.test_v2_stamping_v25 \
  netbox_plant_graph.tests.test_v2_impact_modeling

git diff --check
```

Run the full plugin suite after any P0 group lands:

```bash
./devrun/test.sh netbox_plant_graph
```

## Definition Of Done

This deepening wave is complete when:

- Architecture validity is visible and reusable as a preflight gate.
- Topology integrity has persisted, grouped, operator-actionable results.
- Import/reconcile can safely process dependency-aware bundles with transaction
  semantics.
- Stamp preview is complete enough to trust, and rollback has a constrained
  compensation path.
- Operational impact reports can be saved, compared, exported, and requested by
  a documented API.
- Visual trace has browser-level regression coverage and measured link/export
  completeness.
- Stable external contracts are backed by smoke tests and examples.
