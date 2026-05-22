# Architecture Blueprint Library: Expansion Targets

Status: implemented baseline; follow-up hardening continues through focused tests and operator UX polish
Last updated: 2026-05-22

## Framing

The branch now has a versioned blueprint registry with built-ins for GB300 4-plane
shuffle, H100 4-plane direct attach, and GB300 8-plane shuffle. The original targets
below remain the architectural checklist for the blueprint-library work, but the first
operational baseline has been wired through schema validation, V2.5 preview/apply,
import reconciliation, lifecycle policy, and focused regression coverage.

The targets below are organized into three tiers:

- **Tier 1 — Pattern Expressiveness:** what shapes the schema can describe.
- **Tier 2 — Stamp-Time Parameter Flexibility:** what operators can tune without editing a blueprint.
- **Tier 3 — Library Infrastructure:** how blueprints are stored, versioned, shared, and validated.

---

## Tier 1 — Pattern Expressiveness

### T1-A: Parameterized Plane Count

Today `plane_count` exists as a field in `ArchitectureSchemaDefinition`, but the only
tested value is 4. The stamping executor and the topology-integrity audit both
implicitly assume a 4-plane layout.

**Target:** make `plane_count` a first-class stamp parameter with validated range at the
template level. Blueprint families must declare `min_planes`, `max_planes`, and
`default_planes`. An operator should be able to stamp a 2-plane pilot, a 4-plane
half-fabric, an 8-plane full fabric, and a 16-plane future super-fabric all from the
same blueprint definition by supplying a different `plane_count` at stamp time. The
integrity audit and blast-radius report must reflect the declared plane count, not an
implicit 4.

Primary files:
- `netbox_plant_graph/services/architecture_schema.py`
- `netbox_plant_graph/services/stamping.py`

---

### T1-B: Generalized Shuffle Geometry

The schema today supports exactly one shuffle geometry: `shuffle_2x2`. The math is
correct for a 2×MPO12 cassette. It does not cover:

- `shuffle_1x4`: a single front MPO fans to four rear strands (used in some pre-terminated trunk assemblies).
- `shuffle_2x2_mpo24`: 24-position MPO variant needed as 400G-to-800G migration assemblies appear.
- `shuffle_4x4`: multi-cassette trunk housing.
- `direct_attach`: no shuffle, straight-through polarity, used for short-reach DAC or AOC panels.
- `polarity_type_b` / `polarity_type_c`: TIA-568 polarity variants used in legacy cable plants.

**Target:** extend `TRANSFER_PATTERN_KIND_VALUES` and the schema validator to validate
each of these geometries under its own sub-validator. Every geometry variant must have a
named Python provider function (like `key_down_roll_position_pairs`) whose output the
validator checks deterministically against the declared matrix. Add a `shuffle_nxm`
generic validator for any N×M cassette that is too novel to name yet.

Primary files:
- `netbox_plant_graph/services/architecture_schema.py`

---

### T1-C: MPO Position Count Variants

The entire validator assumes `mpo_position_count = 12`. MPO-24 cabling is now
available in volume from all major vendors and is increasingly common in very-high-density
shuffles.

**Target:** remove the implicit assumption that MPO position count is 12.
`mpo_position_count` must be a validated parameter of the architecture definition, with
the validator applying correct bounds checks at whatever declared value is given (8, 12,
16, 24). The channel map, active/dark position declarations, and shuffle geometry must
all validate against the declared count rather than any hard constant.

Primary files:
- `netbox_plant_graph/services/architecture_schema.py`
- `netbox_plant_graph/services/architecture.py`

---

### T1-D: Speed-Heterogeneous Role Families

The only active port role today is `gpu_osfp` at implicit 800G (4 × 200G). Neocloud
footprints mix generations: H100 nodes use 400G OSFP (2 × 200G), GB300 NVL72 uses
800G OSFP (4 × 200G), and future Vera Rubin is expected at 1.6T (8 × 200G).

**Target:** each `active_port` role must declare `speed_gbps` and `channels_per_osfp`
as role metadata fields that the schema validator cross-checks against the architecture's
`channels_per_subinterface`. A single blueprint family must be allowed to declare
multiple active port roles at different speeds (for spine-facing vs. compute-facing
ports within one plane). The stamping engine must route object creation to the correct
speed role from the template source-binding map.

Primary files:
- `netbox_plant_graph/services/architecture_schema.py`
- `netbox_plant_graph/services/architecture.py`

---

### T1-E: Multi-Tier Spine Topology Roles

The current role set supports `gpu_tray`, `gpu_osfp`, `gpu_mpo`, `leaf_switch`,
`leaf_osfp`, `leaf_mpo`, `shuffle_board`, and `shuffle_front/rear`. There is no role
for super-spine, meta-spine, or pod-boundary aggregation switches needed for fabrics
above roughly 4 000 GPUs.

**Target:** add role kinds `active_tier_2_device` and `active_tier_2_port` with explicit
`fabric_tier` metadata (values: `leaf`, `spine`, `super_spine`, `meta_spine`). The
architecture validator must enforce that every `active_tier_2_port` role has a
corresponding parent tier in the same architecture. The blast-radius report must group
impacted devices by `fabric_tier` so an operator can immediately see whether a fiber cut
affects only a leaf tier or propagates to spine.

Primary files:
- `netbox_plant_graph/services/architecture_schema.py`
- `netbox_plant_graph/services/architecture.py`
- `netbox_plant_graph/services/graph/blast_radius.py`

---

### T1-F: Frontend / Management Plane Fabric Roles

Today the plugin models only the RoCE backend fabric. A neocloud also needs NetBox to
be the source of truth for the frontside (storage, management, checkpointing) network,
but these share racks, devices, and even cable assemblies with the backend fabric.

**Target:** add a top-level `fabric_class` field to `FabricArchitecture` with values
`roce_backend`, `ethernet_frontend`, `management`, and `storage`. The architecture schema
validator must allow different role constraint rules per class (for example, frontend
families do not need shuffle boards or MPO mapping). Blueprint families for the frontside
can reuse the same `StampTemplate` dispatch path and dry-run previews without any code
duplication.

Primary files:
- `netbox_plant_graph/models.py`
- `netbox_plant_graph/services/architecture_schema.py`

---

### T1-G: Custom Transfer Pattern as a First-Class Validated Kind

Today `custom` is in `TRANSFER_PATTERN_KIND_VALUES` but the schema validator has no
sub-validator for it; it accepts any rule body without checking invariants.

**Target:** `custom` transfer patterns must carry a mandatory `validator_entrypoint`
field — a dotted Python import path to a callable that receives the rule body and returns
a list of schema errors. The architecture schema validator calls this entrypoint during
validation, so custom geometries get the same deterministic check as built-in kinds.
Document the entrypoint contract and ship a reference no-op validator. This enables
third-party vendors or site-specific teams to ship their own cassette geometry without
forking the core validator.

Primary files:
- `netbox_plant_graph/services/architecture_schema.py`
- `docs/v2_architecture_schema.md`

---

## Tier 2 — Stamp-Time Parameter Flexibility

### T2-A: Phased / Partial Plane Stamping

Today a stamp creates the full declared plane count. There is no mechanism to stamp two
planes today and stamp planes three and four six months later when hardware arrives.

**Target:** `StampTemplate.template` must support a `stamp_phases` list, where each
phase declares which planes it owns. `execute_stamp_template()` must accept a `phase`
parameter that limits execution to one declared phase. The `StampRun` manifest must
record which phases have been executed so the integrity audit can distinguish
planned-but-not-yet-stamped planes from topology errors. Rollback must be phase-scoped:
rolling back phase 2 must not destroy phase 1 objects.

Primary files:
- `netbox_plant_graph/services/stamping.py`
- `netbox_plant_graph/services/stamping_v25.py`

---

### T2-B: Per-Stamp Rack and Pod Count

Today the mini-proof fixture hard-codes counts: one GPU rack, one leaf, two shuffle
boards. Real deployments have highly variable rack-per-pod counts (e.g., 4, 8, 18, 36,
72 GPU trays per pod depending on the server generation and pod-size SKU).

**Target:** `StampTemplate.template` must expose a `topology_parameters` section with
named integer parameters (`gpu_tray_count`, `leaf_count_per_plane`, `racks_per_pod`,
`pods_per_fabric`). The stamping engine must use these parameters to drive the loop
counts in node and endpoint generation, replacing all current hard-coded loop ranges.
The schema validator must validate parameter bounds and cross-check that the declared
`topology_parameters` are self-consistent (for example,
`leaf_count_per_plane × planes = declared_uplink_count`). The preview diff must expose
the resolved parameter values alongside the generated change plan so operators see
exactly what the parameter choices produce.

Primary files:
- `netbox_plant_graph/services/stamping.py`
- `netbox_plant_graph/services/stamp_template_validation.py`
- `netbox_plant_graph/services/stamping_v25.py`

---

### T2-C: Wavelength Plan Parameterization

The engine today hard-codes four wavelengths in `DEFAULT_WAVELENGTHS_NM`: 1311, 1313,
1315, 1317 nm. These are correct for the current O-band WDM plan but will not match
every vendor's CWDM4 or FR4 channel plan.

**Target:** `StampTemplate.template` must support an optional `wavelength_plan` section
that declares `band` (`o_band`, `c_band`, `direct_detect`), `channel_count`, and
optionally a `channels` list of explicit nm values. The stamping engine must use the
declared plan when generating `OpticalLane` rows, falling back to the current default
only when `wavelength_plan` is absent for backward compatibility. The architecture schema
validator must cross-check `channel_count` against `channels_per_subinterface`.

Primary files:
- `netbox_plant_graph/services/stamping.py`
- `netbox_plant_graph/services/architecture_schema.py`

---

### T2-D: Name Pattern Customization

Today the name pattern for sub-interfaces is the single string from
`channel_subinterfaces.name_pattern`. Site-specific naming conventions vary enormously:
some operators use `osfp{port}/{channel}`, others use `e{rack}-{tray}.{port}.{channel}`,
others use SNMP ifIndex-compatible schemes.

**Target:** `StampTemplate.template` must support a `name_patterns` section with
per-role-kind pattern templates that accept a declared set of interpolation variables
(`{rack_id}`, `{tray_index}`, `{plane_index}`, `{port_index}`, `{channel_index}`,
`{node_address}`, `{fabric_slug}`). The stamp preview's `name_pattern_samples` output
must render the first N generated names from every pattern variable in the template,
with collision flags, before any writes happen. The schema validator must verify that
every declared variable in a pattern is in the supported variable set for that role kind.

Primary files:
- `netbox_plant_graph/services/stamping.py`
- `netbox_plant_graph/services/stamping_v25.py`

---

### T2-E: Selectable Allocation Rule Sets at Stamp Time

Today a `StampTemplate` references exactly one `FabricArchitecture`, and that
architecture has exactly one `AllocationRuleSet` for channel-subinterface mapping.
There is no mechanism to swap in a different allocation strategy at stamp time (for
example, using a shifted channel map for a specific fiber vendor's cassette polarity).

**Target:** `StampTemplate.template` must support an optional `allocation_rule_override`
key that names an alternative `AllocationRuleSet` slug. The stamping engine must apply
the overridden rule set when it is supplied, subject to a compatibility check (the
override's channel map must be a valid alternative map for the same architecture). The
stamp preview must show which rule set was selected and flag any delta from the
architecture default. This enables the same blueprint to stamp correctly against both
vendor A's and vendor B's pre-terminated cable plant without maintaining two separate
templates.

Primary files:
- `netbox_plant_graph/services/stamping.py`
- `netbox_plant_graph/services/stamping_v25.py`

---

### T2-F: Active/Dark Position Override per Stamp

Some fabric deployments intentionally leave specific MPO positions dark for future
expansion headroom. Others vary dark position sets between planes depending on cable
routing constraints.

**Target:** `StampTemplate.template` must support a `dark_position_overrides` section
that maps plane number or plane label to a replacement dark position list. The schema
validator must verify that all override lists are valid (no position both active and dark,
total coverage invariant holds). The topology-integrity audit's
`dark_mpo_position_usage` check must read the override from the fabric's
`StampRun.result` manifest rather than assuming the architecture default.

Primary files:
- `netbox_plant_graph/services/stamping.py`
- `netbox_plant_graph/services/topology_integrity.py`

---

### T2-G: Tenant and Ownership Injection

Today `Fabric.tenant` is set once at fabric creation. There is no mechanism in the stamp
template to declare which tenant, site, or location the stamped fabric should belong to.

**Target:** `StampTemplate.template` must support a `fabric_ownership` section with
optional `tenant_slug`, `scope_site_slug`, and `scope_location_slug`. At stamp-apply
time, these values are injected into the `Fabric` row (and into generated NetBox
`Device` rows when device creation is enabled). The stamp preview must resolve and
display the target tenant/site/location names before apply, and must flag a warning when
any referenced object does not yet exist in NetBox.

Primary files:
- `netbox_plant_graph/services/stamping.py`
- `netbox_plant_graph/services/stamping_v25.py`

---

## Tier 3 — Library Infrastructure

### T3-A: Versioned Blueprint Registry

Today `ArchitectureSchemaDefinition.version` is a free string (`v2`), and there is no
mechanism to query what blueprints exist, what versions they have, or what parameters
they accept.

**Target:** introduce a `BlueprintRegistry` service class (in a new
`services/blueprint_registry.py`) that:

- maintains a keyed registry of `(slug, version) → ArchitectureSchemaDefinition` entries;
- enforces that each registered entry passes `validate_architecture_schema()` at
  registration time and raises at import if it does not;
- exposes `list_blueprints()`, `get_blueprint(slug, version)`, and `latest_version(slug)`;
- is consumed by the architecture-compatibility helpers so they can validate against the
  registry rather than only the single hardcoded fixture.

The first three registered entries should be:

1. `roce-4-plane-gb300-2x2-shuffle` (current built-in)
2. `roce-4-plane-h100-direct-attach` (direct-attach 400G variant)
3. `roce-8-plane-gb300-2x2-shuffle` (8-plane scale-out variant)

Primary files:
- `netbox_plant_graph/services/blueprint_registry.py` (new)

---

### T3-B: Declarative Parameter Schema per Blueprint

Every registered blueprint must carry a `parameter_schema` field: a JSON Schema object
describing the `topology_parameters`, `wavelength_plan`, `name_patterns`,
`dark_position_overrides`, and `allocation_rule_override` inputs the blueprint accepts at
stamp time. When a `StampTemplate` is validated, the template's `topology_parameters`
and other stamp-time knobs must be validated against the linked blueprint's
`parameter_schema` before execution. Validation failures must appear in the stamp
preview's `issues` list with code `blueprint_parameter.*`.

Primary files:
- `netbox_plant_graph/services/blueprint_registry.py`
- `netbox_plant_graph/services/stamp_template_validation.py`

---

### T3-C: Blueprint Compatibility Matrix

A blueprint should be declaratively compatible with a set of NetBox DeviceType slugs.
An operator who tries to stamp a `roce-8-plane-gb300-2x2-shuffle` blueprint against a
NetBox instance that has only H100 device types registered should get an explicit
pre-flight failure, not a confusing runtime error.

**Target:** each `ArchitectureSchemaDefinition` must carry a `required_device_types`
mapping from role slug to a list of compatible NetBox DeviceType slugs. The stamp
preview's architecture-gate check must query `dcim.DeviceType` and report any missing
device types as `architecture_gate.missing_device_type` issues with error severity.
Include a management command `mpf_check_blueprint_compatibility` that runs this check
for all registered blueprints against the current NetBox instance and exits non-zero
on any gap.

Primary files:
- `netbox_plant_graph/services/blueprint_registry.py`
- `netbox_plant_graph/services/stamping_v25.py`
- `netbox_plant_graph/management/commands/mpf_check_blueprint_compatibility.py` (new)

---

### T3-D: External Blueprint Import via Payload

Today blueprints can only be added by writing Python code in the plugin. The
import/reconciliation pipeline can already ingest topology rows; it should also be
able to ingest new blueprint definitions.

**Target:** extend the import JSON schema with a new item kind
`fabric_architecture_blueprint`. An item of this kind carries all the fields of an
`ArchitectureSchemaDefinition` as a JSON payload plus `parameter_schema` and
`required_device_types`. The reconciliation engine must run
`validate_architecture_schema()` on the payload during dry-run, report any schema
violations as `conflict` outcomes, and then persist the definition as
`FabricArchitecture` + `ArchitectureRole` + `TransferPattern` + `AllocationRuleSet`
rows on apply. This enables vendor-supplied or community-maintained blueprint packages
to be imported without code changes.

Primary files:
- `netbox_plant_graph/services/imports/reconciliation.py`

---

### T3-E: Blueprint Test Harness

New blueprints added to the registry must have a proof-of-stamping test: a mini-fixture
test that stamps the blueprint against an in-memory test fabric, asserts that path
resolution returns at least one valid end-to-end lane path, and asserts that the
topology-integrity audit reports no blocking findings.

**Target:** introduce `netbox_plant_graph/tests/test_v2_blueprint_registry.py` that:

- asserts every registered blueprint passes `validate_architecture_schema()`;
- asserts the parameter schema for each registered blueprint is itself valid JSON Schema;
- for every blueprint that has a bundled stamp template, runs
  `preview_stamp_template_v25()` against a minimal synthetic NetBox setup and asserts
  zero error-severity preview issues;
- for all built-in blueprints, stamps a mini fabric and asserts at least one resolvable
  optical lane path;
- is added to the full-suite test invocation in `devrun/test.sh`.

Primary files:
- `netbox_plant_graph/tests/test_v2_blueprint_registry.py` (new)

---

### T3-F: Blueprint Versioning and Deprecation Lifecycle

**Target:** define a formal lifecycle for blueprint versions in code and in a new
`docs/blueprint_versioning_policy.md`:

- **Active**: fully supported, validated at import, accepted by the stamping engine.
- **Deprecated**: still importable and stampable, but the stamp preview emits a
  `warning`-severity `blueprint_deprecated` issue.
- **Retired**: rejected by the schema import gate and the stamping engine with an explicit
  error pointing to the successor version.

`BlueprintRegistry` must support `deprecate(slug, version, successor_version)` and
`retire(slug, version)`. The architecture-compatibility check must report
`blueprint_lifecycle` issues when a template references a deprecated or retired blueprint
version. This prevents operational debt from accumulating silently as the blueprint
library grows.

Primary files:
- `netbox_plant_graph/services/blueprint_registry.py`
- `docs/blueprint_versioning_policy.md` (new)

---

### T3-G: Canonical Blueprint Bundle Format

**Target:** define a single-file bundle format (`*.mpf-blueprint.json`) that packages:

- the `ArchitectureSchemaDefinition` payload,
- the `parameter_schema`,
- the `required_device_types` compatibility matrix,
- one or more seed `StampTemplate` payloads as named entries,
- a `bundle_version` and `bundle_author` field,
- a `schema_contract_version` that the importer checks against `ARCHITECTURE_SCHEMA_CONTRACT_VERSION`.

The `mpf_import_reconcile` command and the Import Preview UI must both accept
`.mpf-blueprint.json` files and route them through the same `fabric_architecture_blueprint`
item kind defined in T3-D. This gives the neocloud's infrastructure-as-code repository a
single artifact type that encodes "a complete fabric reference architecture" and can be
diffed, reviewed in pull requests, and applied atomically.

Primary files:
- `netbox_plant_graph/services/imports/reconciliation.py`
- `docs/v2_import_reconciliation.md`

---

## Summary Table

| Target | Primary file(s) | Net new surface |
|---|---|---|
| T1-A: Parameterized plane count | `architecture_schema.py`, `stamping.py` | `min_planes` / `max_planes` on definition; executor loop parameterized |
| T1-B: Generalized shuffle geometry | `architecture_schema.py` | Sub-validators for `shuffle_1x4`, `shuffle_2x2_mpo24`, `shuffle_4x4`, `direct_attach`, `polarity_type_b/c`, `shuffle_nxm` |
| T1-C: MPO position count variants | `architecture_schema.py`, `architecture.py` | Remove MPO=12 constant; validate against declared `mpo_position_count` |
| T1-D: Speed-heterogeneous roles | `architecture_schema.py`, `architecture.py` | `speed_gbps` + `channels_per_osfp` role metadata; multi-role speed validation |
| T1-E: Multi-tier spine roles | `architecture_schema.py`, `architecture.py`, `blast_radius.py` | `active_tier_2_device/port` role kinds; `fabric_tier` metadata enforcement; tier-grouped blast radius |
| T1-F: Frontend/management fabric class | `models.py`, `architecture_schema.py` | `fabric_class` field on `FabricArchitecture`; relaxed validator per class |
| T1-G: Custom transfer pattern validator | `architecture_schema.py` | `validator_entrypoint` field; dynamic entrypoint invocation at validation time |
| T2-A: Phased stamping | `stamping.py`, `stamping_v25.py` | `stamp_phases` in template spec; `phase` parameter on execute; phase-scoped rollback |
| T2-B: Rack/pod topology parameters | `stamping.py`, `stamp_template_validation.py` | `topology_parameters` section; validated loop-count parameters; preview renders resolved values |
| T2-C: Wavelength plan | `stamping.py`, `architecture_schema.py` | `wavelength_plan` section; channel count cross-check; `OpticalLane` creation uses plan |
| T2-D: Name pattern customization | `stamping.py`, `stamping_v25.py` | `name_patterns` section; variable set validation; per-role-kind pattern rendering in preview |
| T2-E: Allocation rule override | `stamping.py`, `stamping_v25.py` | `allocation_rule_override` in template; compatibility gate; preview shows selected rule set |
| T2-F: Active/dark position override | `stamping.py`, `topology_integrity.py` | `dark_position_overrides` section; per-plane validation; integrity audit reads from manifest |
| T2-G: Tenant/ownership injection | `stamping.py`, `stamping_v25.py` | `fabric_ownership` section; pre-flight lookup; injected into `Fabric` and `Device` rows |
| T3-A: Versioned blueprint registry | `services/blueprint_registry.py` (new) | `BlueprintRegistry` class; `list/get/latest`; at-registration validation |
| T3-B: Parameter schema per blueprint | `blueprint_registry.py`, `stamp_template_validation.py` | `parameter_schema` on definition; JSON Schema validation at preview time |
| T3-C: Compatibility matrix | `blueprint_registry.py`, `stamping_v25.py`, management command (new) | `required_device_types` on definition; `mpf_check_blueprint_compatibility` |
| T3-D: External blueprint import | `services/imports/reconciliation.py` | `fabric_architecture_blueprint` item kind; schema-validates then persists all sub-rows |
| T3-E: Blueprint test harness | `tests/test_v2_blueprint_registry.py` (new) | Per-blueprint schema + preview + stamp + path-resolution + integrity assertions |
| T3-F: Versioning lifecycle | `blueprint_registry.py` | `deprecate/retire` methods; `blueprint_deprecated`/`blueprint_lifecycle` preview issues |
| T3-G: Bundle format | `services/imports/`, docs | `.mpf-blueprint.json` schema; bundle import via UI and CLI; PR-diffable artifact |

---

## What These Targets Collectively Unlock

With all of the above in place, a neocloud's deployment teams can:

1. **Check in a `.mpf-blueprint.json` file per reference architecture** (H100 4-plane,
   GB300 8-plane, Vera Rubin 16-plane, frontside Clos, etc.) and treat blueprint changes
   with the same PR review discipline as software releases.

2. **Stamp any new pod by selecting a blueprint, dialing topology parameters**, and
   running the preview — no code edits, no per-site script maintenance.

3. **Phase deployments correctly**: hardware ordered but not yet delivered? Stamp phases 1
   and 2 now; stamp phase 3 six months later and the integrity audit will know the gap was
   planned.

4. **Swap allocation rule sets per cable vendor** without separate blueprints — one
   blueprint covers all MPO cassette polarity variants a given generation of GPU node will
   ever see.

5. **Add a third-party custom cassette geometry** by supplying a `validator_entrypoint`
   function, without forking the plugin.

6. **Validate every registered blueprint in CI** before merging any code change,
   eliminating the class of bugs where a blueprint that shipped in a release could never
   actually produce a valid stamped fabric.
