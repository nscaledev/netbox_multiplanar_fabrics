# Floorplan Integration Tracking

Tracked implementation checklist for refactoring the spatial-planning surface of
`netbox_plant_graph` to leverage `netbox-floorplan-plugin` for operator-facing
2D site/location layout.

## Status

- [x] Create tracked implementation note
- [x] Slice 1: dependency, prerequisite docs, and floorplan compatibility guard
- [x] Slice 2: floorplan bridge service
- [x] Slice 3: navigation cutover and coordinate-layout deprecation
- [x] Slice 4: stamp-time floorplan synchronization
- [x] Slice 5: API and plan-execution sync reporting
- [x] Slice 6: narrow `SpatialPlacement` ownership to planning metadata
- [x] Slice 7: explicit floorplan-to-placement reconciliation
- [x] Slice 8: final docs cleanup

## Ownership Boundary

- `netbox-floorplan-plugin` owns operator-facing 2D layout for `Site` and
  `Location` scopes.
- `netbox_plant_graph` continues to own:
  - `SpatialTemplate` and `SpatialTemplateNode`
  - `RackPopulationTemplate` and `RackPopulationSlot`
  - `ConnectionTemplate`
  - spatial/rack/assembly/breakout stamp execution
  - deployment-plan provenance
  - `SpatialPlacement` data not represented by floorplan canvas state,
    especially `position_z`, `orientation`, arbitrary reference frames, and
    planning metadata

## Slice 1 Checklist

- [x] Add this tracking file
- [x] Add `netbox-floorplan-plugin` package dependency
- [x] Document install and local-dev prerequisite for enabling
      `netbox_floorplan`
- [x] Add compatibility helper for package/import/settings checks
- [x] Add startup and system-check warnings for missing or disabled floorplan
      plugin
- [x] Add contract tests for the compatibility helper

## Slice 2 Checklist

- [x] Add a dedicated floorplan bridge service module
- [x] Confine floorplan-model imports to the bridge
- [x] Add scope helpers for `Site` and `Location`
- [x] Add floorplan ensure/get/url helpers
- [x] Add rack-placement synchronization with managed-object markers
- [x] Add mocked Django tests for floorplan creation and rack sync behavior
- [x] Enable `netbox_floorplan` in local dev/test NetBox plugin configuration

## Slice 3 Checklist

- [x] Deprecate the custom `coordinate-layout/` page as the primary 2D UI
- [x] Redirect scoped site/location layout requests to floorplan plugin URLs
- [x] Replace the old SVG/grid template with a deprecation handoff page
- [x] Add floorplan guidance to the spatial stamp UI
- [x] Add a concrete floorplan link on successful spatial stamps
- [x] Update view tests for redirect and deprecation behavior

## Slice 4 Checklist

- [x] Extend `SpatialStampResult` with floorplan-sync summary fields
- [x] Collect stamped rack/placement pairs during spatial stamping
- [x] Call the floorplan bridge after successful spatial stamp transactions
- [x] Keep hierarchy, rack-population, and connection stamping behavior unchanged
- [x] Respect the bridge's manual-override and force-sync behavior through service flags
- [x] Add planning tests covering site scope, location scope, and no-rack skip behavior

## Execution Notes

- Keep floorplan-plugin imports confined to dedicated integration modules.
- Do not change current template/rack/connection planning semantics during
  Slice 1.
- Avoid deleting existing `SpatialPlacement` behavior until the replacement
  path is wired and tested.

## Slice 5 Checklist

- [x] Preserve floorplan-sync summary fields in direct spatial-stamp API responses
- [x] Add API controls for disabling or forcing floorplan sync during spatial stamps
- [x] Persist floorplan-sync summary data onto plan-backed spatial `StampRecord.metadata`
- [x] Carry floorplan-sync reporting through queued `execute_plan()` spatial dispatch
- [x] Surface floorplan-sync outcome in deployment-plan detail/workflow views
- [x] Add planning/API/view tests covering the reported floorplan-sync summary

## Slice 6 Checklist

- [x] Convert generated `SpatialPlacement` UI surfaces to read-only browse/detail views
- [x] Disable generated create/update/delete methods for the `SpatialPlacement` REST surface
- [x] Keep GraphQL and scope-filtered read access for stamped placement metadata
- [x] Update handoff/docs copy so floorplan remains the only interactive layout editor

## Slice 7 Checklist

- [x] Add an explicit floorplan-to-placement reconciliation service in the floorplan bridge
- [x] Reconcile managed rack objects back into `SpatialPlacement.position_x`, `position_y`, and `orientation`
- [x] Preserve existing `position_z`, reference frames, and metadata when updating placements
- [x] Keep creation of missing placements opt-in rather than implicit
- [x] Expose a dedicated reconciliation API endpoint separate from the read-only `SpatialPlacement` CRUD surface
- [x] Add bridge/API tests covering update, create-missing, and unmanaged-object skip behavior

## Slice 8 Checklist

- [x] Update top-level docs to describe the final floorplan-vs-planning ownership boundary
- [x] Document the explicit reconciliation workflow after manual floorplan edits
- [x] Align contributor/dev setup guidance with the read-only `SpatialPlacement` surface
- [x] Update the design sketch status notes so they describe the completed handoff rather than the pre-integration plan
