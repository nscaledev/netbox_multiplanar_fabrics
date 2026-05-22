# V2 First-Class Physical Cabling Plan

Status: implementation-complete for current V2 scope
Last updated: 2026-05-22

## Objective

Make physical cabling a first-class plugin-native domain with explicit cable
assembly rows and explicit strand-to-cable linkage (without NetBox native cable
objects as source of truth).

## Required Behavior (Target)

1. Add plugin table for cable assemblies with:
   - `site` (FK to NetBox `Site`)
   - `cable_id` (string, unique per site)
   - `manufacturer` (string)
   - `serial_number` (string)
   - `model_id` (string)
   - `description` (text)
   - `parent_cable` (self-reference for trunk/jumper hierarchy)
2. Jumpers are one row each.
3. Trunks are one row each.
4. Trunk-contained jumpers modeled as child cables via `parent_cable`.
5. Each fiber strand resolves to a cable assembly via strand-local
   `cable_site + cable_id`.

## Execution Slices

### Slice 1: Schema introduction (completed)

Delivered:

1. Added `CableAssembly` model with unique (`site`, `cable_id`).
2. Added `FiberStrand.cable_site` and `FiberStrand.cable_id`.
3. Added strand validation that enforces pair coherence and assembly existence
   when pair values are provided.
4. Added migration:
   - `0003_cable_assembly.py`

### Slice 2: Registry/API/UI integration (completed)

Delivered:

1. Added `CableAssembly` to V2 registry standard object surface.
2. Exposed cable assembly through generated API/UI forms/tables/filtersets.
3. Added strand table/API exposure for `cable_site` + `cable_id`.

### Slice 3: Stamping/runtime propagation (completed)

Delivered:

1. Updated stamping service to create/update deterministic `CableAssembly` rows
   for stamped fiber segments.
2. Updated `FiberStrand` creation to persist `cable_site + cable_id`.
3. Added managed object tracking for `cable_assemblies`.
4. Updated rollback model maps to include `cable_assemblies`.

### Slice 4: Test updates (completed for focused V2 coverage)

Delivered:

1. Updated model/resolver/stamping tests to include cable assembly semantics.
2. Updated registry/API contract tests for the expanded V2 model surface.
3. Ran focused coverage during implementation for:
   - `test_v2_models`
   - `test_v2_resolver`
   - `test_v2_stamping`
   - `test_v2_registry`
   - `test_v2_api`

### Slice 6: Workflow/UI exposure (completed)

Delivered:

1. Added top-level plugin menu CRUD entry for `CableAssembly`.
2. Added cable-assembly context to `Path Query` path-step table.
3. Added cable-assembly context to lane-analysis workflow pages:
   - `Lane Drilldown`
   - `Lane Compare`
   - `Physical Cable Blast Radius`
   - `Lane Workspace`
4. Added cable-assembly representation in visual path traces as translucent
   grouped cable cylinders with brace labels and SVG export support.

### Slice 5: Hardening and migration policy (completed for current phase)

Decisions finalized:

1. Keep `FiberStrand.cable_site` + `cable_id` **nullable for now** to support
   topology-first modeling before physical cable plant design is finalized.
2. Keep `parent_cable` constrained to the **same site**.
3. Keep relational `parent_cable` FK only; no additional literal parent-cable
   ID column.

Follow-up backlog:

1. Optional parent/child cycle prevention.
2. Optional immutability policy for cable identity after first strand binding.

## Significant Decisions Requiring Explicit Confirmation

Resolved:

1. `FiberStrand.cable_site` + `cable_id` remain nullable in this phase.
2. `parent_cable` remains same-site only.
3. `parent_cable` relational FK is sufficient.

## Current Implementation Notes

1. `CableAssembly` is now a first-class persisted model.
2. Stamped strands now carry explicit cable assembly identity.
3. Data model documentation has been updated in:
   - `/Users/mencken/github-repos/netbox_multiplanar_fabrics/docs/data_model.md`
