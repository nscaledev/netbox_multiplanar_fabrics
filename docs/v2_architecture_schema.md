# V2 Architecture Schema Contract

The V2 architecture fixture is executable topology data, not just seed data. The schema validator in
`netbox_plant_graph.services.architecture_schema` checks the parts of that data that stamping and path resolution rely
on before they become database rows.

## Built-in RoCE blueprints

The default blueprint registry exposes three active built-in architecture
families:

- `roce-4-plane-gb300-2x2-shuffle` `v2`: GB300 four-plane 2x2 shuffle.
- `roce-4-plane-h100-direct-attach` `v2`: H100 four-plane direct attach.
- `roce-8-plane-gb300-2x2-shuffle` `v2`: GB300 eight-plane 2x2 shuffle.

Use:

- `build_roce_4plane_shuffle_architecture_schema()` for the GB300 four-plane
  shuffle fixture.
- `build_roce_4plane_h100_direct_attach_architecture_schema()` for the H100
  direct-attach fixture.
- `build_roce_8plane_gb300_shuffle_architecture_schema()` for the GB300
  eight-plane shuffle fixture.
- `get_default_blueprint_registry()` to resolve the active built-in registry
  entries, their lifecycle metadata, required device types, and bundled stamp
  templates.

Persisted built-in rows store `metadata.schema_contract_version` with the
current schema contract version. Registry-backed persisted architectures also
store the importable blueprint definition under `metadata.blueprint.definition`
so V2.5 stamping and import preflight can validate against the same contract
later.

Validation returns structured `ArchitectureSchemaError` objects with `code`, `path`, `message`, and optional `context`.
It does not raise assertions. Callers can show all errors, filter by code, or fail a deployment before writing rows.

## Definition Metadata

`ArchitectureSchemaDefinition` carries both the legacy executable surfaces and newer blueprint metadata:

- plane range: `min_planes`, `max_planes`, and `default_planes`;
- fabric class: one of `roce_backend`, `ethernet_frontend`, `management`, or `storage`;
- `parameter_schema`: a JSON Schema object for stamp-time parameters;
- `required_device_types`: role slug to compatible NetBox DeviceType slugs;
- lifecycle fields: `status` and `lifecycle`;
- provider hooks: `transfer_pair_providers` and `custom_validator_entrypoints`.

For custom transfer patterns, `rule.validator_entrypoint` must be a dotted Python import path to a callable. The callable
receives the transfer rule body and returns either `None`, an empty sequence, or a sequence of
`ArchitectureSchemaError` objects or mappings with `code`, `path`, `message`, and optional `context`. The reference
validator is `netbox_plant_graph.services.architecture_schema.noop_custom_transfer_validator`.

Custom validator entrypoints are trusted code. The default allowlist accepts only local
`netbox_plant_graph.*` entrypoints, including the shipped noop validators. Entry points outside that namespace fail
schema validation with a trust-policy error instead of being imported. Wrap vendor-specific validation in a local plugin
adapter before referencing it from architecture data.

## Persisted Architecture Preflight

Use `validate_persisted_architecture_schema(architecture)` when the source of truth is a `FabricArchitecture` row and
its related `ArchitectureRole`, `TransferPattern`, and `AllocationRuleSet` rows. The helper converts persisted rows back
into an `ArchitectureSchemaDefinition` and then runs the same validator used by the fixture.

The architecture detail page renders this preflight directly. It shows schema
version, declared schema contract version, validation status, built-in RoCE
compatibility status, error/warning rows, and a compact channel-map summary. If
related rows are malformed or incomplete, the page displays
`persisted_architecture.*` errors rather than raising a server error.

Persisted conversion currently expects:

- `channel_subinterface_mapping.rule.channel_map_matrix` on an allocation rule set.
- `shuffle_2x2.rule.groups[].active_position_groups` on a transfer pattern.
- MPO position counts from MPO role metadata, the shuffle rear-position transform, or the GB300 OSFP/MPO allocation rule.
- MPO-per-OSFP counts from OSFP role metadata, the GB300 OSFP/MPO allocation rule, or the channel map MPO indexes.

If a row cannot be converted, the helper returns `ArchitectureSchemaValidationResult` with
`persisted_architecture.*` error codes instead of raising. Once conversion succeeds, normal schema errors such as
`channel_map.*`, `mpo_positions.*`, and `shuffle_2x2.*` are returned.

## Compatibility Semantics

Use `compare_persisted_architecture_compatibility(architecture, expected_schema)` before applying stamp templates,
import payloads, or reconciliation hints that assume a specific architecture contract. The helper intentionally compares
only the stable contract surfaces needed by V2 preflight:

- architecture slug
- architecture version
- optional `metadata.schema_contract_version`
- channel map matrix
- MPO position count
- inferred dark MPO positions

The result is an `ArchitectureCompatibilityResult` with one of three statuses:

- `compatible`: no issues were found.
- `warning`: only warning-level drift was found. The current warning case is a missing schema contract version when the
  caller supplied an expected version.
- `incompatible`: at least one error-level issue was found, such as slug/version mismatch, schema contract mismatch,
  channel-map mismatch, MPO position-count mismatch, dark-position mismatch, or an unreadable persisted schema.

`ArchitectureCompatibilityResult.is_compatible` is true for `compatible` and warning-only results, and false for
`incompatible`. Callers that need strict metadata hygiene should reject `warning` explicitly.

## Stamp Preflight Integration

`preview_stamp_template_v25(...)` uses the same helpers as architecture
detail/schema tooling, plus blueprint-registry checks:

- `get_default_blueprint_registry()` selects the template's requested
  `architecture_slug`/`architecture_version`, or a compatible persisted
  architecture blueprint when one is attached to the template.
- `validate_architecture_schema(...)` checks the selected registry definition or
  persisted blueprint definition.
- `validate_blueprint_parameters(...)` checks stamp-time parameters against the
  selected blueprint `parameter_schema`.
- `check_blueprint_device_type_compatibility(...)` checks lifecycle state and
  required NetBox `DeviceType` availability.
- `validate_persisted_architecture_schema(architecture)` checks a persisted
  target when it can be inferred from the template FK or template architecture
  hints.
- `compare_persisted_architecture_compatibility(architecture, expected_schema)`
  runs only after the persisted schema is readable, and maps warning-only drift
  to non-blocking stamp preview warnings.

Stamp preview issue codes keep the architecture code as the suffix. Schema
validation appears as `architecture_schema.<schema-code>` or
`architecture_schema.<persisted-code>`, compatibility drift appears as
`architecture_compatibility.<compatibility-code>`, blueprint selection appears
as `architecture_gate.*`, and parameter/lifecycle checks appear as
`blueprint_parameter.*` or `blueprint_lifecycle`.

## Error Code Catalog

The codes below are stable for operator display and automation branching. New codes may be added as new architecture
families are added, but existing code meanings should not be reused for different validation failures.

Compatibility helpers can also wrap validation failures:

- `schema.<schema-code>`: the persisted architecture converted successfully, then failed normal schema validation.
- `persisted_schema.<persisted-code>`: the persisted architecture could not be converted into a schema definition.

### Compatibility

| Code | Stability | Notes |
| --- | --- | --- |
| `architecture_slug_mismatch` | Stable | Persisted architecture slug differs from the expected contract. |
| `architecture_version_mismatch` | Stable | Persisted architecture version differs from the expected contract. |
| `schema_contract_version_missing` | Stable | Metadata does not declare `schema_contract_version`; warning by default. |
| `schema_contract_version_mismatch` | Stable | Metadata declares an incompatible schema contract version. |
| `channel_map_matrix_mismatch` | Stable | Persisted channel map differs from the expected contract. |
| `mpo_position_count_mismatch` | Stable | Persisted MPO position count differs from the expected contract. |
| `dark_positions_mismatch` | Stable | Persisted active/dark position contract differs from the expected contract. |

### Generic Schema Shape

| Code | Stability | Notes |
| --- | --- | --- |
| `schema.string_required` | Stable | Required string value is missing or blank. |
| `schema.positive_int_required` | Stable | Required positive integer is missing or invalid. |
| `schema.sequence_required` | Stable | Required sequence value is missing or invalid. |

### Blueprint Metadata

| Code | Stability | Notes |
| --- | --- | --- |
| `fabric_class.unknown` | Stable | Fabric class is not part of the contract. |
| `plane_range.min_exceeds_max` | Stable | `min_planes` is larger than `max_planes`. |
| `plane_range.plane_count_out_of_range` | Stable | `plane_count` is outside the declared range. |
| `plane_range.default_out_of_range` | Stable | `default_planes` is outside the declared range. |
| `parameter_schema.type` | Stable | `parameter_schema` is not an object. |
| `parameter_schema.root_type` | Stable | `parameter_schema.type` is not `object`. |
| `parameter_schema.properties_type` | Stable | `parameter_schema.properties` is not an object. |
| `blueprint.status_unknown` | Stable | Blueprint status is not supported. |
| `blueprint.lifecycle_type` | Stable | Lifecycle metadata is not an object. |
| `blueprint.lifecycle_successor_required` | Stable | Retired blueprint lacks a successor version. |
| `mpo_position_count.unsupported` | Stable | MPO position count is not one of 8, 12, 16, or 24. |

### Required Device Types

| Code | Stability | Notes |
| --- | --- | --- |
| `required_device_types.type` | Stable | Device type compatibility matrix is not an object. |
| `required_device_types.role_slug_type` | Stable | A compatibility key is not a role slug string. |
| `required_device_types.unknown_role` | Stable | Compatibility matrix references an unknown role. |
| `required_device_types.empty` | Stable | A role lists no compatible DeviceType slugs. |
| `required_device_types.device_type_slug` | Stable | A compatible DeviceType entry is not a non-empty string. |

### Roles

| Code | Stability | Notes |
| --- | --- | --- |
| `roles.empty` | Stable | Role definitions are empty. |
| `roles.entry_type` | Stable | A role definition is not an object. |
| `roles.duplicate_slug` | Stable | A role slug appears more than once. |
| `roles.unknown_kind` | Stable | Role kind is not supported by the schema contract. |
| `roles.metadata_type` | Stable | Role metadata is not an object. |
| `roles.mpo_position_count_mismatch` | Stable | MPO role metadata disagrees with architecture MPO position count. |
| `roles.speed_gbps_required` | Stable | Active port role lacks positive `metadata.speed_gbps`. |
| `roles.channels_per_osfp_required` | Stable | Active port role lacks positive `metadata.channels_per_osfp`. |
| `roles.channel_speed_gbps_required` | Stable | Active port role has invalid `metadata.channel_speed_gbps`. |
| `roles.channels_per_osfp_mismatch` | Stable | Active port channel count differs from the architecture channel width. |
| `roles.legacy_channels_mismatch` | Stable | Legacy `metadata.channels` disagrees with `channels_per_osfp`. |
| `roles.speed_channel_mismatch` | Stable | Active port speed does not equal channels times channel speed. |
| `roles.fabric_tier_required` | Stable | Tier-2 role lacks `metadata.fabric_tier`. |
| `roles.fabric_tier_unknown` | Stable | Role fabric tier is not supported. |
| `roles.fabric_tier_parent_missing` | Stable | Tier-2 port lacks a matching tier device role. |

### MPO Positions

| Code | Stability | Notes |
| --- | --- | --- |
| `mpo_positions.active_groups_type` | Stable | Active position groups are not an object. |
| `mpo_positions.active_groups_empty` | Stable | No active position groups are defined. |
| `mpo_positions.active_group_width` | Stable | Active group width differs from channels-per-subinterface. |
| `mpo_positions.active_overlap` | Stable | A position appears in more than one active group. |
| `mpo_positions.duplicate_dark` | Stable | A dark position is listed more than once. |
| `mpo_positions.active_dark_overlap` | Stable | A position is both active and dark. |
| `mpo_positions.coverage` | Stable | Active plus dark positions do not cover every MPO position. |
| `mpo_positions.position_out_of_range` | Stable | A referenced MPO position exceeds the position count. |
| `mpo_positions.duplicate_position` | Stable | A position list contains duplicate entries. |

### Channel Map

| Code | Stability | Notes |
| --- | --- | --- |
| `channel_map.empty` | Stable | Channel map matrix is empty. |
| `channel_map.entry_type` | Stable | A channel map row is not an object. |
| `channel_map.subinterface_sequence` | Stable | Subinterface indexes are not contiguous. |
| `channel_map.channel_width` | Stable | A channel maps the wrong number of positions. |
| `channel_map.mpo_index_out_of_range` | Stable | Channel map references an MPO index outside the OSFP contract. |
| `channel_map.dark_position` | Stable | Channel map uses a dark MPO position. |
| `channel_map.inactive_position` | Stable | Channel map uses a position outside the active set. |
| `channel_map.duplicate_lane` | Stable | One MPO position is mapped more than once. |
| `channel_map.duplicate_subinterface` | Stable | A subinterface index appears more than once. |
| `channel_map.missing_active_positions` | Stable | Active positions are missing from the channel map. |

### Transfer Patterns

| Code | Stability | Notes |
| --- | --- | --- |
| `transfer_patterns.empty` | Stable | Transfer pattern definitions are empty. |
| `transfer_patterns.entry_type` | Stable | A transfer pattern definition is not an object. |
| `transfer_patterns.duplicate_slug` | Stable | A transfer pattern slug appears more than once. |
| `transfer_patterns.unknown_kind` | Stable | Transfer pattern kind is not supported by the schema contract. |
| `transfer_patterns.rule_type` | Stable | Transfer pattern rule is not an object. |
| `identity.rule_type` | Stable | Identity rule type is not `position_map`. |
| `identity.mode` | Stable | Identity rule mode is not `identity`. |
| `stagger.rule_type` | Stable | Stagger rule type is not `allocation_transform`. |
| `stagger.duplicate_members` | Stable | Staggered members are not unique. |

### Generalized Position-Map Transfers

| Code | Stability | Notes |
| --- | --- | --- |
| `transfer_patterns.matrix_rule_type` | Stable | Generalized transfer rule type is not `position_map`. |
| `transfer_patterns.bidirectional_type` | Stable | Generalized transfer bidirectional flag is not boolean. |
| `transfer_patterns.position_count_mismatch` | Stable | Transfer rule position count differs from architecture MPO count. |
| `transfer_patterns.mpo24_position_count` | Stable | `shuffle_2x2_mpo24` is used without MPO-24 geometry. |
| `transfer_patterns.provider_missing` | Stable | Generalized transfer kind lacks a Python pair provider. |
| `transfer_patterns.front_mpo_count` | Stable | Named geometry has the wrong front MPO count. |
| `transfer_patterns.rear_mpo_count` | Stable | Named geometry has the wrong rear MPO count. |
| `transfer_patterns.front_rear_count_mismatch` | Stable | Paired polarity/direct attach geometry has uneven front/rear counts. |
| `transfer_patterns.shuffle_nxm_dimension` | Stable | Generic N×M counts differ from declared MPO lists. |
| `transfer_patterns.matrix_entry_type` | Stable | Generalized matrix row is not an object. |
| `transfer_patterns.matrix_unknown_mpo` | Stable | Generalized matrix row references an undeclared MPO. |
| `transfer_patterns.matrix_duplicate_crossing` | Stable | Generalized matrix repeats a front/rear crossing. |
| `transfer_patterns.matrix_missing_crossing` | Stable | Generalized matrix omits an expected front/rear crossing. |
| `transfer_patterns.matrix_position_pairs_type` | Stable | Generalized row lacks sequence-shaped `position_pairs`. |
| `transfer_patterns.matrix_position_pair_type` | Stable | A generalized position pair is not a sequence. |
| `transfer_patterns.matrix_position_pair_width` | Stable | A generalized position pair is not source/destination width. |
| `transfer_patterns.matrix_pair_position` | Stable | A generalized position pair exceeds the MPO position count. |
| `transfer_patterns.matrix_pair_duplicate` | Stable | A generalized position pair is duplicated. |
| `transfer_patterns.matrix_pair_mismatch` | Stable | Declared pairs differ from provider output. |
| `transfer_patterns.helper_error` | Stable | Generalized provider raised during validation. |

#### Operational Maturity

Generalized transfer geometry support is staged:

| Geometry | Schema-supported | Preview-supported | Execution-supported |
| --- | --- | --- | --- |
| `shuffle_2x2` | Yes, with the dedicated 2x2 validator. | Yes, for the bundled GB300 four-plane and eight-plane mini-proof templates. | Yes, through the current V2.5 GB300 shuffle executors. |
| `direct_attach` | Yes, through generalized position-map validation and a provider. | Yes, for the bundled H100 direct-attach mini-proof template. | Yes, through the current V2.5 H100 direct-attach executor. |
| `polarity_type_b`, `polarity_type_c` | Yes, when a provider is supplied. | No generic object-diff preview yet. | No V2.5 executor yet. |
| `shuffle_1x4`, `shuffle_2x2_mpo24`, `shuffle_4x4`, `shuffle_nxm` | Yes, when declared dimensions and provider output match. | No generic object-diff preview yet. | No V2.5 executor yet. |
| `custom` | Yes, for trusted validator-entrypoint contracts. | No generic object-diff preview yet. | No generic executor. |

`Schema-supported` means the architecture contract can reject malformed geometry. `Preview-supported` means
`preview_stamp_template_v25(...)` can produce concrete create/update rows for a template using that geometry.
`Execution-supported` means `apply_stamp_template_v25(...)` can reconcile the corresponding plugin objects.

### Custom Validators

| Code | Stability | Notes |
| --- | --- | --- |
| `custom.validator_entrypoint_required` | Stable | Custom transfer rule lacks `validator_entrypoint`. |
| `custom.validator_entrypoint_format` | Stable | Custom transfer entrypoint is not a dotted import path. |
| `custom.validator_entrypoint_not_allowed` | Stable | Custom transfer entrypoint is outside the allowed local namespace. |
| `custom.validator_entrypoint_import_error` | Stable | Custom transfer entrypoint could not be imported. |
| `custom.validator_entrypoint_not_callable` | Stable | Custom transfer entrypoint is not callable. |
| `custom.validator_error` | Stable | Custom transfer validator raised an exception. |
| `custom.validator_result_type` | Stable | Custom transfer validator returned a non-sequence result. |
| `custom.validator_result_entry_type` | Stable | Custom transfer validator returned an invalid error entry. |
| `architecture_validators.entrypoint_format` | Stable | Architecture validator entrypoint is not a dotted import path. |
| `architecture_validators.entrypoint_not_allowed` | Stable | Architecture validator entrypoint is outside the allowed local namespace. |
| `architecture_validators.entrypoint_import_error` | Stable | Architecture validator entrypoint could not be imported. |
| `architecture_validators.entrypoint_not_callable` | Stable | Architecture validator entrypoint is not callable. |
| `architecture_validators.validator_error` | Stable | Architecture-level validator raised an exception. |
| `architecture_validators.validator_result_type` | Stable | Architecture-level validator returned a non-sequence result. |
| `architecture_validators.validator_result_entry_type` | Stable | Architecture-level validator returned an invalid error entry. |

### 2x2 Shuffle

| Code | Stability | Notes |
| --- | --- | --- |
| `shuffle_2x2.rule_type` | Stable | 2x2 shuffle rule type is not `position_map`. |
| `shuffle_2x2.bidirectional_type` | Stable | 2x2 shuffle bidirectional flag is not boolean. |
| `shuffle_2x2.group_type` | Stable | A shuffle group is not an object. |
| `shuffle_2x2.front_mpo_count` | Stable | A shuffle group does not define exactly two front MPOs. |
| `shuffle_2x2.rear_mpo_count` | Stable | A shuffle group does not define exactly two rear MPOs. |
| `shuffle_2x2.active_groups_type` | Stable | A shuffle group active-position spec is not an object. |
| `shuffle_2x2.active_group_labels` | Stable | A shuffle group does not expose canonical `A` and `B` groups. |
| `shuffle_2x2.active_group_mismatch` | Stable | Shuffle group active positions differ from the architecture active positions. |
| `shuffle_2x2.transform_type` | Stable | Rear position transform is not an object. |
| `shuffle_2x2.transform_kind` | Stable | Rear position transform kind is not supported. |
| `shuffle_2x2.transform_position_count` | Stable | Rear transform position count differs from architecture position count. |
| `shuffle_2x2.matrix_entry_type` | Stable | A shuffle matrix row is not an object. |
| `shuffle_2x2.matrix_unknown_group` | Stable | A matrix row references an unknown active position group. |
| `shuffle_2x2.matrix_transform` | Stable | Matrix row does not match key-down-roll transform semantics. |
| `shuffle_2x2.matrix_duplicate_crossing` | Stable | Matrix contains duplicate front/rear/group crossing. |
| `shuffle_2x2.matrix_missing_crossing` | Stable | Matrix is missing an expected front/rear/group crossing. |
| `shuffle_2x2.helper_cross_group` | Stable | Shuffle helper returned a cross-group pair. |
| `shuffle_2x2.helper_mismatch` | Stable | Shuffle helper output does not match matrix semantics. |
| `shuffle_2x2.helper_error` | Stable | Shuffle helper raised an exception during validation. |
| `shuffle_mpo_groups.group_size` | Stable | A 2x2 shuffle MPO group does not contain exactly two MPOs. |
| `shuffle_mpo_groups.overlap` | Stable | An MPO belongs to more than one shuffle MPO group. |

### Allocation Rules

| Code | Stability | Notes |
| --- | --- | --- |
| `allocation_rules.empty` | Stable | Allocation rule sets are empty. |
| `allocation_rules.entry_type` | Stable | An allocation rule set is not an object. |
| `allocation_rules.duplicate_slug` | Stable | An allocation rule set slug appears more than once. |
| `allocation_rules.rule_type` | Stable | Allocation rule set rule is not an object. |
| `allocation_rules.missing_channel_map` | Stable | Required `channel_subinterface_mapping` rule set is missing. |
| `allocation_rules.child_name_pattern` | Stable | Child name pattern does not contain `{channel_index}`. |
| `allocation_rules.channel_map_type` | Stable | Channel map rule does not expose a matrix sequence. |
| `allocation_rules.channel_map_mismatch` | Stable | Channel map rule matrix differs from architecture channel map. |

### Persisted Architecture Conversion

| Code | Stability | Notes |
| --- | --- | --- |
| `persisted_architecture.missing_relation` | Stable | Required related manager/sequence is missing. |
| `persisted_architecture.relation_type` | Stable | Related data is not a related manager or sequence. |
| `persisted_architecture.channel_map_rule_type` | Stable | Persisted channel-map rule is not an object. |
| `persisted_architecture.channel_map_missing` | Stable | Persisted channel-map rule lacks `channel_map_matrix`. |
| `persisted_architecture.channel_map_rule_missing` | Stable | Persisted architecture lacks the channel-map allocation rule. |
| `persisted_architecture.shuffle_rule_type` | Stable | Persisted shuffle rule is not an object. |
| `persisted_architecture.shuffle_groups_missing` | Stable | Persisted shuffle rule lacks groups. |
| `persisted_architecture.shuffle_group_type` | Stable | Persisted shuffle group is not an object. |
| `persisted_architecture.active_position_groups_missing` | Stable | Persisted shuffle groups do not expose active position groups. |
| `persisted_architecture.active_position_groups_conflict` | Stable | Persisted shuffle groups disagree on active position groups. |
| `persisted_architecture.mpo_position_count_type` | Stable | Persisted MPO position count candidate is not a positive integer. |
| `persisted_architecture.mpo_position_count_missing` | Stable | Persisted architecture cannot infer MPO position count. |
| `persisted_architecture.mpo_position_count_conflict` | Stable | Persisted architecture exposes conflicting MPO position counts. |
| `persisted_architecture.channel_width_missing` | Stable | Persisted architecture cannot infer channels-per-subinterface. |
| `persisted_architecture.mpo_count_per_osfp_type` | Stable | Persisted MPO-per-OSFP candidate is not a positive integer. |
| `persisted_architecture.mpo_count_per_osfp_missing` | Stable | Persisted architecture cannot infer MPO count per OSFP. |
| `persisted_architecture.mpo_count_per_osfp_conflict` | Stable | Persisted architecture exposes conflicting MPO-per-OSFP counts. |

## Required semantics

Architecture definitions must provide these schema surfaces:

- Blueprint metadata: `plane_count` must be within `min_planes..max_planes`, `default_planes` must be within the same
  range, `fabric_class` must be supported, and non-empty `parameter_schema` values must be object-shaped JSON Schema.
- Roles: every role has a unique `slug`, non-empty `name`, supported `role_kind`, and object-shaped `metadata`.
  MPO connector roles must declare `metadata.position_count` equal to the architecture MPO position count. Active port
  roles must declare `metadata.speed_gbps`, `metadata.channels_per_osfp`, and optionally
  `metadata.channel_speed_gbps`; speed must equal channels times channel speed.
- Transfer patterns: every pattern has a unique `slug`, supported `pattern_kind`, and object-shaped `rule`.
  `identity`, `stagger`, `shuffle_2x2`, generalized position-map kinds, and `custom` have additional checks.
- Allocation rules: every rule set has a unique `slug`, non-empty `name`, and object-shaped `rule`.
  `channel_subinterface_mapping` is required and must carry the same channel map matrix as the architecture.
- MPO positions: every physical MPO position must be declared either active or dark. Active groups must be disjoint,
  dark positions must be disjoint from active positions, and all positions must be within `1..mpo_position_count`.
- Channel map matrix: subinterface indexes must be contiguous, each channel maps the configured number of positions,
  no MPO position may be assigned twice, and dark positions may not be assigned.
- 2x2 shuffle: active groups are canonical `A` and `B` groups, each shuffle group contains two MPOs, matrix rows cover
  every front/rear crossing once, and destination positions apply the key-down-roll transform.
- Generalized transfer kinds: `direct_attach`, `polarity_type_b`, `polarity_type_c`, `shuffle_1x4`,
  `shuffle_2x2_mpo24`, `shuffle_4x4`, and `shuffle_nxm` use `front_mpos`, `rear_mpos`, a position-pair `matrix`, and
  a named Python provider supplied by `transfer_pair_providers`.
- Custom transfers: `rule.validator_entrypoint` is mandatory and is invoked during schema validation.

## Architecture Workspace Integration

The first-class architecture workspace is now the preferred operator/API path
for publishing new or revised architecture definitions. A workspace can ingest
an `.mpf`-style blueprint bundle, raw architecture schema JSON, stamp-template
JSON, or manual/API component rows, normalize those inputs, run schema/import
validation, generate a stable publish plan, and publish through the
`fabric_architecture_blueprint` import handler.

Built-in registry fixtures still live in code, but site-specific or
operator-authored blueprint variants should flow through `Build & Run ->
Architecture Workspaces` so provenance, validation runs, plan approval, and
handoff JSON are retained.

Operators preparing workspace source artifacts should use
`docs/v2_architecture_workspace_payloads.md` for the bundle/API/manual payload
formats. This document defines the inner executable architecture schema; the
workspace-payload guide explains how that schema is wrapped for ingestion.

## Adding Future Architectures

1. Add architecture constants and fixture definitions in the architecture
   service.
2. Build an `ArchitectureSchemaDefinition` for that fixture, including
   fabric-class metadata, plane range, parameter schema, required device types,
   active/dark MPO positions, and any transfer-pair provider used by stamping.
3. Register the fixture as a `BlueprintRegistryEntry` in
   `services/blueprint_registry.py` when it should be selectable by V2.5
   stamping or import preflight.
4. Add focused schema and registry tests proving the fixture validates and
   representative broken definitions fail with clear error codes.
5. For site-specific or operator-authored variants, exercise the architecture
   workspace publish flow with a blueprint bundle before using the architecture
   in onboarding or stamping.
6. Only seed database objects after the schema result is valid. If a future
   architecture needs a new transform kind, extend `architecture_schema.py` with
   a named validator/provider instead of embedding assumptions inside stamping
   code.
