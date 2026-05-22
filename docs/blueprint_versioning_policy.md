# Blueprint Versioning Policy

Status: active
Last updated: 2026-05-22

MPF blueprint versions are immutable architecture contracts keyed by
`(slug, version)`. A new version is required when a blueprint changes its
topology semantics, role contract, transfer pattern, channel map, accepted stamp
parameters, or required NetBox `DeviceType` compatibility matrix.

## Lifecycle States

`active`

The blueprint is fully supported. The registry validates its
`ArchitectureSchemaDefinition` at registration time, accepts it for import, and
allows compatibility checks to pass when the required NetBox device types are
present.

`deprecated`

The blueprint remains importable and stampable, but operators should move to the
declared successor version. Registry lifecycle checks emit a warning-severity
`blueprint_lifecycle` issue. Deprecation must include a `successor_version`
wherever one exists.

`retired`

The blueprint must not be used for new imports or stamping workflows. Registry
lifecycle checks emit an error-severity `blueprint_lifecycle` issue, and
blueprint import rejects payloads that declare a retired lifecycle.

## Compatibility Checks

Every registry entry carries `required_device_types`, a mapping from blueprint
role slug to one or more compatible NetBox `dcim.DeviceType.slug` values.

Run the catalog check with:

```bash
python manage.py mpf_check_blueprint_compatibility
```

The command checks every registered blueprint and exits non-zero when any role
has no compatible `DeviceType` in the current NetBox instance. JSON output is
available for CI:

```bash
python manage.py mpf_check_blueprint_compatibility --json
```

## Versioning Rules

- Keep a version immutable after release. Add a new version rather than editing
  an active production contract in place.
- Use deprecation before retirement when operators need time to migrate stamped
  fabrics or IaC bundles.
- Record the successor version when deprecating or retiring a blueprint.
- Keep `parameter_schema` backward compatible within a version. Tightening a
  parameter bound or removing an accepted parameter requires a new version.
- Keep `required_device_types` additive within a version. Removing a compatible
  slug or changing role meanings requires a new version.

## Current Built-ins

- `roce-4-plane-gb300-2x2-shuffle` `v2`: active GB300 four-plane shuffle proof.
- `roce-4-plane-h100-direct-attach` `v2`: active H100 direct-attach contract
  using the first-class `direct_attach` transfer kind and bundled mini proof
  template. Preview/apply is wired through the `roce_direct_attach_mini_proof`
  executor, which creates direct H100-to-leaf strands without shuffle
  `TransferMap` rows.
- `roce-8-plane-gb300-2x2-shuffle` `v2`: active GB300 eight-plane contract using
  the validated 2x2 shuffle semantics and a bundled mini proof template with
  `topology_parameters.plane_count=8`.
