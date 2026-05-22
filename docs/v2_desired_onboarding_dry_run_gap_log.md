# Desired Onboarding Workflow Dry Run Gap Log

Last updated: 2026-05-22

This document dry-ran the desired/future-state onboarding workflow from
`v2_fabric_onboarding_workflows.md` against the codebase before the first-class
onboarding workspace was implemented.

Status note: Gap Stops 1-7 and the plan-acceptance/execution spine of Gap Stops
9-11 are now addressed by the first-slice `OnboardingWorkspace` implementation.
The remaining major gaps are rich spreadsheet/diagram parsing, guided
remediation tasking, and planned-graph audit/impact simulation before commit.

Dry-run assumption: an operator is onboarding a net-new multi-planar RoCE fabric
for a new site. The site design package contains some blueprint/spreadsheet
data, but some objects must be supplied directly through NetBox or plugin UI/API.

Each **Gap Stop** marks a point where the desired workflow cannot be executed as
written. The dry-run then records the current workaround that would allow an
operator to keep moving today.

## Summary

The current plugin can onboard a fabric through a sequence of existing surfaces:

1. seed/select an architecture,
2. create a fabric,
3. V2.5-preview/apply a stamp template,
4. dry-run/apply import reconciliation payloads,
5. run topology integrity audit,
6. validate visual paths and blast radius,
7. preserve `StampRun`, `OperationRun`, import, audit, and impact artifacts.

The desired workflow cannot yet run as one end-to-end onboarding process because
the plugin lacks a first-class onboarding workspace, source-document artifact
model, normalized design inventory, unified plan object, pre-apply simulated
audit, and publish/handoff dossier.

## Gap Stop 1: I0 - Create onboarding workspace

**Attempted desired action:** create a first-class onboarding workspace that
holds target site, fabric class, selected blueprint, source documents,
direct-entry data, execution plan, approvals, and status.

**Current support:** partial. `FabricOnboardView` creates a `Fabric`, planes,
and an audit event. `OperationRun` can persist operation reports, but it is not
an onboarding workspace.

**Gap:** there is no persistent `OnboardingWorkspace`/`FabricOnboardingRun`
model, no workspace state machine, no workspace detail page, and no single
container for source documents, staged data, plan, approvals, and handoff
artifacts.

**Current workaround:** create the `Fabric` through Onboard Fabric or stamping,
then use separate `StampRun`, import report, audit report, and impact report
records as the operational trail.

**Needed to proceed in desired state:** add an onboarding workspace model and
operator page/API before any later step can be represented as one coherent
workflow.

## Gap Stop 2: I1 - Attach site design package

**Attempted desired action:** attach or reference blueprints, spreadsheets,
diagram files, port maps, cable schedules, rack elevations, vendor BOMs, and
implementation notes.

**Current support:** partial. Import payloads can carry `source_system`,
`source_document`, `source_row`, `external_id`, and `idempotency_key`.
Blueprint bundles can be uploaded/pasted through Import Preview.

**Gap:** there is no plugin-native source-document artifact table, file/hash
tracking, document manifest, attachment UI, or durable mapping from a design
artifact to every object it produced.

**Current workaround:** store files outside the plugin, generate JSON import
payloads, and use row-level provenance fields where supported.

**Needed to proceed in desired state:** add a design-source artifact model with
document references, content hashes, source labels, and row/object provenance.

## Gap Stop 3: I2 - Normalize documents into design inventory

**Attempted desired action:** parse or ingest site design documents into a
staged, diffable design inventory without mutating topology.

**Current support:** partial. `mpf_import_reconcile` and Import Preview can
dry-run already-normalized JSON. Supported item kinds include blueprints, cable
assemblies, strand-to-cable assignment, endpoints, transport channels,
channel-position maps, and strand terminations.

**Gap:** the plugin does not parse spreadsheets or diagrams, does not maintain a
staged design inventory model, and does not preserve a normalized pre-apply
design graph independent of import reports.

**Current workaround:** external scripts transform spreadsheets/diagrams into
import-reconcile JSON, then Import Preview provides row-level diffs.

**Needed to proceed in desired state:** add a normalized design-inventory layer
that stores parsed/staged rows, document provenance, validation status, and
planned object identity before apply.

## Gap Stop 4: I3 - Fill gaps through guided UI or API

**Attempted desired action:** fill missing data through schema-aware workspace
forms or API endpoints while tagging every manual value as operator-entered
provenance.

**Current support:** partial. Generated CRUD/API views exist for standard V2
objects. Import Preview can accept hand-authored JSON. NetBox UI/API handles
core inventory prerequisites.

**Gap:** there is no guided onboarding UI for missing cable rows, endpoint maps,
channel maps, strand terminations, or prerequisite bindings. Manual UI/API edits
are not automatically associated with an onboarding workspace or marked with
operator-entered provenance.

**Current workaround:** use generated CRUD/API views and manually add metadata
where needed.

**Needed to proceed in desired state:** add guided workspace forms/API that
write staged design rows with provenance before committing topology rows.

## Gap Stop 5: I4 - Select or import architecture blueprint

**Attempted desired action:** select a registered blueprint or import a new
blueprint bundle, validate it, and keep it staged inside the onboarding plan
before activation.

**Current support:** strong but not workspace-integrated. The blueprint registry
has active built-ins. Import Preview supports `fabric_architecture_blueprint`
and `.mpf-blueprint.json` bundles. V2.5 stamping validates blueprint identity,
lifecycle, parameters, and device-type compatibility.

**Gap:** imported blueprints are handled through import reconciliation, not as
staged onboarding-plan candidates. There is no workspace-level blueprint
selection object that ties blueprint choice to source documents, prerequisites,
planned stamp parameters, and approval.

**Current workaround:** import the blueprint through Import Preview, then select
or reference the resulting `FabricArchitecture`/`StampTemplate` separately.

**Needed to proceed in desired state:** make blueprint selection/import a
workspace stage with plan-visible validation, approval, and provenance.

## Gap Stop 6: I5 - Resolve NetBox prerequisites

**Attempted desired action:** actively resolve missing prerequisites by choosing
bind, create, or intentionally defer for each missing object.

**Current support:** partial. V2.5 stamp creation options can create or bind
some NetBox devices/interfaces. Blueprint compatibility can check required
`DeviceType` availability. Import Preview reports missing prerequisites as
conflicts.

**Gap:** there is no prerequisite-resolution queue with per-object bind/create/
defer choices. Missing prerequisites are mostly reported as validation issues or
conflicts rather than turned into guided resolution tasks.

**Current workaround:** create or correct prerequisite NetBox objects manually,
then rerun stamp preview or import preview.

**Needed to proceed in desired state:** add a prerequisite planner/resolver that
turns missing NetBox/plugin prerequisites into explicit operator decisions.

## Gap Stop 7: I6 - Generate unified onboarding plan

**Attempted desired action:** generate one plan containing blueprint selection,
NetBox object creation/binding, topology creation/update, cable assignment,
expected audit posture, path/fanout coverage, rollback/retry posture, and
provenance.

**Current support:** fragmented. V2.5 stamp preview, Import Preview, topology
integrity audit, and impact modeling each produce useful reports, but they are
separate artifacts.

**Gap:** there is no unified onboarding plan object, no plan hash, no combined
diff, and no combined provenance/rollback/retry summary across stamp and import
work.

**Current workaround:** operators mentally compose Stamp Preview, Import
Preview, audit reports, and impact reports.

**Needed to proceed in desired state:** add a plan builder service that composes
stamp preview, import dry-run, prerequisite plan, audit expectations, and
impact previews into one persisted plan envelope.

## Gap Stop 8: I7 - Preview stamp, import, audit, and impact

**Attempted desired action:** run a comprehensive pre-apply preview including
V2.5 stamp preview, import dry-run, architecture gate, prerequisite resolution,
simulated topology audit against the planned graph, and optional impact checks.

**Current support:** partial. Stamp preview, import dry-run, architecture gate,
current-state topology audit, and current-state impact modeling all exist.

**Gap:** topology integrity audit and impact modeling run against persisted
current topology, not a staged planned graph. There is no planner that creates
an in-memory or temporary graph for "what will be true after this unified plan
applies."

**Current workaround:** apply in stages, then run topology audit and impact
checks after persistence.

**Needed to proceed in desired state:** add planned-graph simulation or a
transactional preview substrate that can evaluate audits and impact before
committing.

## Gap Stop 9: I8 - Plan accepted?

**Attempted desired action:** accept or reject a plan based on no blocking
errors, explicit warning acknowledgement, creation approval, saved plan hash,
and rollback/retry classification.

**Current support:** partial. Stamp execute has validation gates. Import apply
requires confirmation. Saved reports include hashes in some domains.

**Gap:** there is no single plan-acceptance record, no unified warning
acknowledgement, no plan hash across all stages, and no approval workflow tied
to an onboarding workspace.

**Current workaround:** rely on per-page confirmation controls and persisted
operation reports.

**Needed to proceed in desired state:** add plan approval state, explicit
acknowledgements, and immutable accepted-plan snapshot.

## Gap Stop 10: I9 - Remediate design data in workspace

**Attempted desired action:** remediate rejected plans in the workspace with
links to exact source rows, fields, objects, or manual entries.

**Current support:** partial. Import diffs/conflicts include row-level details
and provenance fields when payloads supply them. Audit findings include
remediation hints and workflow links.

**Gap:** remediation is not workspace-native and cannot consistently round-trip
to the exact spreadsheet cell, diagram object, staged row, or manual entry.

**Current workaround:** edit external documents or JSON payloads, manually
update objects, and rerun previews.

**Needed to proceed in desired state:** add remediation tasks tied to staged
design rows and source-document provenance.

## Gap Stop 11: I10 - Apply plan transactionally by stage

**Attempted desired action:** apply prerequisites, architecture/template rows,
fabric stamp, cable plant imports, audit persistence, and handoff artifacts as a
staged transaction with resume/rollback.

**Current support:** partial. Import apply is transactional. V2.5 stamping is
idempotent and records `StampRun` manifests. Rollback exists for safe stamp-run
manifests.

**Gap:** there is no cross-stage transaction or orchestrator. Stamp and import
stages do not share one resume point or rollback manifest. Prerequisite creation
and handoff artifact generation are not part of one apply sequence.

**Current workaround:** apply stamp and imports as separate operator actions,
then inspect their individual records.

**Needed to proceed in desired state:** add staged onboarding-plan execution
with per-stage state, dependencies, retry/resume, and combined rollback posture.

## Gap Stop 12: I11 - Run readiness audit and policy workflow

**Attempted desired action:** run topology integrity and policy readiness as a
standard onboarding step with grouped findings, affected workflows,
suppress/ack/remediate actions, and readiness deltas.

**Current support:** mostly present after apply. Topology integrity audits can
persist `OperationRun` snapshots. Audit Dashboard/Triage expose grouped
findings and workflow actions. Suppression/exception lifecycle exists.

**Gap:** readiness is not automatically owned by an onboarding workspace, and
readiness deltas are not first-class plan criteria. Stamp/import apply does not
automatically require a post-apply readiness gate.

**Current workaround:** run audit from Operations Center or command line after
apply and review Audit Dashboard/Triage.

**Needed to proceed in desired state:** make readiness audit a required
workspace stage with pass/fail state and delta tracking.

## Gap Stop 13: I12 - Ready for operations?

**Attempted desired action:** compute operational readiness from topology
integrity, path/fanout coverage, cable completeness, blueprint compatibility,
unresolved imports, open findings, and impact-modeling sanity checks.

**Current support:** partial. Individual readiness signals exist across audit,
path query, fanout trace, import reports, blueprint checks, and impact reports.

**Gap:** there is no single readiness decision engine or persisted readiness
score/status for a fabric onboarding run.

**Current workaround:** use Operations Center and manual operator judgment.

**Needed to proceed in desired state:** add a readiness evaluator that consumes
all relevant report artifacts and produces one publish/no-publish decision.

## Gap Stop 14: I13 - Fix, suppress, or approve exceptions

**Attempted desired action:** fix data, suppress known non-blocking findings, or
request/approve exceptions inside the onboarding workflow, then regenerate the
relevant plan or audit section.

**Current support:** partial to strong for audit findings. Suppress/ack/
remediate/exception workflows exist, but they are general audit workflows rather
than onboarding-specific tasks.

**Gap:** exception approvals do not feed back into an onboarding workspace
readiness state or accepted plan.

**Current workaround:** use Audit Triage and Exception Requests, then rerun
audit manually.

**Needed to proceed in desired state:** connect finding lifecycle and exception
state to onboarding readiness and publish gates.

## Gap Stop 15: I14 - Publish fabric and handoff artifacts

**Attempted desired action:** publish the fabric and generate a handoff dossier:
accepted plan, applied manifest, source document manifest/hashes, import
reports, stamp runs, audit reports, representative visual trace exports, and
blast-radius reports.

**Current support:** partial. The component artifacts exist separately:
`StampRun`, import reports, topology integrity reports, impact reports, and SVG
exports.

**Gap:** there is no publish action, no fabric-onboarding dossier, and no
automatic handoff artifact bundle.

**Current workaround:** manually collect links/exports from Operations Center,
Path Query/Fanout Trace, Import Preview, Impact Reports, and Audit Dashboard.

**Needed to proceed in desired state:** add a publish action and generated
handoff bundle tied to the onboarding workspace.

## Gap Stop 16: I15 - Monitor drift and planned changes

**Attempted desired action:** use the same workspace model for later fabric
changes, additional phases/planes, cable plant updates, device replacements,
blueprint migrations, document revisions, and drift audits.

**Current support:** partial. Periodic audits, imports, stamp reruns, and
operation reports exist. Blueprint versioning and import provenance exist.

**Gap:** there is no persistent lifecycle thread linking revisions of design
documents, planned changes, applied topology updates, drift findings, and
readiness changes across the fabric lifecycle.

**Current workaround:** run later changes as independent stamp/import/audit
operations.

**Needed to proceed in desired state:** extend onboarding workspaces into
fabric-change workspaces with revision history and drift tracking.

## Result Of Dry Run

The dry-run hits a gap at every desired-state step, although several steps have
strong current building blocks. The most important sequencing insight is:

1. **Build the onboarding workspace first.** Without it, every later desired
   feature has nowhere coherent to persist state.
2. **Add design-source artifacts and staged inventory next.** These make site
   documents and direct UI/API entry equivalent inputs.
3. **Then compose unified plan generation.** Existing V2.5 stamp preview,
   import dry-run, audit, and impact services can become plan sections.
4. **Finally add publish/readiness lifecycle.** This turns the existing
   operational reports into one handoff and monitoring loop.
