# V2 Gap Closure Execution Plan (Delegation-Ready)

Status: implementation-complete execution record
Last updated: 2026-05-21
Source gap analysis: [v2_gap_analysis_original_vs_v2.md](/Users/mencken/github-repos/netbox_multiplanar_fabrics/docs/v2_gap_analysis_original_vs_v2.md)

## Objective

Close every gap identified in the source analysis, in strict priority order, while preserving V2 data-model decisions and the hybrid architecture:

- Registry-driven standard CRUD/API pages.
- Hand-wired workflow pages and workflow actions.
- No floorplan plugin runtime dependency.

## Progress Snapshot (2026-05-20)

Completed slices:

- P0-A complete: audit lifecycle + triage/policy/plane workflows restored.
- P0-B complete: lane drilldown/compare/blast-radius/path-resolver workflows restored.
- P1-A complete: stamping/template/deployment workflows restored.
- P1-B complete: fabric visibility/onboarding/ops workflows and grouped navigation restored.
- P2 complete: REST + GraphQL operational surfaces expanded for restored workflows.

Focused test evidence (latest):

- `./devrun/test.sh netbox_plant_graph.tests` -> PASS (88 tests, 2026-05-20).
- `./devrun/test.sh netbox_plant_graph.tests.test_v2_api netbox_plant_graph.tests.test_v2_graphql netbox_plant_graph.tests.test_v2_urls` -> PASS (24 tests, 2026-05-20).
- `./devrun/test.sh netbox_plant_graph.tests.test_v2_api netbox_plant_graph.tests.test_v2_graphql netbox_plant_graph.tests.test_v2_urls netbox_plant_graph.tests.test_v2_ui netbox_plant_graph.tests.test_v2_plan_execution netbox_plant_graph.tests.test_v2_resolver netbox_plant_graph.tests.test_v2_audit_services` -> PASS (56 tests, 2026-05-20).
- `./devrun/test.sh netbox_plant_graph.tests.test_v2_urls netbox_plant_graph.tests.test_v2_ui` -> PASS (25 tests, 2026-05-20).
- Prior focused gate remains green for:
  - `netbox_plant_graph.tests.test_v2_plan_execution`
  - `netbox_plant_graph.tests.test_v2_resolver`
  - `netbox_plant_graph.tests.test_v2_audit_services`

Current active slice:

- None. This document is now a completed execution record. Later UX changes
  such as menu consolidation, first-class cable visualization, and fanout/path
  visual polish are tracked by current code and the refreshed docs, not this
  original gap-closure queue.

## Post-Closure Delta Follow-ups (2026-05-20)

Completed additional baseline-parity deltas after refresh gap analysis:

1. Reintroduced API mutation endpoints for:
   - workflow finding lifecycle actions,
   - disjointness exception request/approve/expire/reactivate,
   - stamp template execute and stamp run rollback.
2. Added legacy route-name alias for stamp preview (`stamp-preview`) alongside `stamps-preview`.
3. Formally versioned and documented GraphQL V2 contract breaks in:
   - [v2_graphql_contract_v2.md](/Users/mencken/github-repos/netbox_multiplanar_fabrics/docs/v2_graphql_contract_v2.md)

## Execution Model (Coordinator + Sub-Agents)

Coordinator responsibilities:

1. Own backlog order, merge order, and acceptance gates.
2. Assign disjoint file ownership to sub-agents.
3. Run integration passes after each priority slice.
4. Keep route/menu/view/test parity always green before advancing.

Sub-agent rules:

1. Stay within assigned file ownership.
2. Do not revert or rewrite unrelated files.
3. Add or update focused tests with each slice.
4. Return a short change report: files touched, behavior delivered, tests run.

## Global Constraints

1. Priority order is mandatory: P0-A -> P0-B -> P1-A -> P1-B -> P2.
2. Do not run full suite until P1-B is integrated.
3. Every restored route must have:
   - URL pattern,
   - view implementation,
   - template,
   - menu placement if user-facing,
   - focused URL/UI tests.
4. Workflow pages remain hand-wired even if data tables stay registry-generated.
5. Floorplan references remain forbidden in runtime code paths.

## Priority Queue and Delivery Slices

## P0-A: Audit Lifecycle and Triage Workflows

Gaps closed:

- `audit_dashboard`, `audit_triage`, `policy_review`, `plane_audit`
- `audit_finding_acknowledge`
- `audit_finding_start_remediation`
- `audit_finding_suppress`
- `audit_finding_unsuppress`
- `audit_finding_resolve`
- `audit_finding_reopen`
- `disjointness_exception_request`
- `disjointness_exception_approve`
- `disjointness_exception_expire`
- `disjointness_exception_reactivate`

Target files:

- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/urls.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/views.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/forms.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/services/audit.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/templates/netbox_plant_graph/audit_dashboard.html`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/templates/netbox_plant_graph/audit_triage.html`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/templates/netbox_plant_graph/policy_review.html`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/templates/netbox_plant_graph/plane_audit.html`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/templates/netbox_plant_graph/disjointness_exception_request.html`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/tests/test_v2_urls.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/tests/test_v2_ui.py`

Sub-agent packet A1 (routing + lifecycle actions):

- Ownership:
  - `urls.py`
  - action views and helper methods in `views.py`
- Deliver:
  - restore missing route names and stable reverse names.
  - implement POST actions for finding lifecycle and exception lifecycle.
  - ensure each action writes `AuditEvent`.
- Tests:
  - URL reverse coverage in `test_v2_urls.py`.
  - action status transition coverage in `test_v2_ui.py`.

Sub-agent packet A2 (audit/policy pages):

- Ownership:
  - read-only workflow views in `views.py`
  - templates listed above
- Deliver:
  - restore triage/dashboard/review pages with current V2 model adapters.
  - preserve floorplan-independent behavior.
- Tests:
  - HTML render coverage + key content assertions in `test_v2_ui.py`.

Sub-agent packet A3 (forms/service contracts):

- Ownership:
  - `forms.py`
  - `services/audit.py`
- Deliver:
  - lifecycle transition form validation and policy/triage query helpers.
  - deterministic state machine enforcement for finding transitions.
- Tests:
  - focused form/service tests added to existing test modules.

Coordinator integration gate for P0-A:

1. Merge A3 -> A1 -> A2.
2. Run:
   - `manage.py test netbox_plant_graph.tests.test_v2_urls --verbosity 2 --noinput`
   - `manage.py test netbox_plant_graph.tests.test_v2_ui --verbosity 2 --noinput`
3. Manual smoke:
   - load each restored page from plugin nav.
   - execute one finding lifecycle from acknowledge to resolve.

Exit criteria:

1. All P0-A routes reverse and render.
2. Finding/exception transitions operate through UI actions.
3. Audit trail records each transition.

## P0-B: Advanced Lane Analysis Workflows

Gaps closed:

- `path_resolver` parity uplift on top of current `path_query`
- `lane_drilldown`
- `lane_compare`
- `blast_radius`

Target files:

- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/urls.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/views.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/services/resolver.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/templates/netbox_plant_graph/path_query.html`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/templates/netbox_plant_graph/lane_drilldown.html`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/templates/netbox_plant_graph/lane_compare.html`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/templates/netbox_plant_graph/blast_radius.html`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/tests/test_v2_resolver.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/tests/test_v2_ui.py`

Sub-agent packet B1 (service layer parity):

- Ownership:
  - `services/resolver.py`
  - optional small helper modules under `services/graph/`
- Deliver:
  - reusable query primitives for drilldown, compare, and blast radius.
  - preserve arbitrary-hop traversal behavior.
- Tests:
  - resolver-focused tests in `test_v2_resolver.py`.

Sub-agent packet B2 (workflow views and URLs):

- Ownership:
  - `urls.py`
  - lane-analysis views in `views.py`
- Deliver:
  - restore lane-analysis routes and wire view context to B1 service outputs.
- Tests:
  - URL reverse assertions and render status checks.

Sub-agent packet B3 (templates + UX parity):

- Ownership:
  - `lane_drilldown.html`
  - `lane_compare.html`
  - `blast_radius.html`
  - `path_query.html` uplift where needed
- Deliver:
  - operator-usable tables/panels with linkouts to V2 objects.
- Tests:
  - content assertions in `test_v2_ui.py`.

Coordinator integration gate for P0-B:

1. Merge B1 -> B2 -> B3.
2. Run:
   - `manage.py test netbox_plant_graph.tests.test_v2_resolver --verbosity 2 --noinput`
   - `manage.py test netbox_plant_graph.tests.test_v2_ui --verbosity 2 --noinput`
3. Manual smoke:
   - run one lane drilldown, one lane compare, one blast radius query from UI.

Exit criteria:

1. Restored lane-analysis pages are navigable and non-placeholder.
2. Results include path/hop context and lane-level details.
3. Query behavior is stable on multi-hop fabrics.

## P1-A: Stamping, Templates, and Deployment Workflows

Gaps closed:

- `assembly_stamp_wizard`
- `assembly_graph_stamp_wizard`
- `assembly_template_build`
- `breakout_stamp_wizard`
- `spatial_stamp_wizard`
- `spatial_template_compose`
- `connection_template_builder`
- `rack_population_stamp_wizard`
- `deployment_plan_execute`
- `deployment_plan_rollback`
- `deployment_plan_workflow`
- `template_library`

Target files:

- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/urls.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/views.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/forms.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/services/assembly_stamp.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/services/spatial_stamp.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/services/rack_population_stamp.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/services/plan_execution.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/services/stamp_preview.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/templates/netbox_plant_graph/assembly_stamp_wizard.html`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/templates/netbox_plant_graph/assembly_graph_stamp_wizard.html`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/templates/netbox_plant_graph/assembly_template_build.html`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/templates/netbox_plant_graph/breakout_stamp_wizard.html`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/templates/netbox_plant_graph/spatial_stamp_wizard.html`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/templates/netbox_plant_graph/spatial_template_compose.html`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/templates/netbox_plant_graph/connection_template_builder.html`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/templates/netbox_plant_graph/rack_population_stamp_wizard.html`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/templates/netbox_plant_graph/deployment_plan_workflow.html`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/templates/netbox_plant_graph/template_library.html`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/tests/test_v2_stamping.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/tests/test_v2_ui.py`

Sub-agent packet C1 (template and wizard workflows):

- Ownership:
  - wizard/template URLs and views
  - wizard/template templates
- Deliver:
  - restore template-library and template-build flows.
  - restore assembly/breakout/spatial/rack stamp wizards.
- Tests:
  - render + submit success path for each wizard class.

Sub-agent packet C2 (deployment plan operations):

- Ownership:
  - `services/plan_execution.py`
  - deployment workflow views and template
- Deliver:
  - restore plan execute/rollback workflow path.
  - ensure operation provenance records run IDs and outcomes.
- Tests:
  - execute/rollback flow coverage in `test_v2_stamping.py` and `test_v2_ui.py`.

Sub-agent packet C3 (service hardening and previews):

- Ownership:
  - `stamp_preview.py`, `assembly_stamp.py`, `spatial_stamp.py`, `rack_population_stamp.py`
- Deliver:
  - restore preview-driven validation and idempotent stamping boundaries.
  - ensure compatibility with current V2 models and source anchoring.
- Tests:
  - focused service tests in `test_v2_stamping.py`.

Coordinator integration gate for P1-A:

1. Merge C3 -> C1 -> C2.
2. Run:
   - `manage.py test netbox_plant_graph.tests.test_v2_stamping --verbosity 2 --noinput`
   - `manage.py test netbox_plant_graph.tests.test_v2_ui --verbosity 2 --noinput`
3. Manual smoke:
   - one template build -> one stamp wizard run -> one deployment execute -> one rollback.

Exit criteria:

1. Core stamping/deployment workflows are usable from UI.
2. Preview/validation exists before mutation.
3. Execution and rollback emit provenance and audit events.

## P1-B: Fabric Visibility, Onboarding, and Ops Flows

Gaps closed:

- `graph_overview`
- `health`
- `fabric_onboard`
- `fabric_operations`
- `fabric_assign_planes`

Target files:

- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/urls.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/views.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/forms.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/templates/netbox_plant_graph/graph_overview.html`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/templates/netbox_plant_graph/health.html`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/templates/netbox_plant_graph/fabric_onboard.html`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/templates/netbox_plant_graph/fabric_operations.html`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/templates/netbox_plant_graph/fabric_plane_assignment.html`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/navigation.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/tests/test_v2_ui.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/tests/test_v2_urls.py`

Sub-agent packet D1 (visibility pages):

- Ownership:
  - `graph_overview`, `health` views/templates
- Deliver:
  - restore visibility and health summaries from V2 data.
- Tests:
  - page render and key metric assertions.

Sub-agent packet D2 (onboard/assign/ops workflows):

- Ownership:
  - onboarding/plane assignment/operations views and forms
- Deliver:
  - restore fabric onboarding and operational control pages.
  - keep flow aligned with V2 stamp/run models.
- Tests:
  - submit path coverage and redirect assertions.

Sub-agent packet D3 (menu reconstruction):

- Ownership:
  - `navigation.py`
- Deliver:
  - restore grouped workflow navigation:
    - `Fabric Visibility`
    - `Lane Analysis`
    - `Policy & Audit`
    - keep `Multi-planar v2` base section for registry objects and current kernel pages.
- Tests:
  - menu structure assertions in `test_v2_ui.py`.

Coordinator integration gate for P1-B:

1. Merge D1 -> D2 -> D3.
2. Run:
   - `manage.py test netbox_plant_graph.tests.test_v2_urls --verbosity 2 --noinput`
   - `manage.py test netbox_plant_graph.tests.test_v2_ui --verbosity 2 --noinput`
3. Manual smoke:
   - click-path from menu to every restored page.

Exit criteria:

1. Visibility/onboarding/day-2 ops pages are restored and navigable.
2. Menu grouping matches operational mental model.

## P2: API and GraphQL Expansion to Support Restored Workflows

Gaps closed:

- REST:
  - `workflow/summary/`
  - `workflow/findings/`
  - `workflow/findings/<int:pk>/`
  - `workflow/runs/`
  - `stamps/preview/`
- GraphQL:
  - lane drilldown/compare/blast-radius equivalents
  - durable audit workflow summary/search/detail/timeline
  - policy summary/dashboard/contamination domain equivalents
  - planning detail queries needed by restored UI

Target files:

- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/api/urls.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/api/views.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/api/serializers.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/graphql/schema.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/graphql/operational_types.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/tests/test_v2_api.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/tests/test_v2_graphql.py`

Sub-agent packet E1 (REST workflow endpoints):

- Ownership:
  - `api/urls.py`
  - `api/views.py`
  - `api/serializers.py`
- Deliver:
  - restore missing workflow/stamp preview endpoints with V2-model wiring.
- Tests:
  - endpoint behavior coverage in `test_v2_api.py`.

Sub-agent packet E2 (GraphQL operational schema):

- Ownership:
  - `graphql/schema.py`
  - `graphql/operational_types.py`
- Deliver:
  - restore operational query surface required by the UI workflows.
- Tests:
  - query payload and filter behavior in `test_v2_graphql.py`.

Coordinator integration gate for P2:

1. Merge E1 -> E2.
2. Run:
   - `manage.py test netbox_plant_graph.tests.test_v2_api --verbosity 2 --noinput`
   - `manage.py test netbox_plant_graph.tests.test_v2_graphql --verbosity 2 --noinput`

Exit criteria:

1. UI workflows no longer require ad hoc service calls outside formal API/GraphQL contracts.
2. REST and GraphQL coverage exists for restored operational pages.

## Final Hardening Gate (after P1-B and P2)

Run complete suite:

- `manage.py test netbox_plant_graph.tests --verbosity 2 --noinput`

Manual plugin smoke in local NetBox:

1. `up.sh`
2. Navigate all menu groups/pages.
3. Run one end-to-end operator journey:
   - onboard fabric,
   - assign planes,
   - stamp workflow,
   - lane drilldown/compare,
   - audit finding lifecycle action,
   - deployment execute/rollback,
   - verify API + GraphQL reads.

Release criteria:

1. No `NoReverseMatch` on any plugin page.
2. Every restored workflow has at least one success-path test.
3. No runtime dependency on floorplan plugin surfaces.

## Delegation Playbook (Copy/Paste Brief Template)

Use this exact handoff shape for each packet:

1. Context:
   - "We are restoring parity gaps from `v2_gap_analysis_original_vs_v2.md`."
2. Ownership boundary:
   - explicit file list only.
3. Required behavior:
   - explicit route/view/template/service outcomes.
4. Tests required:
   - explicit test files and command.
5. Team safety:
   - "You are not alone in the codebase; do not revert others' edits; adapt to concurrent changes."
6. Return format:
   - changed files,
   - behavior summary,
   - tests run and pass/fail.

## Coordinator Tracking Board

- [x] P0-A complete
- [x] P0-B complete
- [x] P1-A complete
- [x] P1-B complete
- [x] P2 complete
- [x] Full-suite pass
- [ ] Manual smoke pass
