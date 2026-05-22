# Contributing

Contributions are welcome. This project moves quickly, so the most useful
changes are concrete, scoped, and verified against the current V2 data model.

## Project Direction

The active implementation is V2 of `netbox_plant_graph`, a plugin-native
multiplanar optical fabric model. Contributions should assume:

- modeled-fabric connectivity is owned by plugin tables, not NetBox-native
  `Cable`/`CablePath` state;
- physical cable assemblies are first-class plugin objects;
- optical paths are resolved from strands, MPO positions, optical lanes, and
  transfer maps;
- the standard object UI/API surface is generated from the V2 registry;
- custom workflow pages remain hand-wired;
- `netbox_floorplan` is not a runtime dependency.

## Local Setup

See [LOCAL_DEV_SETUP.md](LOCAL_DEV_SETUP.md). The local NetBox stack is driven
from:

```bash
./local-netbox-dev/scripts/up.sh
./local-netbox-dev/scripts/down.sh
```

For commands that need Django, run through the active NetBox container or local
NetBox environment. Typical examples:

```bash
python manage.py mpf_seed_v2 --architecture-only
python manage.py test netbox_plant_graph.tests.test_v2_ui --keepdb
```

## Making Changes

- Read the nearby code first and follow established patterns.
- Use `netbox_plant_graph/v2_registry.py` for standard model list/detail/CRUD/API
  plumbing instead of duplicating one-off boilerplate.
- Add or update serializers, forms, filters, tables, API viewsets, and tests
  through the registry when adding a new model.
- Keep workflow pages explicit when they have custom behavior, visualizations,
  or operator-specific flows.
- Keep migration risk in mind. Nullable cable-assembly linkage on
  `FiberStrand` is intentional so topology can be modeled before final cable
  plant assignment.
- Do not reintroduce floorplan-plugin coupling or NetBox-native cable/path
  reliance unless the architecture is explicitly revisited.

## Testing Expectations

Choose the smallest test slice that exercises the change:

- model/registry changes: registry, API, URL, and serializer tests;
- path semantics: resolver and path-query tests;
- visual workflow changes: focused UI tests plus browser verification when the
  local NetBox instance is running;
- audit/workflow changes: audit service, workflow API, and UI tests.

Full-suite testing can wait until several slices have accumulated, but focused
tests should accompany behavior changes.

## Documentation Expectations

Update docs in the same change when behavior or semantics shift. The current
docs map is [docs/README.md](docs/README.md).

At minimum, keep these current:

- [README.md](README.md) for user-facing capability summary;
- [docs/data_model.md](docs/data_model.md) for persistent model semantics;
- [docs/v2_graphql_contract_v2.md](docs/v2_graphql_contract_v2.md) for GraphQL
  contract changes;
- [docs/v2_cutover_runbook.md](docs/v2_cutover_runbook.md) for operator
  validation flows.

Historical plans and runbooks may remain in `docs/`, but they must be clearly
marked as historical if they describe V1 concepts or superseded architecture.

## Pull Request Guidance

Good PRs include:

- a short description of the operator or model behavior changed;
- migrations when schema changes require them;
- focused test output;
- screenshots for meaningful UI changes;
- documentation updates for visible behavior, contracts, or data model changes.

Avoid broad refactors bundled with feature work unless they are necessary to
complete the feature safely.
