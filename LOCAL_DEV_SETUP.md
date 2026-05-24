# Local Development Setup

The current local NetBox environment for the multiplanar fabrics plugin is
driven by the scripts in:

```bash
/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/scripts
```

## Expected Environment

- Docker available locally.
- NetBox configured with `netbox_plant_graph` enabled.
- The local stack reachable at `http://localhost:8000` after startup.
- The plugin checkout mounted or installed into the local NetBox container.
- No `netbox_floorplan` runtime dependency is required for V2.

The local development stack commonly includes other NetBox plugins used in the
same lab environment, but `netbox_plant_graph` must be able to run without
floorplan integration enabled.

## Stack Commands

From the repository root:

```bash
./local-netbox-dev/scripts/up.sh
./local-netbox-dev/scripts/down.sh
```

Use the NetBox container's `manage.py` entry point for migrations, focused
tests, and seed commands. For example:

```bash
docker compose exec netbox python manage.py mpf_seed_v2 --architecture-only
docker compose exec netbox python manage.py test netbox_plant_graph.tests.test_v2_ui --keepdb
```

If the local compose project name differs in your environment, run the same
`manage.py` commands through the active NetBox service/container.

## Useful Seed/Backfill Scripts

Madison-specific operational scripts live under
`local-netbox-dev/scripts/`. They are useful for lab data and validation, but
they are not plugin core behavior. Treat them as local-environment helpers, not
as required runtime dependencies.

Common examples:

- `mpf_seed_v2 --architecture-only` seeds the generic V2 architecture fixture.
- `backfill_madison_transport_channels.py` repairs or enriches lab transport
  channel data.
- `backfill_madison_fiber_cable_assemblies.py` links existing lab strands to
  first-class cable assemblies.
- `report_madison_first_nvl72_fiber_paths.py` and
  `audit_madison_first_nvl72_fiber_fast.py` verify routed Madison fiber paths.

## Development Notes

- The plugin's modeled-fabric graph is plugin-native. Do not add NetBox-native
  cables or `CablePath` dependencies for owned V2 fabrics.
- Keep standard model CRUD/API surfaces registry-driven through
  `netbox_plant_graph/v2_registry.py`.
- Hand-wire workflow pages when they need custom behavior beyond model CRUD.
- Keep first-class cable-assembly semantics in the plugin model, with
  `FiberStrand.cable_site` and `FiberStrand.cable_id` remaining nullable for
  topology-first planning.
- Avoid reintroducing floorplan-plugin runtime coupling; spatial/layout work is
  plugin-native or lab-script-specific unless explicitly redesigned.
