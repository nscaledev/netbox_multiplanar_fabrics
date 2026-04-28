# Local Development Setup

This project follows the same local dev patterns as `netbox_rpki`.

## Expected environment

- NetBox source tree at `$HOME/src/netbox-v4.5.7/netbox`
- Virtualenv at `$HOME/.virtualenvs/netbox-4.5.7`
- Docker for PostgreSQL and Redis
- `netbox-floorplan-plugin` installed into the same NetBox virtualenv
- `PLUGINS = ['netbox_floorplan', 'netbox_plant_graph']` in local NetBox configuration
- `devrun` uses its own Docker Compose project name, `netbox_plant_graph_devrun`, so its local volumes do not collide with other NetBox plugin repos

## Common commands

```bash
./devrun/dev.sh start
./devrun/dev.sh stop
./devrun/dev.sh status
./devrun/dev.sh test fast
./devrun/dev.sh test contract
./devrun/seed-data.sh --dry-run
./devrun/seed-data.sh --cleanup-only --fabric nvidia
```

## Notes

- The floorplan/layout refactor assumes `netbox_floorplan` is enabled before you exercise new spatial-planning integration work.
- Interactive 2D edits belong in the floorplan plugin UI. If you need to copy managed rack moves back into `SpatialPlacement`, use `POST /api/plugins/netbox_plant_graph/floorplan/reconcile/` instead of re-enabling placement CRUD.
- If you previously ran this repo before the Compose project-name fix, remove the old generic `devrun_*` volumes once with `docker compose -p devrun down -v` from the `devrun/` directory before starting again.
- If you started this repo before removing hardcoded `container_name` entries, remove the old legacy containers once with `docker rm -f netbox-plant-graph-postgres netbox-plant-graph-redis` before the next `./dev.sh start`.
- If `~/.config/netbox-rpki-dev/credentials.env` exists, `./dev.sh start` reuses that repo's NetBox database/admin/app secrets by default and now reconciles the preserved local PostgreSQL role password automatically. You no longer need a manual `ALTER ROLE netbox ...` step when switching between the two plugin repos.
- If a seed run is interrupted, use `./devrun/seed-data.sh --cleanup-only --fabric nvidia|arista|all` to remove partial sample inventory before rerunning.
