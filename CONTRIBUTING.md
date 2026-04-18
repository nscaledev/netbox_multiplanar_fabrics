# Contributing

Contributions are welcome, and they are greatly appreciated. Every little bit
helps, and credit will always be given.

We want to make contributing to this project as easy and transparent as
possible, whether that means:

- Reporting a bug
- Discussing the current state of the code
- Submitting a fix
- Proposing a feature
- Becoming a maintainer

## General Tips for Working on GitHub

- Register for a free GitHub account if you have not already.
- Use GitHub Markdown for formatting text and screenshots when useful.
- Please avoid bumping issues with no activity. Use reactions instead.
- Avoid pinging maintainers with `@` unless they are already involved in that issue or PR.
- Keep issue and PR discussion concrete and reproducible.

## Types of Contributions

### Report Bugs

Report bugs at https://github.com/menckend/netbox_plant_graph/issues.

If you are reporting a bug, please include:

- Your operating system name and version.
- Any details about your local setup that might help reproduce the issue.
- Clear steps to reproduce the bug.
- Any traceback, failing query, or failing test output you have.

### Fix Bugs

Look through the GitHub issues for bugs. Anything tagged with `bug` and `help wanted` is open to whoever wants to implement it.

### Implement Features

Look through the GitHub issues for features. Anything tagged with `enhancement` and `help wanted` is open to whoever wants to implement it.

### Write Documentation

NetBox Plant Graph can always use more documentation, whether in the README,
`LOCAL_DEV_SETUP.md`, `netbox_plant_graph_plugin_design.md`, docstrings, or
focused implementation notes.

### Submit Feedback

The best way to send feedback is to file an issue at https://github.com/menckend/netbox_plant_graph/issues.

If you are proposing a feature:

- Explain in detail how it would work.
- Keep the scope as narrow as possible.
- Prefer incremental changes over broad speculative refactors.

## Get Started

For local development setup instructions, see `LOCAL_DEV_SETUP.md`.

This repo follows the same general local-development pattern as `netbox_rpki`,
but it has its own `devrun` wrapper, Compose project name, and plugin-specific
workflows.

### Generating Migrations Locally

In the standard local workspace layout from `LOCAL_DEV_SETUP.md`, NetBox's
management entry point lives at:

```bash
~/src/netbox-v4.5.7/netbox/manage.py
```

Use the pinned NetBox virtualenv and the plugin's test configuration when
generating plugin migrations:

```bash
~/.virtualenvs/netbox-4.5.7/bin/python \
    ~/src/netbox-v4.5.7/netbox/manage.py \
    makemigrations netbox_plant_graph \
    --settings=netbox_plant_graph.tests.netbox_configuration
```

If your local checkout or virtualenv uses a different pinned NetBox version,
adjust the `netbox-v4.5.7` and `netbox-4.5.7` path segments accordingly.

### Test Lane Expectations

Use the `devrun` wrapper for local test runs:

```bash
cd ~/src/netbox_multiplanar_fabrics/devrun
./dev.sh test fast
./dev.sh test contract
./dev.sh test full
```

Lane intent:

- `fast`: low-cost structural smoke checks for registry wiring and surface registration
- `contract`: registry, UI, API, and GraphQL contract coverage
- `full`: the full plugin suite, including sync, resolver, audit, and GraphQL execution coverage

The wrapper prepares the plugin test configuration and expects valid local
PostgreSQL credentials. In the standard local setup, `./dev.sh test ...` is the
right entry point; do not bypass it with ad hoc `manage.py test` commands unless
you know exactly which environment variables you need.

Use the same wrapper for targeted runs:

```bash
cd ~/src/netbox_multiplanar_fabrics/devrun
./dev.sh test netbox_plant_graph.tests.test_sync --verbosity 2
./dev.sh test netbox_plant_graph.tests.test_resolver --verbosity 2
./dev.sh test netbox_plant_graph.tests.test_graphql --verbosity 2
```

For local development that needs the full NetBox stack, use:

```bash
cd ~/src/netbox_multiplanar_fabrics/devrun
./dev.sh start
./dev.sh status
./dev.sh stop
```

## Pull Request Guidelines

Before you submit a pull request, check that it meets these guidelines:

1. The pull request should include tests when behavior changes.
2. If the pull request adds or changes functionality, update the docs in the same slice.
3. The pull request should work for Python 3.12, 3.13, and 3.14.

## Registry-Based Architecture

This plugin uses a registry-driven surface architecture modeled after the one in
`netbox_rpki`. The goal is to keep Django models and graph logic explicit while
generating repetitive NetBox surfaces from metadata.

Read these files together when you are doing architecture work:

- `netbox_plant_graph/object_specs.py`
- `netbox_plant_graph/object_registry.py`
- `netbox_plant_graph/detail_specs.py`
- `netbox_plant_graph/navigation.py`
- `netbox_plant_graph/views.py`
- `netbox_plant_graph/urls.py`
- `netbox_plant_graph/forms.py`
- `netbox_plant_graph/filtersets.py`
- `netbox_plant_graph/tables.py`
- `netbox_plant_graph/api/serializers.py`
- `netbox_plant_graph/api/views.py`
- `netbox_plant_graph/api/urls.py`
- `netbox_plant_graph/graphql/filters.py`
- `netbox_plant_graph/graphql/types.py`
- `netbox_plant_graph/graphql/schema.py`
- `netbox_plant_graph/services/sync/`
- `netbox_plant_graph/services/graph/`
- `netbox_plant_graph/jobs.py`
- `netbox_plant_graph/tests/registry_scenarios.py`
- `netbox_plant_graph/tests/topology.py`
- `netbox_plant_graph/tests/test_views.py`
- `netbox_plant_graph/tests/test_api.py`
- `netbox_plant_graph/tests/test_graphql.py`
- `netbox_plant_graph/tests/test_sync.py`
- `netbox_plant_graph/tests/test_resolver.py`
- `netbox_plant_graph_plugin_design.md`

### Core Architecture

Keep explicit:

- Django models
- Django migrations
- topology extraction, transformation, and rebuild logic
- path resolution, audits, and blast-radius behavior
- jobs and orchestration
- complex validation
- any GraphQL operation that is not standard model exposure
- any detail page that cannot be described cleanly with metadata

Keep registry-driven:

- standard REST serializers
- standard REST viewsets
- API router registration
- standard forms
- filter forms
- filtersets
- tables
- standard list, detail, edit, and delete views
- standard UI URL registration
- navigation and menu registration
- GraphQL filters, types, and query field registration
- shared smoke and surface-contract tests

The registry exists to eliminate repeated plumbing. It does not exist to hide
domain behavior or to dynamically invent the model layer.

### The Spec Contract

`netbox_plant_graph/object_specs.py` defines the active metadata contract.

Each `ObjectSpec` includes the important parts of one object family:

- `registry_key`
- `model`
- `labels`
- `routes`
- `api`
- `filterset`
- `graphql`
- `navigation`
- `form`
- `filter_form`
- `table`
- `view`

Treat `ObjectSpec` as a real contract. The generator modules build concrete
classes, routes, menus, and GraphQL fields from it.

### Internal Identity Versus Public Naming

Do not overload one identifier to serve every role.

This plugin separates:

- `registry_key`: internal plugin identity
- `RouteSpec.slug`: UI route stem
- `RouteSpec.path_prefix`: UI path segment when it should not be derived from the slug
- `ApiSpec.basename`: REST router basename
- `GraphQLSpec.detail_field_name`: singular GraphQL field name
- `GraphQLSpec.list_field_name`: list GraphQL field name

Rules:

- `registry_key` should stay stable and internal.
- Public names should be chosen deliberately.
- Do not assume model class names are safe public names.
- If the standard builder defaults are not safe, make the naming explicit.

### Generation Pipeline

The registry feeds nearly every standard surface in the plugin.

#### UI generation

`netbox_plant_graph/views.py` generates standard list, detail, edit, and delete
view classes.

Important behavior:

- list actions are derived from whether the object has an edit view
- detail actions are derived from whether the object has edit and delete views
- read-only objects do not get generated edit or delete actions
- simple detail pages are generated from metadata
- richer detail pages come from `detail_specs.py`

#### Form and filter generation

`netbox_plant_graph/forms.py` generates model forms and filter forms.

`netbox_plant_graph/filtersets.py` generates filtersets and implements shared
text-search behavior from `spec.filterset.search_fields`.

#### REST API generation

`netbox_plant_graph/api/serializers.py` generates serializers and exposes
`SERIALIZER_CLASS_MAP` keyed by `registry_key`.

`netbox_plant_graph/api/views.py` generates standard `NetBoxModelViewSet`
subclasses and exposes `VIEWSET_CLASS_MAP` keyed by `registry_key`.

Important behavior:

- read-only objects get `http_method_names = ["get", "head", "options"]`
- writable objects use normal NetBox model viewset behavior
- custom behavior should be implemented explicitly by subclassing a generated viewset when needed

#### GraphQL generation

`netbox_plant_graph/graphql/filters.py` generates filter classes.

`netbox_plant_graph/graphql/types.py` generates GraphQL types.

`netbox_plant_graph/graphql/schema.py` registers both:

- model-backed GraphQL fields from the registry
- operational queries such as `resolvePath`, `planeAudit`, and `blastRadius`

Important behavior:

- generated GraphQL field names are metadata, not accidents
- the GraphQL package must continue to re-export `schema` from `netbox_plant_graph/graphql/__init__.py`
- operational GraphQL queries belong in explicit resolver functions, not in registry metadata

## Adding a New Model

Follow this checklist.

### Step 1: Add the model explicitly

Add the Django model in `netbox_plant_graph/models.py`.

Also add or confirm:

- `Meta.ordering` if needed
- `__str__`
- `get_absolute_url`
- any validation or `clean()` methods
- any managers or queryset behavior
- the migration

Do not dynamically generate Django models.

### Step 2: Decide whether the model belongs in the registry

Ask these questions:

- Does it need a normal list or detail page?
- Does it need add, edit, or delete UI?
- Does it need a standard REST API surface?
- Does it need GraphQL exposure?
- Does it need a standard filterset, form, and table?

If yes, it belongs in `netbox_plant_graph/object_registry.py`.

If no, keep it explicit and local.

### Step 3: Create the `ObjectSpec`

Prefer `build_standard_object_spec(...)` when the object can use the shared pattern.

Provide at minimum:

- `registry_key`
- `model`
- `class_prefix`
- `route_slug`
- `api_basename`
- `label_singular`
- `label_plural`
- navigation metadata if it belongs in the menu
- `api_fields`
- `brief_fields`
- `filter_fields`
- `search_fields`
- `graphql_fields`
- optional read-only overrides

Use a fully explicit `ObjectSpec(...)` when the builder defaults would produce
the wrong public names or hide an important exception.

### Step 4: Choose the public names deliberately

Check whether the defaults are safe for:

- UI route names
- UI path prefixes
- API basename
- GraphQL singular field name
- GraphQL list field name

Override them in the spec when needed.

### Step 5: Decide whether the object is writable or read-only

Use the shared builder's read-only flags when the object should not expose add,
edit, delete, or write APIs.

When an object is read-only in the UI, the shared builder omits:

- generated form metadata
- edit view generation
- delete view generation
- add buttons in navigation

Current read-only graph-derived families include:

- `SignalLane`
- `FineEdge`
- `LaneMap`
- `AuditFinding`

### Step 6: Decide whether it belongs in navigation

If the object should be a top-level menu item, add navigation metadata.

If it should not be a top-level menu item, leave navigation metadata out.

Do not fake a hidden menu entry just to get routes generated.

### Step 7: Decide whether it needs a rich detail page

If the generated detail page is enough, stop there.

If the object needs curated ordering or custom rendering, add a spec in
`netbox_plant_graph/detail_specs.py`.

### Step 8: Add explicit object-specific behavior where required

The registry does not replace real behavior.

Add explicit code for things like:

- sync extraction and transformation behavior
- graph build orchestration
- procedural GraphQL operations
- jobs
- business validation
- computed summaries and traversals

Current examples:

- `resolve_path()` in `netbox_plant_graph/services/graph/resolver.py`
- `run_plane_audit()` in `netbox_plant_graph/services/graph/audits.py`
- `compute_blast_radius()` in `netbox_plant_graph/services/graph/blast_radius.py`
- `rebuild_graph()` in `netbox_plant_graph/services/sync/rebuilder.py`

If you add custom REST actions in the future, keep them explicit in
`netbox_plant_graph/api/views.py` and add matching tests.

### Step 9: Add shared test support

If the object participates in registry-driven surfaces, it must be constructible
in shared tests.

That usually means adding or extending:

- `netbox_plant_graph/tests/registry_scenarios.py`
- `netbox_plant_graph/tests/topology.py` for topology-backed service scenarios
- any explicit test helpers needed by view, API, and GraphQL contract tests

If the object has special behavior, keep the special tests explicit.

### Step 9a: Avoid cross-test data collisions in shared builders

As shared builders grow, fixed names become an easy source of full-suite-only failures.

Rules:

- Shared builders used by registry-driven tests should generate unique or overrideable search-visible values.
- If a builder creates nested topology objects, derive child names from the same token.
- Do not assume a focused test run is sufficient. A builder change is only safe once the full suite is green.

### Step 10: Update documentation

If the object changes user-facing functionality, update the relevant docs at the same time.

Typical places:

- `README.md`
- `LOCAL_DEV_SETUP.md`
- `netbox_plant_graph_plugin_design.md`
- inline docstrings when service behavior becomes more complex

## Testing and the Definition of Green

The plugin treats surface contracts as part of correctness.

Green does not mean only that one broad test command passed. It also means the
generated surfaces actually match the registry contract and the graph behavior
still works.

At minimum, a new or changed object family must prove:

- list-view actions match whether the object is creatable
- detail-view actions match whether the object is editable and deletable
- table row actions match whether edit and delete routes exist
- API methods match read-only versus writable intent
- GraphQL fields are registered with the intended stable names
- the object can be built in shared scenario-driven tests
- graph rebuild, path resolution, audits, and blast-radius behavior still work when your change touches those areas

Registry-wide contract coverage already lives in:

- `netbox_plant_graph/tests/test_views.py`
- `netbox_plant_graph/tests/test_api.py`
- `netbox_plant_graph/tests/test_graphql.py`
- `netbox_plant_graph/tests/registry_scenarios.py`

Topology-backed service coverage lives in:

- `netbox_plant_graph/tests/test_sync.py`
- `netbox_plant_graph/tests/test_resolver.py`
- `netbox_plant_graph/tests/topology.py`

Do not add a new registry object and skip the contract tests.

### Required verification habits

- Run focused tests while iterating.
- Run the full plugin suite before claiming the work is done.
- Use non-interactive test commands only.
- Treat manual browser testing as confirmation, not as a substitute for Python-level contract tests.

Known-good full-suite command:

```bash
cd /home/mencken/src/netbox_multiplanar_fabrics/devrun
./dev.sh test full
```

Focused contract command:

```bash
cd /home/mencken/src/netbox_multiplanar_fabrics/devrun
./dev.sh test contract
```

Fast structural smoke lane:

```bash
cd /home/mencken/src/netbox_multiplanar_fabrics/devrun
./dev.sh test fast
```

Use explicit test labels through the same wrapper when you want a targeted run:

```bash
cd ~/src/netbox_multiplanar_fabrics/devrun
./dev.sh test netbox_plant_graph.tests.test_views --verbosity 2
```

## Lessons Learned

These are not abstract style preferences. They come from real breakage and
follow-on cleanup in this plugin.

- Do not overload one identifier to serve as registry key, URL stem, API basename, and GraphQL field name.
- Do not assume NetBox defaults are safe for generated read-only objects. Explicitly control list actions, detail actions, menu buttons, and row actions.
- Structural smoke coverage is not enough. Surface-contract tests are required.
- Public names must be stable and explicit.
- Generated code should be inspectable and boring. Stable named classes and exported maps are better than clever metaprogramming.
- Rich detail pages should be reserved for objects that genuinely need curated rendering.
- Keep business logic out of surface metadata.
- Keep topology extraction, transformation, and traversal behavior in `services/`, not in registry builders.
- The plugin GraphQL package must continue to export `schema`.
- Browser testing is useful, but it does not replace registry-wide Python contract tests.
- When functionality changes, update the docs in the same slice.

## Practical Decision Rules

When you are unsure how to add something, follow these defaults:

- Add the model and migration explicitly.
- Put standard surfaces in the registry.
- Keep graph and workflow logic explicit.
- Use explicit public naming metadata early.
- Mark derived reporting objects read-only in both UI and API metadata.
- Add a rich detail spec only when the simple generated detail page is insufficient.
- Extend shared registry-driven tests in the same change.
- Do not call work complete until the full plugin suite and the surface-contract expectations are both green.
