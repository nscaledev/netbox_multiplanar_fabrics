# Local NetBox 4.2.3 Development Instance

Production-like local NetBox runtime with auth plugins intentionally omitted.

## Runtime

- NetBox base image: `netboxcommunity/netbox:v4.2.3-3.2.0`
- Plugins: `netbox_dns`, `netbox_prometheus_sd`, `netbox_floorplan==0.6.0`,
  `netbox_plant_graph`, `netbox_power_plant`
- Auth: local username/password only
- URL: `http://localhost:8000`
- User: `admin`
- Password: `admin`

The local source directories for `netbox_multiplanar_fabrics` and
`netbox_power_plant` are installed editable in the image and bind-mounted at
runtime so ordinary Python/template edits are visible inside the containers.

## Start

```bash
./scripts/up.sh
```

## Stop

```bash
./scripts/down.sh
```

## Optional Database Import

Place one prod/dev dump in `data/` before the first start, for example:

```bash
cp /path/to/YYYY_MM_DD_netbox_dump.sql.gz data/
./scripts/up.sh
```

Postgres imports files from `data/` only when the local database volume is new.
To force a fresh import:

```bash
./scripts/down.sh --volumes
./scripts/up.sh
```
