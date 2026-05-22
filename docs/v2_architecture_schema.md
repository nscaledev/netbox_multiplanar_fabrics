# V2 Architecture Schema Contract

The V2 architecture fixture is executable topology data, not just seed data. The schema validator in
`netbox_plant_graph.services.architecture_schema` checks the parts of that data that stamping and path resolution rely
on before they become database rows.

## Built-in RoCE fixture

The current built-in architecture is `roce-4-plane-gb300-2x2-shuffle` version `v2`. Use:

- `build_roce_4plane_shuffle_architecture_schema()` to get the fixture as an `ArchitectureSchemaDefinition`.
- `validate_roce_4plane_shuffle_architecture_fixture()` to validate the built-in data and receive an
  `ArchitectureSchemaValidationResult`.
- The persisted built-in row stores `metadata.schema_contract_version` with the current schema contract version.

Validation returns structured `ArchitectureSchemaError` objects with `code`, `path`, `message`, and optional `context`.
It does not raise assertions. Callers can show all errors, filter by code, or fail a deployment before writing rows.

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

`preview_stamp_template_v25(...)` uses the same helpers as architecture detail/schema tooling:

- `validate_roce_4plane_shuffle_architecture_fixture()` checks the built-in fixture contract.
- `validate_persisted_architecture_schema(architecture)` checks a persisted target when it can be inferred from the
  template FK or a unique template `architecture_slug`/`architecture_version` hint.
- `compare_persisted_architecture_compatibility(architecture, expected_schema)` runs only after the persisted schema is
  readable, and maps warning-only drift to non-blocking stamp preview warnings.

Stamp preview issue codes keep the architecture code as the suffix. Schema validation appears as
`architecture_schema.<schema-code>` or `architecture_schema.<persisted-code>`, while compatibility drift appears as
`architecture_compatibility.<compatibility-code>`.

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

### Roles

| Code | Stability | Notes |
| --- | --- | --- |
| `roles.empty` | Stable | Role definitions are empty. |
| `roles.entry_type` | Stable | A role definition is not an object. |
| `roles.duplicate_slug` | Stable | A role slug appears more than once. |
| `roles.unknown_kind` | Stable | Role kind is not supported by the schema contract. |
| `roles.metadata_type` | Stable | Role metadata is not an object. |
| `roles.mpo_position_count_mismatch` | Stable | MPO role metadata disagrees with architecture MPO position count. |

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

- Roles: every role has a unique `slug`, non-empty `name`, supported `role_kind`, and object-shaped `metadata`.
  MPO-12 connector roles must declare `metadata.position_count` equal to the architecture MPO position count.
- Transfer patterns: every pattern has a unique `slug`, supported `pattern_kind`, and object-shaped `rule`.
  `identity`, `stagger`, and `shuffle_2x2` have additional rule-shape checks.
- Allocation rules: every rule set has a unique `slug`, non-empty `name`, and object-shaped `rule`.
  `channel_subinterface_mapping` is required and must carry the same channel map matrix as the architecture.
- MPO positions: every physical MPO position must be declared either active or dark. Active groups must be disjoint,
  dark positions must be disjoint from active positions, and all positions must be within `1..mpo_position_count`.
- Channel map matrix: subinterface indexes must be contiguous, each channel maps the configured number of positions,
  no MPO position may be assigned twice, and dark positions may not be assigned.
- 2x2 shuffle: active groups are canonical `A` and `B` groups, each shuffle group contains two MPOs, matrix rows cover
  every front/rear crossing once, and destination positions apply the key-down-roll transform.

## Adding Future Architectures

1. Add architecture constants and fixture definitions in the architecture service.
2. Build an `ArchitectureSchemaDefinition` for that fixture, including active/dark MPO positions and any transform
   helper used by stamping.
3. Add focused schema tests proving the fixture validates and representative broken definitions fail with clear error
   codes.
4. Only seed database objects after the schema result is valid. If a future architecture needs a new transform kind,
   extend `architecture_schema.py` with a named validator instead of embedding assumptions inside stamping code.
