# V2 Workspace Usability Improvement Plan

Last updated: 2026-05-22

Status: Implemented in this wave.

Focused verification:

- `./devrun/test.sh netbox_plant_graph.tests.test_v2_architecture_workspace netbox_plant_graph.tests.test_v2_onboarding_workspace`
- Result: 16 tests passed.

This plan covers the next utility/usability wave for the first-class
architecture and onboarding workspaces. The goal is to turn both workspaces
from raw object-detail pages with action buttons into operator workbenches that
show state, next action, blockers, evidence, and handoff outputs.

## Design Principles

1. **Show the operator where they are.** Each workspace should start with a
   compact workflow state strip and a plain next-action panel.
2. **Make blockers visible early.** Validation errors, import conflicts,
   unresolved prerequisites, stale plans, and readiness failures should be
   visible without opening every child object.
3. **Separate source, interpretation, plan, and execution.** Operators need to
   see the source artifact, normalized components/items, generated plan, and
   execution/publish results as distinct layers.
4. **Prefer drill-down over walls of JSON.** Summary cards and grouped tables
   should point to detail pages and handoff JSON instead of dumping everything
   into one screen.
5. **Keep provenance obvious.** Every generated component/item should retain an
   obvious source-artifact link, parser/source label, and revision/hash.
6. **Stay compatible with NetBox.** Use normal NetBox/Bootstrap tables, badges,
   cards, forms, and links. Do not introduce a frontend framework.

## Architecture Workspace Workstream

Primary operator story:

> As a fabric architect, I want to turn a blueprint bundle or schema JSON into
> a published architecture contract, while seeing exactly what was parsed,
> validated, planned, approved, and published.

### A1. Workflow Header

Status: complete.

Add a top-of-workspace operator strip with:

- `Sources`
- `Normalize`
- `Validate`
- `Plan`
- `Approve`
- `Publish`
- `Handoff`

Each step should show `complete`, `active`, `blocked`, or `not started` using
existing workspace/source/component/validation/plan state.

### A2. Next Action Panel

Status: complete.

Add a compact panel that answers:

- what the operator should do next;
- why that action is available or blocked;
- which button or child object is relevant.

Examples:

- no sources: attach a source artifact;
- sources received but not normalized: normalize sources;
- components invalid/conflicted: inspect components;
- no validation run: run validation;
- failed validation: inspect latest validation issues;
- no publish plan: generate plan;
- generated unapproved plan: approve plan;
- approved plan: publish plan;
- published: fetch handoff JSON or open published architecture.

### A3. Source Authoring Help

Status: complete.

Improve the `Attach Architecture Source Artifact` section:

- add a short inline explanation of `Blueprint Bundle`, `Schema JSON`,
  `Stamp Template`, `API Payload`, and `Manual Entry`;
- link to `docs/v2_architecture_workspace_payloads.md` and the RoCE walkthrough
  where link rendering is appropriate;
- show parser inference behavior directly below `Parser Key`;
- keep the form compact enough that the source table remains visible.

### A4. Component Inventory Summary

Status: complete.

Add summary cards or badges for:

- component kinds;
- validation statuses;
- source artifact count by status;
- key architecture semantics when available from normalized payload:
  slug/version, plane count, fabric class, OSFP channel count, MPO count,
  active/dark positions, transfer patterns, and stamp template count.

### A5. Validation And Publish Triage

Status: complete.

Improve validation/publish plan sections:

- show latest validation status and import dry-run summary near the top;
- surface latest issues in a compact table with severity/code/path/message;
- mark stale plans when their workspace revision no longer matches;
- distinguish `generated`, `approved`, `published`, `blocked`, and `failed`
  plans clearly;
- show post-publish links to published architecture and handoff JSON.

### A6. Focused Coverage

Status: complete.

Add/update focused tests for:

- workspace detail page renders the new workflow/header/next-action content;
- normalized architecture bundle shows component counts and source provenance;
- validation and publish plan summaries render without server errors.

## Onboarding Workspace Workstream

Primary operator story:

> As an implementation operator, I want to move from site design sources to a
> staged/applied fabric onboarding plan, while seeing prerequisite gaps,
> readiness, staged object links, and execution progress.

### O1. Workflow Header

Status: complete.

Add a top-of-workspace operator strip with:

- `Sources`
- `Normalize`
- `Prerequisites`
- `Plan`
- `Approve`
- `Apply`
- `Readiness`
- `Publish/Handoff`

Each step should show `complete`, `active`, `blocked`, or `not started` using
existing source/design/prerequisite/plan/stage/readiness state.

### O2. Next Action Panel

Status: complete.

Add a compact panel that answers:

- what the operator should do next;
- which prerequisite, plan, stage, or readiness issue is blocking progress;
- whether the current plan is ready to approve/apply or needs regeneration.

Examples:

- no sources: attach source artifacts;
- unnormalized sources: normalize sources;
- no prerequisites: discover prerequisites;
- open prerequisites: bind/create/defer prerequisites;
- no plan: generate plan;
- generated plan: approve plan;
- approved plan: apply plan;
- failed stage: inspect stage result;
- applied plan: run readiness;
- readiness clean: publish/handoff.

### O3. Source And Design Inventory

Status: complete.

Improve the source/design sections:

- show source status, parser, SHA, and parse result together;
- group design items by kind/status;
- highlight conflicts and warnings before the long item table;
- make source provenance visible for each design item.

### O4. Prerequisite Triage

Status: complete.

Improve prerequisite handling:

- group prerequisites by `open`, `resolved`, `deferred`, and `blocked`;
- show requirement key, object model, resolution mode, bound object, and planned
  create payload availability;
- make quick update forms readable in dense tables;
- show a top-level count of unresolved/blocking prerequisites.

### O5. Plan And Execution Status

Status: complete.

Improve plan/execution sections:

- show latest plan status, approval state, apply state, and import summary;
- show stage counts by status;
- surface failed/skipped/rolled-back stages with result/error snippets;
- link object links created by the current plan where possible.

### O6. Readiness And Handoff

Status: complete.

Improve readiness/handoff:

- surface readiness status and issue counts near the header;
- show readiness issue groups when present;
- make handoff JSON and object links obvious after apply/publish.

### O7. Focused Coverage

Status: complete.

Add/update focused tests for:

- onboarding workspace detail page renders the workflow/header/next-action
  content;
- prerequisites grouped counts render;
- current plan stages and readiness summary render without server errors.

## Execution Split

Two sub-agents should work in parallel:

- **Architecture worker:** Own A1-A6. Primary files are
  `netbox_plant_graph/templates/netbox_plant_graph/v2_object.html`,
  architecture workspace context helpers in `netbox_plant_graph/views.py`, and
  `netbox_plant_graph/tests/test_v2_architecture_workspace.py`.
- **Onboarding worker:** Own O1-O7. Primary files are
  `netbox_plant_graph/templates/netbox_plant_graph/v2_object.html`, onboarding
  workspace context helpers in `netbox_plant_graph/views.py`, and onboarding
  focused tests.

Both workers must avoid unrelated `local-netbox-dev/*` changes already present
in the worktree and should run focused tests only. Do not run the full plugin
test suite unless explicitly requested.

Execution result:

- Architecture worker implemented A1-A6 in
  `netbox_plant_graph/views.py`,
  `netbox_plant_graph/templates/netbox_plant_graph/v2_object.html`, and
  `netbox_plant_graph/tests/test_v2_architecture_workspace.py`.
- Onboarding worker implemented O1-O7 in
  `netbox_plant_graph/views.py`,
  `netbox_plant_graph/templates/netbox_plant_graph/v2_object.html`, and
  `netbox_plant_graph/tests/test_v2_onboarding_workspace.py`.
- Coordination pass confirmed the shared template and view-helper edits are
  separated by workspace block/helper and pass the focused combined workspace
  test run.

## Integration Notes

The two workstreams touch neighboring sections in `v2_object.html` and
neighboring context helpers in `views.py`. Workers should keep edits scoped to
their assigned workspace block/function and report any shared helper they add,
so final integration can resolve overlap cleanly.

Prefer template-local improvements first. Add Python context only where the
template cannot derive the state cleanly from existing objects.
