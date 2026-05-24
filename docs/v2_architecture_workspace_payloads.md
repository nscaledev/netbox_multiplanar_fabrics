# Architecture Workspace Payloads

Last updated: 2026-05-24

This document is the operator-facing preparation guide for source artifacts
attached to `Build & Run -> Architecture Workspaces`.

For a screenshot-backed end-to-end example that publishes a four-plane RoCE
architecture with OSFP 4x200Gbps optics and 2x2 fiber shuffles, see
`docs/v2_roce_4plane_shuffle_architecture_walkthrough.md`.

The architecture workspace form uses `JSON Payload` as the raw JSON input field.
That field is not a separate payload format. It is where an operator pastes the
JSON body for the selected `Artifact Type`, such as a blueprint bundle, raw
architecture schema, standalone stamp template, or manual component list.

Current architecture payloads describe reusable topology semantics such as
roles, MPO geometry, channel maps, transfer/shuffle patterns, allocation rules,
cable profiles, required device types, and stamp templates. They do not yet
publish a dedicated `transceiver_profiles` schema section. Transceiver profiles
are seeded or imported through the plugin transceiver model and then consumed by
V2.5 stamping/import reconciliation when binding NetBox module inventory to
plugin connector faces.

## What To Use

Use this decision table when preparing architecture workspace inputs.

| Operator input | Artifact Type | Parser | Use when |
| --- | --- | --- | --- |
| Complete architecture bundle | `Blueprint Bundle` | `mpf_blueprint_bundle` | Publishing a reusable architecture blueprint, usually with parameter schema, device-type requirements, and stamp templates. |
| Inner architecture definition only | `Schema JSON` | `architecture_schema_json` | Validating or publishing an architecture definition without bundle metadata or templates. |
| Standalone stamp template | `Stamp Template` | `stamp_template_json` | Adding a template component to an existing workspace source set. |
| Generic pasted JSON | `API Payload` | `generic_json` | API-driven workflows where the workspace should auto-detect bundle/schema/manual component rows. |
| Hand-entered component rows | `Manual Entry` | `manual_component` | Capturing individual normalized component rows or notes from UI/API entry. |
| Diagram/spreadsheet reference | `Diagram` or `Spreadsheet` | `generic_json` unless overridden | Recording source provenance now. Automatic diagram/spreadsheet extraction is not implemented yet. |

Leave `Parser Key` blank unless you need to override the default parser.
Defaults are inferred from `Artifact Type`; `API Payload` also auto-detects
blueprint bundles, architecture schema JSON, and `items` lists.

## Blueprint Bundle

A blueprint bundle is the preferred input when an operator wants to create or
revise an architecture that will be used by onboarding and V2.5 stamping.

The bundle must be a JSON object with an architecture definition under one of
these keys:

- `definition`
- `architecture`
- `blueprint`
- `blueprint.definition`

Top-level bundle fields:

| Field | Required | Notes |
| --- | --- | --- |
| `definition`, `architecture`, or `blueprint` | Yes | The executable architecture definition. See `docs/v2_architecture_schema.md`. |
| `schema_contract_version` | Recommended | Defaults to the plugin's current architecture schema contract if omitted, but explicit is safer for review. Current value: `v2`. |
| `bundle_version` | Recommended | Human or automation version for the source bundle. |
| `bundle_author` | Optional | Team or tool that produced the bundle. |
| `name` | Optional | Display name copied into the import item. |
| `description` | Optional | Operator-facing architecture description. |
| `status` | Optional | Blueprint lifecycle status copied into the import item. |
| `lifecycle` | Optional | Lifecycle metadata copied into the import item. |
| `successor_version` | Optional | Successor hint for retired/replaced definitions. |
| `metadata` | Optional | Additional architecture metadata. |
| `parameter_schema` | Optional | JSON Schema object for stamp-time parameters. If absent, the definition-level `parameter_schema` is used. |
| `required_device_types` | Optional | Role slug to allowed NetBox `DeviceType` slugs. If absent, the definition-level matrix is used. |
| `stamp_templates` or `templates` | Optional | Mapping or list of stamp templates to publish with the architecture. |

Abbreviated bundle shape:

```json
{
  "schema_contract_version": "v2",
  "bundle_version": "2026.05-site-design-draft",
  "bundle_author": "network-architecture",
  "name": "Example 4-plane GB300 2x2 shuffle",
  "description": "Site-specific version of the GB300 four-plane shuffle architecture.",
  "definition": {
    "slug": "example-roce-4-plane-gb300-2x2-shuffle",
    "version": "v1",
    "plane_count": 4,
    "roles": [],
    "transfer_patterns": [],
    "allocation_rule_sets": [],
    "channel_map_matrix": [],
    "active_position_groups": {},
    "dark_positions": [],
    "mpo_position_count": 12,
    "shuffle_mpo_groups": []
  },
  "parameter_schema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {}
  },
  "required_device_types": {},
  "stamp_templates": {
    "example-mini-proof": {
      "slug": "example-mini-proof",
      "name": "Example mini proof",
      "template": {}
    }
  }
}
```

The architecture object above is intentionally abbreviated. Real payloads must
include valid roles, transfer patterns, allocation rules, channel-map matrix,
active/dark MPO positions, and any provider metadata required by the schema
contract.

### Generate A Valid Starter Bundle

The safest way to prepare a custom bundle is to start from a built-in registry
blueprint, change the slug/version/name, then edit the resulting JSON.

Run from a NetBox shell in the local development environment:

```bash
python /opt/netbox/netbox/manage.py shell -c '
import json
from dataclasses import replace
from netbox_plant_graph.services.architecture_schema import ARCHITECTURE_SCHEMA_CONTRACT_VERSION
from netbox_plant_graph.services.blueprint_registry import architecture_definition_to_payload, get_default_blueprint_registry

registry = get_default_blueprint_registry()
entry = registry.get_blueprint("roce-4-plane-gb300-2x2-shuffle", "v2")
definition = replace(
    entry.definition,
    slug="my-site-roce-4-plane-gb300-2x2-shuffle",
    version="v1",
)
bundle = {
    "schema_contract_version": ARCHITECTURE_SCHEMA_CONTRACT_VERSION,
    "bundle_version": "v1",
    "bundle_author": "network-architecture",
    "definition": architecture_definition_to_payload(definition),
    "parameter_schema": definition.parameter_schema,
    "required_device_types": definition.required_device_types,
    "stamp_templates": {
        slug: value
        for slug, value in entry.stamp_templates.items()
    },
}
print(json.dumps(bundle, indent=2, sort_keys=True))
'
```

The generated JSON can be pasted into `JSON Payload` with `Artifact Type` set to
`Blueprint Bundle`, or posted to the REST source attach endpoint.

## Schema JSON

`Schema JSON` is the inner architecture definition only. Use it when the source
system already tracks bundle metadata elsewhere or when you want to validate the
architecture definition before wrapping it in a bundle.

The payload must look like the value of `definition` in the bundle example:

```json
{
  "slug": "example-roce-4-plane-gb300-2x2-shuffle",
  "version": "v1",
  "plane_count": 4,
  "roles": [],
  "transfer_patterns": [],
  "allocation_rule_sets": [],
  "channel_map_matrix": [],
  "active_position_groups": {},
  "dark_positions": [],
  "mpo_position_count": 12,
  "shuffle_mpo_groups": []
}
```

As with bundles, the example is abbreviated. The complete contract is
documented in `docs/v2_architecture_schema.md`.

## Stamp Template JSON

`Stamp Template` attaches a standalone stamp-template component to the
workspace. It should include a stable slug, display name, and template body:

```json
{
  "slug": "example-mini-proof",
  "name": "Example mini proof",
  "template": {
    "architecture_slug": "example-roce-4-plane-gb300-2x2-shuffle",
    "architecture_version": "v1"
  }
}
```

If `slug` is omitted, normalization derives a stable digest-based natural key,
which is less operator-friendly.

## API Payload Or Manual Component Rows

`API Payload` uses `generic_json` detection:

- a payload with `definition`, `architecture`, `blueprint`, `stamp_templates`,
  or `templates` is treated as a blueprint bundle;
- a payload with `slug`, `version`, `roles`, `transfer_patterns`, and
  `allocation_rule_sets` is treated as architecture schema JSON;
- a payload with an `items` array is treated as manual component rows;
- any other JSON object becomes a single manual component.

Manual row list shape:

```json
{
  "items": [
    {
      "kind": "manual_note",
      "natural_key": "site-design-note-1",
      "name": "Operator note",
      "metadata": {
        "source": "whiteboard review"
      }
    }
  ]
}
```

## UI Workflow

1. Open `Build & Run -> Architecture Workspaces`.
2. Create or open an architecture workspace.
3. In `Attach Source Artifact`, choose the correct `Artifact Type`.
4. Paste the prepared object into `JSON Payload`.
5. Leave `Parser Key` blank unless overriding parser selection.
6. Attach the source artifact.
7. Normalize the artifact and inspect the generated design components.
8. Run validation.
9. Generate a publish plan.
10. Approve warnings if appropriate.
11. Publish the exact plan.
12. Use handoff JSON for downstream automation or audit review.

## REST Workflow

Attach a blueprint bundle:

```bash
curl -X POST \
  -H "Authorization: Token $NETBOX_TOKEN" \
  -H "Content-Type: application/json" \
  "$NETBOX_URL/api/plugins/plant-graph/architecture-workspaces/$WORKSPACE_ID/sources/" \
  --data @source-attach-request.json
```

The request body should include the source-attach envelope. If you generated a
standalone bundle with the shell command above, put that entire bundle object
under `raw_payload`:

```json
{
  "artifact_type": "blueprint_bundle",
  "name": "My site blueprint bundle",
  "source_label": "site-design-v1",
  "payload_version": "v1",
  "raw_payload": {
    "schema_contract_version": "v2",
    "bundle_version": "v1",
    "definition": {}
  }
}
```

Then normalize, validate, plan, approve, and publish using the workflow
endpoints documented in `docs/v2_external_contracts.md`.

## Current Gaps

- Diagram and spreadsheet artifacts preserve provenance but do not yet parse
  diagram or workbook content into architecture components.
- The workspace UI accepts pasted JSON but does not yet provide an inline
  schema-aware editor, examples drawer, or generated starter bundle button.
- The abbreviated examples in this document are not replacements for schema
  validation. Always run workspace validation before publishing.
