# NetBox Plant Graph Plugin

Lane-aware, plane-aware topology extension for multi-plane RoCE fabrics.

## Overview

This NetBox plugin layers a **plant-graph model** on top of native NetBox inventory and cabling. It is intended for environments with GPU clusters using multi-plane RoCEv2 fabrics, shuffle cables/modules, and 800G ports subdivided into 200G child transport units.

The plugin maintains a derived, normalized, graph-oriented topology layer that advanced consumers can query for automation, troubleshooting, validation, and visualization.

## Current Coverage

The current implementation is centered on **attachment-unit resolution**, with an initial **signal-lane** slice:

- rebuilds derive topology from NetBox `CablePath` objects
- cable profile expansion uses NetBox cable profile position mapping at sync time
- channelized parent interfaces are mapped onto child-interface attachment units
- plane memberships sourced from child interfaces are propagated across passive attachment hops
- signal lanes, signal-lane `FineEdge`s, and `LaneMap`s are materialized for channelized topologies
- resolver support includes both attachment-unit and signal-lane path resolution
- operational pages expose graph overview, path resolution, plane audit, lane drilldown, and blast-radius results in the plugin UI
- ambiguous blank-profile fanout cables are left unresolved in sync and surfaced by plane audit as missing-profile findings
- profile-derived breakout mappings now require explicit child interfaces; when those are missing, sync leaves the path unresolved and plane audit reports a missing-child-interface finding
- profile-derived breakouts with only a partial child-interface set now materialize only the positions that exist, and plane audit reports an incomplete-child-interface-set finding
- explicit child-interface attachment units that never participate in any derived path are now surfaced by plane audit as orphaned-attachment-unit findings
- cabled passive front/rear ports without `PortMapping` coverage are surfaced by plane audit as missing-port-mapping findings
- operational resolver/blast-radius flows now accept core NetBox `Interface`, `FrontPort`, and `RearPort` objects directly, and object-page badges provide shortcuts into those workflows
- lane drilldown is available from object badges, operational pages, detail cards, and GraphQL for lane-first inspection of materialized `SignalLane` objects
- operational path, audit, and blast-radius pages now render direct object links, contextual metadata, and guided next-action links for follow-on investigation

The test suite includes a multiplane shuffle fixture with one 800G host interface, four 200G child interfaces, one shuffle module with `PortMapping` rows, and one leaf switch.

## Requirements

- NetBox 4.5.0+
- Python 3.12+

## Installation

```bash
pip install netbox_plant_graph
```

Add `netbox_plant_graph` to the `PLUGINS` list in your NetBox `configuration.py`:

```python
PLUGINS = ['netbox_plant_graph']
```

## Development

See [LOCAL_DEV_SETUP.md](LOCAL_DEV_SETUP.md) for local development environment setup.
The `devrun` wrapper uses a repo-specific Docker Compose project name so its PostgreSQL and Redis volumes stay isolated from other NetBox plugin repos.
The compose stack intentionally avoids fixed `container_name` values so multiple local NetBox plugin repos do not collide on global Docker container names.

## License

Apache License 2.0
