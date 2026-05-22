# V2 Cutover Decisions

Last updated: 2026-05-21

## Scope

This document records final cutover decisions for the V2 rewrite of `netbox_plant_graph`.

## Decisions

1. Package replacement
   `netbox_plant_graph` is replaced in place by V2. We do not run V1 and V2 as separate plugin packages.

2. Data migration policy
   No automatic migration of V1 topology/audit/cablepath-derived data is included in MVP.
   Existing V1 data may remain in database tables for historical reference, but V2 does not depend on it.

3. Modeled-fabric source of truth
   For modeled fabrics, V2 remains authoritative for path semantics.
   NetBox `Cable`, `CableTermination`, `CablePath`, and `PortMapping` are not used as V2 source of truth.

4. Active endpoint anchoring
   V2 nodes/endpoints can anchor to NetBox `Device` and `Interface` records via generic source links.
   Stamping supports both explicit bind and create-or-bind execution modes.

5. Acceptance gate
   Cutover acceptance requires:
   workflow stamp success
   resolved optical lane paths
   no modeled-fabric NetBox cable rows
   source anchors present
   stamp provenance present

## Deferred To Post-MVP

- V1-to-V2 data translation tooling.

## Post-MVP Decisions Already Applied

- Audit, suppression, policy, operations, and exception workflow surfaces were
  added back on top of the V2 model.
- Floorplan-plugin parity is no longer a goal; runtime reliance on
  `netbox_floorplan` is explicitly forbidden.
- Spatial/layout work, when present, must be plugin-native or local lab support,
  not an external floorplan handoff.
- GraphQL V2 is formally versioned as `2.0.0`; REST remains the workflow
  mutation surface.
