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
- [Import reconciliation](v2_import_reconciliation.md): JSON shape and command
  contract for plugin-native dry-run/apply imports.
- [Topology integrity audit](v2_topology_integrity.md): command and JSON report
  contract for preflight topology checks.
- [Visual trace component](v2_visual_trace_component.md): reusable Path Query
  and Interface Fanout Trace renderer shell and test hooks.
- [High-value deepening plan](v2_high_value_deepening_plan.md): second-stage
  plan for fleshing out the seven highest-value V2 improvements after their
  first implementation wave.
- [Cutover runbook](v2_cutover_runbook.md): validation checklist for bringing
  V2 online.
- [Post-MVP expansion plan](v2_post_mvp_expansion_plan.md): completed and
  planned post-MVP work areas.
- [First-class cabling plan](v2_first_class_cabling_plan.md): plan that led to
  `CableAssembly` and strand-to-cable linkage.
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
- `Build & Run`: Onboard Fabric, Operations Center
- `Audit`: Audit Dashboard, Audit Triage, Exception Requests
- `Model Inventory`: Fabrics, Architectures, Cable Assemblies, Lane Inventory,
  Model Catalog

Hidden or legacy URLs may still exist for compatibility or advanced workflows,
but the menu above is the intended operator entry point.
