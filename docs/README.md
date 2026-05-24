# Documentation Map

This directory contains a mix of current V2 reference material and historical
planning notes. Use this map to avoid treating old V1 design documents as the
active implementation contract.

## Current Reference

- [Data model](data_model.md): current V2 schema, topology semantics,
  invariants, and operator/API surfaces.
- [GraphQL contract](v2_graphql_contract_v2.md): versioned GraphQL V2 query
  surface and known contract breaks.
- [External automation contracts](v2_external_contracts.md): stable vs
  experimental labels for REST, GraphQL, import/reconcile, topology audit, and
  visual trace surfaces.
- [Architecture schema](v2_architecture_schema.md): executable architecture
  schema contract, built-in blueprint families, validation codes, and transfer
  geometry maturity.
- [Architecture workspace payloads](v2_architecture_workspace_payloads.md):
  operator-facing guide for preparing Blueprint Bundle, Schema JSON, Stamp
  Template, API Payload, and Manual Entry source artifacts.
- [RoCE 4-plane shuffle architecture walkthrough](v2_roce_4plane_shuffle_architecture_walkthrough.md):
  screenshot-backed operator walkthrough for publishing a four-plane RoCE
  architecture with OSFP 4x200Gbps optics and 2x2 fiber shuffles.
- [Blueprint versioning policy](blueprint_versioning_policy.md): lifecycle,
  immutability, compatibility, and current built-in blueprint support.
- [V2.5 stamping](v2_stamping_v25.md): registry-backed stamp preview/apply,
  retry classification, and rollback preview/apply behavior.
- [Import reconciliation](v2_import_reconciliation.md): JSON shape and command
  contract for plugin-native dry-run/apply imports and saved import reports.
- [Operational impact modeling](v2_operational_impact_modeling.md): cable cut,
  MPO unplug, OSFP unseat, saved impact reports, JSON export, and comparison
  contracts.
- [Topology integrity audit](v2_topology_integrity.md): command and JSON report
  contract for preflight topology checks.
- [Visual trace component](v2_visual_trace_component.md): reusable Path Query
  and Interface Fanout Trace renderer shell and test hooks.
- [Fabric onboarding workflows](v2_fabric_onboarding_workflows.md): current and
  desired end-to-end process for onboarding a net-new multi-planar RoCE fabric,
  including site design documents and direct UI/API entry paths.
- [Desired onboarding dry-run gap log](v2_desired_onboarding_dry_run_gap_log.md):
  historical dry run that motivated the first-class onboarding workspace. Its
  early workspace/source/plan gaps are now addressed by the first-slice
  implementation; later planned-graph simulation gaps remain useful context.
- [First-class architecture workspace](v2_first_class_architecture_workspace.md):
  current design and implementation notes for architecture source artifacts,
  normalized blueprint components, validation runs, publish plans, and publish
  handoff JSON.
- [First-class onboarding workspace](v2_first_class_onboarding_workspace_design.md):
  implemented first-slice data model, services, UI, API, migration, and
  follow-on roadmap for turning fabric onboarding into a persistent guided
  workspace.
- [Blueprint library expansion targets](blueprint_library_expansion_targets.md):
  implemented baseline for generalized blueprint shapes and remaining hardening
  notes.
- [High-value deepening plan](v2_high_value_deepening_plan.md): historical
  second-stage plan for fleshing out the seven highest-value V2 improvements.
- [Cutover runbook](v2_cutover_runbook.md): validation checklist for bringing
  V2 online.
- [Post-MVP expansion plan](v2_post_mvp_expansion_plan.md): completed and
  planned post-MVP work areas.
- [First-class cabling plan](v2_first_class_cabling_plan.md): plan that led to
  `CableAssembly` and strand-to-cable linkage.
- [Transceiver modeling](v2_transceiver_modeling_plan.md): current hybrid
  NetBox-module/plugin-semantic transceiver model, built-in profiles, stamping
  and import bindings, UI exposure, and remaining polish.
- [V2 ground-up rewrite plan](v2_ground_up_rewrite_plan.md): historical V2
  implementation plan; useful for intent, but code and `data_model.md` are now
  authoritative.

## Gap / Execution Records

These are useful audit trails for how V2 reached the current state:

- [Original vs V2 gap analysis](v2_gap_analysis_original_vs_v2.md)
- [Gap analysis refresh](v2_gap_analysis_refresh_2026-05-20.md)
- [Gap closure execution plan](v2_gap_closure_execution_plan.md)
- [Gap resolution plan](gap-resolution-plan.md)
- [Stamp system gap closure plan](stamp-system-gap-closure-plan.md)

## Historical / Superseded

These documents describe older V1-era thinking or superseded implementation
directions. Read them only as historical context:

- [Floorplan integration tracking](floorplan_integration_tracking.md)
- [Madison cable plant conceptual model](madison_cable_plant_conceptual_model.md)
- [RoCE fabric modeling runbook](runbook-roce-fabric-modeling.md)
- [RoCE automation strategy](roce_v2_automation_strategy.md)
- [RoCE automation strategy response](roce_v2_automation_strategy_response.md)
- [V1 gap resolution plan](gap-resolution-plan.md)
- [V1 stamp system gap closure plan](stamp-system-gap-closure-plan.md)
- [Plugin design sketch](../netbox_plant_graph_plugin_design.md)

## Current Operator Surface

The active NetBox menu is:

- `Operate`: Fabric Overview, Interface Fanout Trace, Path Query, Physical Cable
  Blast Radius
- `Build & Run`: Architecture Workspaces, Onboarding Workspaces, Onboard
  Fabric, Operations Center, Import Preview, Impact Reports
- `Audit`: Audit Dashboard, Audit Triage, Exception Requests
- `Model Inventory`: Fabrics, Architectures, Cable Assemblies, Lane Inventory,
  Model Catalog

Hidden or legacy URLs may still exist for compatibility or advanced workflows,
but the menu above is the intended operator entry point.
