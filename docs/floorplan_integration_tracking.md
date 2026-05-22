# Floorplan Integration Tracking

Status: **Archived / superseded**

This file is retained as historical context only. It does **not** describe the
current V2 architecture or an active implementation plan.

## Current V2 Position

The current multiplanar fabrics plugin does not rely on
`netbox-floorplan-plugin` or `netbox_floorplan` at runtime.

V2 ownership is:

- `netbox_plant_graph` owns modeled-fabric topology, stamping, first-class cable
  assemblies, optical lanes, transport channels, path tracing, blast radius, and
  audit/workflow state.
- NetBox core owns device/site/rack/interface inventory used as anchors.
- No external floorplan plugin owns required V2 behavior.
- Madison/local spatial scripts under `local-netbox-dev/scripts/` are lab
  helpers, not plugin core runtime behavior.

## Superseded Assumptions

Earlier notes in this repository explored delegating operator-facing 2D layout
to `netbox-floorplan-plugin`. That direction was explicitly reversed during the
V2 rewrite. Do not use this historical plan as guidance for new work.

Specifically, do not reintroduce:

1. package or settings dependencies on `netbox_floorplan`,
2. floorplan compatibility checks or startup warnings,
3. floorplan bridge/sync/reconcile services,
4. floorplan URL handoffs as required workflow paths,
5. floorplan-backed stamp execution or reporting fields.

## If Spatial Work Returns

Any future spatial/layout work should start from the V2 model and operator
workflow requirements, not from the archived floorplan handoff plan. A new plan
should explicitly answer:

1. whether spatial objects are plugin-native or external,
2. how spatial data relates to `FabricNode`, `Endpoint`, `CableAssembly`, and
   NetBox rack/site objects,
3. whether the UI is operational, planning-only, or both,
4. how the implementation remains optional for non-spatial fabric deployments.
