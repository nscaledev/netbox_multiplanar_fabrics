# NetBox Multiplanar Fabrics

`netbox_plant_graph` is a NetBox plugin for modeling multi-planar optical
fabrics, with the current V2 implementation focused on RoCE/GPU fabrics that
use OSFP endpoints, MPO12 fanout, shuffle cassettes, bundled cable assemblies,
and 200gbps transport channels.

The plugin is intentionally **plugin-native** for modeled fabric connectivity.
It anchors to NetBox devices and interfaces where those objects exist, but it
does not rely on NetBox `Cable`, `CablePath`, `CableTermination`, or
`PortMapping` objects as the source of truth for fabrics it owns.

## Current V2 Scope

V2 provides:

- a normalized fabric data model for architectures, fabrics, planes, nodes,
  endpoints, connector positions, transport channels, cable assemblies, fiber
  segments, fiber strands, strand terminations, optical lanes, transfer maps,
  and path intents;
- a seeded architecture definition for
  `roce-4-plane-gb300-2x2-shuffle`, including a 4-plane topology, OSFP ->
  MPO12 child connector semantics, a 200gbps sub-interface channel-map matrix,
  and the active-position 2x2 shuffle transform;
- first-class `CableAssembly` rows for jumpers, trunks, and parent/child cable
  hierarchy, with each `FiberStrand` able to resolve back to an assembly by
  site-local cable ID;
- on-demand optical path resolution at connector-position resolution across
  arbitrary strand and transfer-map hops;
- stamping templates and `StampRun` provenance for creating V2 fabrics from
  architecture rules;
- generated list/detail/CRUD/API surfaces from the V2 registry for standard
  model inventory objects;
- hand-wired operator workflows for path tracing, interface fanout tracing,
  physical cable blast-radius analysis, onboarding, operations, and audit
  triage;
- a read-oriented GraphQL V2 contract plus REST API workflow mutations for
  stamping, audit lifecycle actions, exception requests, path query, and
  operational summaries.

## Operator UI

The plugin menu is grouped around current workflows:

- **Operate**
  - Fabric Overview
  - Interface Fanout Trace
  - Path Query
  - Physical Cable Blast Radius
- **Build & Run**
  - Onboard Fabric
  - Operations Center
- **Audit**
  - Audit Dashboard
  - Audit Triage
  - Exception Requests
- **Model Inventory**
  - Fabrics
  - Architectures
  - Cable Assemblies
  - Lane Inventory
  - Model Catalog

The visual trace workflows render source/destination 200gbps interface groups,
MPO12 connector positions, shuffle cassette transforms, cable assemblies, and
per-lane paths. The trace sections support collapse/expand behavior and SVG
export.

The Physical Cable Blast Radius workflow supports operator-first selection by
site, device type, role, rack label, rack row, rack elevation, and partial device
name. It can model cable-assembly failures, connector unplug events, and an
unseated OSFP transceiver, then reports impacted endpoints and devices with
drill-down links.

## NetBox Integration Boundary

V2 uses NetBox as inventory context, not as the fabric connection graph:

- `Fabric` can scope to a NetBox site, location, and tenant.
- `FabricNode` and `Endpoint` can anchor to NetBox objects through generic
  foreign keys.
- `TransportChannel.source_subinterface` can link to NetBox child interfaces
  created for 200gbps channels.
- NetBox interface detail pages expose incoming multiplanar context through a
  plugin template extension.
- NetBox-native cable/path objects are not required or created for owned
  fabric connectivity.
- The plugin has no runtime dependency on `netbox_floorplan`.

## Requirements

- NetBox 4.2.3+ through 4.5.x
- Python 3.12+

## Installation

```bash
pip install netbox_plant_graph
```

Enable the plugin in NetBox configuration:

```python
PLUGINS = ["netbox_plant_graph"]
```

Seed the built-in V2 architecture fixture when needed:

```bash
python manage.py mpf_seed_v2 --architecture-only
```

## Development

The local NetBox development stack for this repo is driven from:

```bash
/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/scripts
```

Common stack commands:

```bash
./local-netbox-dev/scripts/up.sh
./local-netbox-dev/scripts/down.sh
```

See [LOCAL_DEV_SETUP.md](LOCAL_DEV_SETUP.md) for local environment notes and
[docs/README.md](docs/README.md) for the current documentation map.

## Key Documents

- [V2 data model](docs/data_model.md)
- [External automation contracts](docs/v2_external_contracts.md)
- [GraphQL contract](docs/v2_graphql_contract_v2.md)
- [Cutover runbook](docs/v2_cutover_runbook.md)
- [Post-MVP expansion plan](docs/v2_post_mvp_expansion_plan.md)
- [First-class cabling plan](docs/v2_first_class_cabling_plan.md)

## License

Apache License 2.0
