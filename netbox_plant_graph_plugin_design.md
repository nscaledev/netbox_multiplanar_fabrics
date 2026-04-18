# NetBox Plant-Graph Plugin Design Sketch
## Lane-aware, plane-aware topology extension for shuffle-heavy multi-plane RoCE fabrics

**Status:** implementation sketch with repo-status notes  
**Target platform:** NetBox plugin for NetBox 4.5+  
**Primary objective:** extend NetBox from inventory and structured cabling source-of-truth into a **lane-aware physical plant topology service** for GPU fabrics with shuffle cables/modules and multi-plane path semantics.

### Current repo status

As of this repo snapshot:

- **Phase 0 is largely complete**: plugin skeleton, registry metadata, generated standard CRUD surfaces, API surfaces, GraphQL registration, navigation, shared detail template, migrations, and baseline tests are present
- **Phase 1 is substantially complete**: `Fabric`-scoped graph rebuild, `CablePath` extraction, cable-profile expansion via `get_mapped_position()`, attachment-unit resolver, plane propagation across passive paths, and multiplane integration fixtures/tests are implemented
- **UI hardening is in progress**: generated list/detail pages now render successfully for the current model set, including populated-row cases and read-only surfaces, the custom operational pages now execute live graph services instead of placeholder text, the plugin menu now exposes those operational pages directly, object-page badges provide direct shortcuts into resolver/blast-radius/lane-drilldown workflows, detail pages can expose lane-focused supplementary cards for lane-bearing objects, and the dedicated health page is now implemented
- **Phase 2 is now partially implemented**: `SignalLane`, `LaneMap`, and signal-lane `FineEdge` materialization are present in sync, resolver coverage includes signal-lane traversal, operational queries now accept core NetBox `Interface`/`FrontPort`/`RearPort` objects directly, and lane drilldown is now exposed through operational UI, detail-page affordances, badges, and GraphQL; richer lane-first workflows are still ahead
- **Phase 3 remains partial**: audit and blast-radius services exist, the basic operational pages call them, blank-profile ambiguous fanout cables are surfaced as unresolved audit findings, profile-derived breakouts report missing-child-interface findings when explicit child interfaces are absent, partial child-interface sets report incomplete-child-interface-set findings, profile-derived peer-position mismatches now report partial-profile-mapping findings, disconnected child transport units report orphaned-attachment-unit findings, and cabled passive front/rear ports without `PortMapping` coverage now report missing-port-mapping findings; `AuditFinding` persistence and richer remediation workflows are still ahead
- **Milestone B work remains**: signal-lane graph semantics now exist, but richer lane-first workflows, durable audit surfaces, and deeper remediation/task orchestration are still ahead

---

## 1. Executive summary

This document sketches a NetBox plugin that layers a **plant-graph model** on top of native NetBox inventory and cabling. The plugin is intended for environments with:

- multiple regions, sites, datacenters, halls, and pods
- GPU clusters using **minimum 4-plane** RoCEv2 fabrics
- 800G physical host and switch ports subdivided into **200G child transport units**
- optional need to drill down further into **discrete PAM4 lane primitives**
- shuffle cables and/or shuffle modules at:
  - GPU ↔ leaf
  - leaf ↔ spine
  - spine ↔ meta-spine
- a requirement to expose the resulting topology as a **queryable graph API** for automation, troubleshooting, validation, and visualization

This plugin **does not replace** NetBox core inventory or core cable tracing. Instead:

- **NetBox remains the inventory/cabling source of truth**
- the plugin maintains a **derived, normalized, graph-oriented topology layer**
- advanced consumers query the plugin’s graph API rather than trying to infer topology directly from raw NetBox objects

### Key design principle

The plugin’s topology model must support multiple resolutions:

1. **Container resolution**  
   Example: physical 800G port, patch panel face, cassette connector

2. **Attachment-unit resolution**  
   Example: a 200G child transport unit belonging to one plane

3. **Signal-lane resolution**  
   Example: individual PAM4 electrical/optical lane primitives inside that 200G transport unit

Operationally, the system should default to **attachment-unit resolution**, while allowing optional drill-down to signal-lane resolution for forensic or debugging workflows.

---

## 2. Why a plugin and not a fork?

This design is intentionally **plugin-shaped**.

The plugin should own:

- custom database models
- graph derivation logic
- graph query APIs
- background jobs for sync/rebuild
- custom UI pages for graph exploration and path tracing
- validation and audit workflows
- optional event-driven incremental refresh behavior

The plugin should **not** try to transparently replace all NetBox-native cable/path-trace behavior inside core object pages. That path leads to brittle coupling and a long-term maintenance tax.

### Recommended architecture boundary

**NetBox core owns:**
- devices
- interfaces / child interfaces
- front ports / rear ports
- cables
- cable profiles
- physical placement and inventory hierarchy

**Plugin owns:**
- graph normalization
- plane semantics
- lane-aware topology traversal
- path resolution at multiple granularities
- audit logic
- blast-radius and dependency analysis
- consumer-friendly APIs

---

## 3. Design goals

### Functional goals

The plugin must allow consumers to answer questions such as:

- What is the full physical path from GPU `host123/nic0/plane2-child` to its serving leaf?
- Which exact shuffle modules, cassette positions, and trunks are traversed by a given 200G child transport unit?
- Which exact PAM4 lanes are involved if a 200G child path is mapped incorrectly?
- Which GPU paths in a hall violate plane diversity or disjointness rules?
- What is the blast radius if shuffle module `SM-H1-P12-R3-A` fails?
- Which leaf-to-spine and spine-to-meta-spine paths share passive artifacts that should be plane-isolated?
- Can we render a subgraph for Pod X, Plane Y at either:
  - container level
  - attachment-unit level
  - signal-lane level

### Non-goals

The first implementation should **not** attempt to:

- replace NetBox core trace UX everywhere
- model full telecom/OSP complexity
- solve every optical device type in v1
- create a perfectly normalized abstract graph ontology
- persist redundant graph detail that can be cheaply and deterministically derived

---

## 4. Real-world modeling assumptions

This design assumes the following practical realities:

### 4.1 Host-facing ports are channelized
An 800G GPU NIC port is not the true atomic path endpoint. It is a **container** for multiple lower-speed transport units, for example four 200G child units.

### 4.2 Passive infrastructure is topology-bearing
Shuffle modules, shuffle cables, cassettes, trunks, and patch panels are not decorative metadata. They carry meaningful internal transfer/mapping semantics and therefore must participate in the graph.

### 4.3 Different resolutions are needed for different workflows
Most operational consumers want to reason about **200G attachment units**, not about every individual PAM4 lane. But forensic workflows must be able to drill all the way down when necessary.

### 4.4 Plane identity lives below the device
A physical 800G port may host multiple child transport units that participate in different planes. Therefore plane semantics cannot live only at the device or physical-port layer.

---

## 5. Topology abstraction model

The plugin should distinguish the following concepts cleanly:

### 5.1 Container
A physical connector-bearing object, such as:

- an 800G NIC port
- an 800G switch port
- a panel face
- a cassette connector
- an MPO port on a shuffle module

### 5.2 Attachment Unit
A graph-visible transport endpoint that participates in fabric pathing, such as:

- a 200G child interface of an 800G host port
- a 200G child interface of an 800G switch port
- a grouped set of passive positions associated with one path segment

### 5.3 Signal Lane
The finest transport primitive, such as:

- one PAM4 electrical TX lane
- one PAM4 electrical RX lane
- one optical TX lane
- one optical RX lane

### 5.4 Coarse Transport Edge
A physical connection between two container-level endpoints, usually corresponding to a NetBox cable.

### 5.5 Fine Transport Edge
A derived edge between attachment units or signal lanes after profile and mapping expansion.

### 5.6 Transfer Mapping
An internal mapping relationship inside a plant object or cable assembly, such as:

- identity mapping
- lane shuffle
- polarity swap
- breakout mapping
- cassette remap

---

## 6. Recommended plugin package name

Working name:

`netbox_plant_graph`

Possible alternatives:
- `netbox_fabric_plant`
- `netbox_lanegraph`
- `netbox_roce_topology`

Use a name that is broad enough to support future expansion beyond GPU fabrics if desired.

---

## 7. Proposed plugin package layout

```text
netbox_plant_graph/
├── __init__.py
├── plugin_config.py
├── constants.py
├── choices.py
├── signals.py
├── object_specs.py            # frozen dataclasses: ObjectSpec, LabelSpec, RouteSpec, ApiSpec, etc.
├── object_registry.py         # canonical OBJECT_SPECS tuple + filtered subsets
├── detail_specs.py            # curated detail-page metadata for richer top-level objects
├── navigation.py              # registry-driven menu generation
├── urls.py                    # registry-driven URL registration
├── filtersets.py              # registry-driven filterset generation
├── forms.py                   # registry-driven form generation
├── tables.py                  # registry-driven table generation
├── search.py
├── template_extensions.py
├── graphql/
│   ├── __init__.py
│   ├── filters.py             # registry-driven GraphQL filter generation
│   ├── types.py               # registry-driven GraphQL type generation
│   └── schema.py              # registry-driven query field registration
├── api/
│   ├── __init__.py
│   ├── serializers.py         # registry-driven serializer generation
│   ├── views.py               # registry-driven viewset generation
│   └── urls.py                # registry-driven router registration
├── models/
│   ├── __init__.py
│   ├── plant.py               # Fabric, PlantNode, TerminationPoint, AttachmentUnit, SignalLane
│   ├── topology.py            # CoarseEdge, FineEdge
│   ├── mappings.py            # TransferMap, LaneMap
│   ├── policies.py            # FabricPlane, PlaneMembership, PathIntent, AuditFinding
│   └── sync.py
├── jobs/
│   ├── __init__.py
│   ├── full_rebuild.py
│   ├── incremental_refresh.py
│   ├── plane_audit.py
│   └── blast_radius.py
├── services/
│   ├── __init__.py
│   ├── sync/
│   │   ├── __init__.py
│   │   ├── extractor.py
│   │   ├── transformer.py
│   │   ├── graph_builder.py
│   │   └── rebuilder.py
│   ├── graph/
│   │   ├── __init__.py
│   │   ├── resolution.py
│   │   ├── traversal.py
│   │   ├── resolver.py
│   │   ├── blast_radius.py
│   │   └── audits.py
│   └── netbox/
│       ├── __init__.py
│       ├── adapters.py
│       └── selectors.py
├── views/
│   ├── __init__.py
│   ├── graph.py               # custom graph exploration views (not registry-driven)
│   ├── paths.py               # custom path resolver view (not registry-driven)
│   ├── audits.py              # custom audit view (not registry-driven)
│   └── inventory.py
├── templates/netbox_plant_graph/
│   ├── object_detail.html     # shared metadata-driven detail template
│   ├── graph_overview.html
│   ├── path_detail.html
│   ├── plane_audit.html
│   ├── blast_radius.html
│   └── includes/
│       ├── object_badges.html
│       └── path_summary.html
├── migrations/
└── tests/
    ├── __init__.py
    ├── registry_scenarios.py   # shared test scenarios driven by object registry
    ├── test_models.py
    ├── test_sync.py
    ├── test_resolver.py
    ├── test_api.py
    ├── test_graphql.py
    ├── test_views.py
    ├── test_navigation.py
    ├── test_urls.py
    └── fixtures/
```

---

## 8. Plugin configuration skeleton

### `plugin_config.py`

```python
from netbox.plugins import PluginConfig

class PlantGraphConfig(PluginConfig):
    name = "netbox_plant_graph"
    verbose_name = "Plant Graph"
    description = "Lane-aware, plane-aware topology graph for structured GPU fabric cabling"
    version = "0.1.0"
    author = "Your Team"
    author_email = "team@example.com"
    base_url = "plant-graph"
    min_version = "4.5.0"
    required_settings = []
    default_settings = {
        "graph_default_resolution": "attachment_unit",
        "enable_incremental_refresh": True,
        "max_path_depth": 256,
        "materialize_signal_lanes": True,
        "default_plane_field_name": "fabric_plane",
    }

config = PlantGraphConfig
```

### 8.1 Registry-driven surface architecture

This plugin should adopt the metadata-driven registry pattern proven in `netbox_rpki`. The core principle: **define all non-model object metadata once in a central registry; generate the repetitive NetBox surfaces from that metadata.** Django models and migrations remain explicit.

This eliminates the need to hand-author separate form, filterset, table, serializer, viewset, GraphQL filter/type, navigation entry, URL route, and smoke test classes for each model family. Adding a new object family becomes: write the model, add one registry entry, done.

#### What stays explicit

- Django model classes (in `models/`)
- database migrations
- model methods with real behavior
- complex custom views (graph explorer, path resolver, blast-radius UI, plane audit)
- custom detail-page context builders for objects with unusual rendering needs
- sync/rebuild/audit service-layer logic
- domain-specific validation

#### What becomes registry-driven

- API serializer generation
- API viewset generation
- API router registration
- forms and filter forms
- filtersets
- tables
- navigation menu entries
- standard list / edit / delete views
- standard URL registration
- GraphQL filters, types, and query fields
- standard detail views (for objects that fit the generic pattern)
- smoke-style repeated tests

### 8.2 Object spec dataclasses

Create `object_specs.py` with frozen dataclasses capturing per-object metadata. The proven shape from `netbox_rpki`:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class LabelSpec:
    singular: str
    plural: str


@dataclass(frozen=True)
class RouteSpec:
    slug: str
    path_prefix: str | None = None

    @property
    def resolved_path_prefix(self) -> str:
        return self.path_prefix or f"{self.slug}s"

    @property
    def list_url_name(self) -> str:
        return f"plugins:netbox_plant_graph:{self.slug}_list"

    @property
    def add_url_name(self) -> str:
        return f"plugins:netbox_plant_graph:{self.slug}_add"


@dataclass(frozen=True)
class ApiSpec:
    serializer_name: str
    viewset_name: str
    basename: str
    fields: tuple[str, ...]
    brief_fields: tuple[str, ...]
    read_only: bool = False

    @property
    def detail_view_name(self) -> str:
        return f"plugins-api:netbox_plant_graph-api:{self.basename}-detail"


@dataclass(frozen=True)
class NavigationSpec:
    group: str
    label: str
    order: int
    show_add_button: bool = True


@dataclass(frozen=True)
class FormSpec:
    class_name: str
    fields: tuple[str, ...]


@dataclass(frozen=True)
class FilterSetSpec:
    class_name: str
    fields: tuple[str, ...]
    search_fields: tuple[str, ...]


@dataclass(frozen=True)
class GraphQLFilterFieldSpec:
    field_name: str
    filter_kind: str


@dataclass(frozen=True)
class GraphQLFilterSpec:
    class_name: str
    fields: tuple[GraphQLFilterFieldSpec, ...]


@dataclass(frozen=True)
class GraphQLTypeSpec:
    class_name: str
    fields: str = "__all__"


@dataclass(frozen=True)
class GraphQLSpec:
    filter: GraphQLFilterSpec
    type: GraphQLTypeSpec
    detail_field_name: str
    list_field_name: str


@dataclass(frozen=True)
class TableSpec:
    class_name: str
    fields: tuple[str, ...]
    default_columns: tuple[str, ...]
    linkify_field: str


@dataclass(frozen=True)
class ViewSpec:
    list_class_name: str
    detail_class_name: str
    edit_class_name: str | None = None
    delete_class_name: str | None = None
    simple_detail: bool = False


@dataclass(frozen=True)
class ObjectSpec:
    registry_key: str
    model: type
    labels: LabelSpec
    routes: RouteSpec
    api: ApiSpec
    filterset: FilterSetSpec
    graphql: GraphQLSpec | None = None
    navigation: NavigationSpec | None = None
    form: FormSpec | None = None
    filter_form: FilterFormSpec | None = None
    table: TableSpec | None = None
    view: ViewSpec | None = None
```

### 8.3 Object registry

Create `object_registry.py` as the canonical registry. Each plugin model family gets one `ObjectSpec` entry. The registry exports filtered subsets consumed by surface modules:

```python
OBJECT_SPECS = (
    # Fabric, PlantNode, TerminationPoint, AttachmentUnit, SignalLane,
    # CoarseEdge, FineEdge, TransferMap, LaneMap,
    # FabricPlane, PlaneMembership, AuditFinding, ...
)

API_OBJECT_SPECS = OBJECT_SPECS
GRAPHQL_OBJECT_SPECS = tuple(s for s in OBJECT_SPECS if s.graphql is not None)
FILTERSET_OBJECT_SPECS = tuple(s for s in OBJECT_SPECS if s.filterset is not None)
FORM_OBJECT_SPECS = tuple(s for s in OBJECT_SPECS if s.form is not None)
TABLE_OBJECT_SPECS = tuple(s for s in OBJECT_SPECS if s.table is not None)
VIEW_OBJECT_SPECS = tuple(s for s in OBJECT_SPECS if s.view is not None)
SIMPLE_DETAIL_VIEW_OBJECT_SPECS = tuple(s for s in VIEW_OBJECT_SPECS if s.view.simple_detail)
OBJECT_SPEC_BY_REGISTRY_KEY = {s.registry_key: s for s in OBJECT_SPECS}
MENU_GROUP_ORDER = ("Fabrics", "Topology", "Mappings", "Policy", "Audit")
```

Important implementation rules from `netbox_rpki`:
- store explicit public names (serializer class names, viewset class names, GraphQL field names, URL basenames) in the registry rather than deriving them from model names
- stable external names matter more than deduplication purity
- the registry is for surface metadata, not business rules

### 8.4 Surface builder functions

Each consuming module (`filtersets.py`, `forms.py`, `tables.py`, `api/serializers.py`, `api/views.py`, `views.py`, `graphql/`) should contain a small builder function that takes an `ObjectSpec` and returns a generated class. The proven pattern:

```python
# filtersets.py
from netbox.filtersets import NetBoxModelFilterSet
from netbox_plant_graph.object_registry import FILTERSET_OBJECT_SPECS

def build_filterset_class(spec):
    meta_class = type('Meta', (), {'model': spec.model, 'fields': spec.filterset.fields})
    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        query = Q()
        for lookup in spec.filterset.search_fields:
            query |= Q(**{lookup: value})
        return queryset.filter(query)
    return type(spec.filterset.class_name, (NetBoxModelFilterSet,), {
        '__module__': __name__, 'Meta': meta_class, 'search': search,
    })

for spec in FILTERSET_OBJECT_SPECS:
    globals()[spec.filterset.class_name] = build_filterset_class(spec)


# api/serializers.py
from netbox.api.serializers import NetBoxModelSerializer
from netbox_plant_graph.object_registry import API_OBJECT_SPECS

def build_serializer_class(spec):
    meta_class = type("Meta", (), {
        "model": spec.model,
        "fields": spec.api.fields,
        "brief_fields": spec.api.brief_fields,
    })
    return type(spec.api.serializer_name, (NetBoxModelSerializer,), {
        "__module__": __name__,
        "url": HyperlinkedIdentityField(view_name=spec.api.detail_view_name),
        "Meta": meta_class,
    })

SERIALIZER_CLASS_MAP = {}
for spec in API_OBJECT_SPECS:
    cls = build_serializer_class(spec)
    SERIALIZER_CLASS_MAP[spec.registry_key] = cls
    globals()[spec.api.serializer_name] = cls


# api/urls.py
from netbox.api.routers import NetBoxRouter
from netbox_plant_graph.object_registry import API_OBJECT_SPECS
from netbox_plant_graph.api.views import VIEWSET_CLASS_MAP

router = NetBoxRouter()
for spec in API_OBJECT_SPECS:
    router.register(spec.api.basename, VIEWSET_CLASS_MAP[spec.registry_key], basename=spec.api.basename)

urlpatterns = router.urls
```

The same pattern applies to tables, forms, views, navigation, and GraphQL types/filters.

#### Override pattern for objects with custom behavior

Generate the base class from the registry, then subclass to add custom fields or methods. Re-inject into the class map:

```python
# Example: Fabric serializer with extra computed field
class FabricSerializer(SERIALIZER_CLASS_MAP['fabric']):
    plane_summary = serializers.SerializerMethodField()

    class Meta(SERIALIZER_CLASS_MAP['fabric'].Meta):
        fields = SERIALIZER_CLASS_MAP['fabric'].Meta.fields + ('plane_summary',)

    def get_plane_summary(self, obj):
        return {'plane_count': obj.planes.count(), 'expected': obj.expected_plane_count}

SERIALIZER_CLASS_MAP['fabric'] = FabricSerializer
globals()['FabricSerializer'] = FabricSerializer
```

### 8.5 Detail page metadata

Create `detail_specs.py` for curated detail-page metadata on richer top-level objects (`Fabric`, `PlantNode`, `FabricPlane`). Simpler objects (`CoarseEdge`, `FineEdge`, `TransferMap`, `SignalLane`) should use the lightweight generated detail path from the registry's `ViewSpec.simple_detail = True`.

The detail spec system should support:
- primary attribute rows with field ordering
- related table sections (e.g. planes under a fabric, attachment units under a termination point)
- add buttons with prefilled query parameters
- per-object template override hooks for objects like graph overview and path resolver that need entirely custom rendering

### 8.6 Expected registry entries for this plugin

The following model families should have `ObjectSpec` entries:

| Registry key | Model | Menu group | Standard CRUD | Rich detail | Notes |
|---|---|---|---|---|---|
| `fabric` | `Fabric` | Fabrics | Yes | Yes | Top-level scoping object; detail shows planes, health |
| `fabricplane` | `FabricPlane` | Fabrics | Yes | Yes | Detail shows memberships, path stats |
| `plantnode` | `PlantNode` | Topology | Yes | Yes | Detail shows termination points, edges |
| `terminationpoint` | `TerminationPoint` | Topology | Yes | Simple | |
| `attachmentunit` | `AttachmentUnit` | Topology | Yes | Simple | |
| `signallane` | `SignalLane` | Topology | Read-only | Simple | Phase 2 |
| `coarseedge` | `CoarseEdge` | Topology | Yes | Simple | |
| `fineedge` | `FineEdge` | Topology | Read-only | Simple | Derived, not user-editable |
| `transfermap` | `TransferMap` | Mappings | Yes | Simple | |
| `lanemap` | `LaneMap` | Mappings | Read-only | Simple | Phase 2 |
| `planemembership` | `PlaneMembership` | Policy | Yes | Simple | |
| `auditfinding` | `AuditFinding` | Audit | Read-only | Simple | Phase 3 |

Objects that should **not** be in the standard registry:
- Custom views (graph explorer, path resolver, blast radius, plane audit) are purpose-built and not registry-driven
- Sync/rebuild jobs are NetBox background jobs, not CRUD objects

---

## 9. Core data model

The following models are the core of the plugin.

### 9.1 `PlantNode`

Represents any meaningful graph node corresponding to a physical or logical plant object.

Examples:
- device
- patch panel
- shuffle module
- cassette
- cable assembly
- trunk bundle

Suggested fields:

```python
class PlantNode(NetBoxModel):
    name = models.CharField(max_length=200)
    node_type = models.CharField(max_length=50, choices=PlantNodeTypeChoices)
    role = models.CharField(max_length=50, blank=True)
    status = models.CharField(max_length=50, blank=True)

    location_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.PROTECT)
    location_id = models.PositiveBigIntegerField(null=True, blank=True)
    location = GenericForeignKey("location_type", "location_id")

    source_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.PROTECT)
    source_id = models.PositiveBigIntegerField(null=True, blank=True)
    source = GenericForeignKey("source_type", "source_id")

    metadata = models.JSONField(default=dict, blank=True)
```

Notes:
- `source` should point back to the canonical NetBox object where applicable
- `metadata` should store lightweight derived information only, not large denormalized blobs
- `PlantNode` should be the top-level object for graph visualization and blast-radius analysis

---

### 9.2 `TerminationPoint`

Represents a physical connector-bearing endpoint or container.

Examples:
- GPU NIC physical 800G port
- switch physical 800G port
- shuffle module MPO face
- panel front port
- cassette rear port

Suggested fields:

```python
class TerminationPoint(NetBoxModel):
    plant_node = models.ForeignKey("PlantNode", related_name="termination_points", on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    tp_type = models.CharField(max_length=50, choices=TerminationPointTypeChoices)

    connector_type = models.CharField(max_length=100, blank=True)
    channel_capacity = models.PositiveIntegerField(default=0)
    speed_gbps = models.PositiveIntegerField(null=True, blank=True)

    source_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.PROTECT)
    source_id = models.PositiveBigIntegerField(null=True, blank=True)
    source = GenericForeignKey("source_type", "source_id")

    metadata = models.JSONField(default=dict, blank=True)
```

Notes:
- `channel_capacity` is a semantic hint, not necessarily a statement about active children
- many `TerminationPoint`s will have a direct source relationship to a NetBox interface, front port, or rear port

---

### 9.3 `AttachmentUnit`

Represents the graph-visible transport endpoint used for normal pathing.

Examples:
- one 200G child interface of an 800G host port
- one 200G child interface of a switch port
- one passive position-group logically associated with a 200G transport path

Suggested fields:

```python
class AttachmentUnit(NetBoxModel):
    termination_point = models.ForeignKey("TerminationPoint", related_name="attachment_units", on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    ordinal = models.PositiveIntegerField(default=0)
    unit_type = models.CharField(max_length=50, choices=AttachmentUnitTypeChoices)
    speed_gbps = models.PositiveIntegerField(null=True, blank=True)

    topology_role = models.CharField(max_length=50, blank=True)
    active = models.BooleanField(default=True)

    source_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.PROTECT)
    source_id = models.PositiveBigIntegerField(null=True, blank=True)
    source = GenericForeignKey("source_type", "source_id")

    metadata = models.JSONField(default=dict, blank=True)
```

Notes:
- this should usually be the **default resolution** object for path queries
- host and switch child interfaces should map naturally here
- if a passive artifact does not exist as a NetBox child interface, the plugin may still synthesize attachment units for it

---

### 9.4 `SignalLane`

Represents the finest transport primitive.

Examples:
- one PAM4 electrical TX lane inside a host 200G child
- one optical RX lane inside a cassette-facing position group

Suggested fields:

```python
class SignalLane(NetBoxModel):
    attachment_unit = models.ForeignKey("AttachmentUnit", related_name="signal_lanes", on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    lane_index = models.PositiveIntegerField(default=0)

    lane_kind = models.CharField(max_length=50, choices=SignalLaneKindChoices)
    signaling = models.CharField(max_length=32, choices=SignalEncodingChoices, default="pam4")
    nominal_rate_gbps = models.PositiveIntegerField(null=True, blank=True)

    direction_role = models.CharField(max_length=50, blank=True)
    wavelength_group = models.CharField(max_length=64, blank=True)

    source_anchor = models.CharField(max_length=128, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
```

Notes:
- lane cardinality should be driven by media/profile metadata, not hardcoded assumptions
- not every path query should expand to this level unless requested

---

### 9.5 `CoarseEdge`

Represents a physical connection between two container-level endpoints.

Usually corresponds 1:1 with a NetBox cable.

```python
class CoarseEdge(NetBoxModel):
    edge_type = models.CharField(max_length=50, choices=CoarseEdgeTypeChoices)

    a_tp = models.ForeignKey("TerminationPoint", related_name="coarse_edges_a", on_delete=models.CASCADE)
    b_tp = models.ForeignKey("TerminationPoint", related_name="coarse_edges_b", on_delete=models.CASCADE)

    source_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.PROTECT)
    source_id = models.PositiveBigIntegerField(null=True, blank=True)
    source = GenericForeignKey("source_type", "source_id")

    cable_profile_name = models.CharField(max_length=200, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
```

Notes:
- do not attempt to store the full expanded lane map here
- store enough information to anchor back to the NetBox cable

---

### 9.6 `FineEdge`

Represents a derived graph edge between attachment units or signal lanes.

```python
class FineEdge(NetBoxModel):
    granularity = models.CharField(max_length=32, choices=GraphResolutionChoices)
    edge_type = models.CharField(max_length=50, choices=FineEdgeTypeChoices)

    a_au = models.ForeignKey("AttachmentUnit", null=True, blank=True, related_name="fine_edges_a_au", on_delete=models.CASCADE)
    b_au = models.ForeignKey("AttachmentUnit", null=True, blank=True, related_name="fine_edges_b_au", on_delete=models.CASCADE)

    a_lane = models.ForeignKey("SignalLane", null=True, blank=True, related_name="fine_edges_a_lane", on_delete=models.CASCADE)
    b_lane = models.ForeignKey("SignalLane", null=True, blank=True, related_name="fine_edges_b_lane", on_delete=models.CASCADE)

    parent_coarse_edge = models.ForeignKey("CoarseEdge", null=True, blank=True, related_name="fine_edges", on_delete=models.CASCADE)
    derived_from_profile = models.BooleanField(default=False)
    metadata = models.JSONField(default=dict, blank=True)
```

Notes:
- exactly which fields are populated depends on granularity
- this model may be materialized or rebuilt as needed
- if performance/storage becomes an issue later, signal-lane edges can become partially virtualized
- `derived_from_profile` distinguishes edges derived from cable profile `_mapping` dicts (cheap to rebuild, never stale) from edges derived from `PortMapping` DB state (require sync tracking)

---

### 9.7 `TransferMap`

Represents internal mapping between attachment units **inside passive devices only** (e.g. shuffle modules modeled as devices with front/rear ports, where the mapping logic lives in `PortMapping` DB rows rather than in a cable profile class).

> **Design rule:** Do not persist a `TransferMap` row for any mapping that is deterministically derivable from a cable profile's `_mapping` dictionary. NetBox 4.5 cable profiles already encode connector-to-connector position mappings — including shuffle and breakout semantics — as Python dictionaries on the profile class (e.g. `Trunk4C4PShuffleCableProfile._mapping`). The `get_mapped_position(side, connector, position)` and `get_peer_termination(termination, position)` methods resolve these at runtime. Persisting denormalized copies creates a dual-maintenance problem: when core adds or modifies cable profiles, the plugin's copies silently go stale.

At sync time, the transformer should:
- call `cable.profile_class.get_mapped_position()` directly to derive attachment-unit-level `FineEdge` connectivity for profile-bearing cables
- persist `TransferMap` rows only for device-internal `PortMapping` relationships on passive devices

```python
class TransferMap(NetBoxModel):
    owner_node = models.ForeignKey("PlantNode", related_name="transfer_maps", on_delete=models.CASCADE)

    src_attachment_unit = models.ForeignKey("AttachmentUnit", related_name="transfer_map_sources", on_delete=models.CASCADE)
    dst_attachment_unit = models.ForeignKey("AttachmentUnit", related_name="transfer_map_destinations", on_delete=models.CASCADE)

    mapping_type = models.CharField(max_length=50, choices=TransferMapTypeChoices)
    source_port_mapping = models.ForeignKey(
        "dcim.PortMapping", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+"
    )
    metadata = models.JSONField(default=dict, blank=True)
```

Notes:
- `owner_edge` has been removed; cable-level mappings are derived from profiles at sync time, not persisted as `TransferMap` rows
- `source_port_mapping` anchors back to the NetBox `PortMapping` that this row was derived from
- the `owner_node` FK is now required (not nullable) since every `TransferMap` must belong to a passive device node

Examples:
- attachment-unit shuffle through a passive module (sourced from `PortMapping`)
- attachment-unit polarity remap inside a patch panel (sourced from `PortMapping`)

---

### 9.8 `LaneMap`

Represents fine-grained mapping between signal lanes.

```python
class LaneMap(NetBoxModel):
    owner_node = models.ForeignKey("PlantNode", null=True, blank=True, related_name="lane_maps", on_delete=models.CASCADE)
    owner_edge = models.ForeignKey("CoarseEdge", null=True, blank=True, related_name="lane_maps", on_delete=models.CASCADE)

    src_lane = models.ForeignKey("SignalLane", related_name="lane_map_sources", on_delete=models.CASCADE)
    dst_lane = models.ForeignKey("SignalLane", related_name="lane_map_destinations", on_delete=models.CASCADE)

    mapping_type = models.CharField(max_length=50, choices=LaneMapTypeChoices)
    metadata = models.JSONField(default=dict, blank=True)
```

Examples:
- lane shuffle
- polarity swap
- host-to-optic internal mapping
- cassette lane remap

---

### 9.9 `Fabric`

Represents a distinct fabric as the top-level scoping and policy boundary.

In real multi-tenant GPU environments, multiple fabrics coexist within the same datacenter (training, inference, storage), each with distinct topological properties. Without a first-class model, every query requires passing an unvalidated string, rebuild jobs can't enforce isolation, and the UI has no natural landing page.

```python
class Fabric(NetBoxModel):
    name = models.CharField(max_length=200, unique=True)
    description = models.CharField(max_length=200, blank=True)
    expected_plane_count = models.PositiveIntegerField(default=4)
    tier_depth = models.PositiveIntegerField(default=3)  # leaf, spine, meta-spine
    disjointness_policy = models.CharField(
        max_length=50, choices=DisjointnessChoices, default="full"
    )
    scope_site = models.ForeignKey(
        "dcim.Site", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+"
    )
    scope_location = models.ForeignKey(
        "dcim.Location", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+"
    )
    metadata = models.JSONField(default=dict, blank=True)
```

Notes:
- `Fabric` is the natural owner of expected plane count, disjointness policy, and tier-role definitions (which device roles constitute leaf vs. spine vs. meta-spine for this fabric)
- every API endpoint, rebuild job, and audit inherits fabric-scoped filtering via a single FK rather than a loose string match
- the `Fabric` model becomes the UI entry point: pick a fabric → see planes → see health/audits
- replaces the previous pattern of scattering fabric identity across a `fabric_name` CharField and `default_fabric_field_name` plugin setting

---

### 9.10 `FabricPlane`

Represents a plane within a fabric.

```python
class FabricPlane(NetBoxModel):
    fabric = models.ForeignKey(
        "Fabric", related_name="planes", on_delete=models.CASCADE
    )
    plane_number = models.PositiveIntegerField()
    description = models.CharField(max_length=200, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ("fabric", "plane_number")
```

Notes:
- `fabric_name` is replaced by a proper FK to `Fabric`, providing referential integrity and cascading scope
- plane count validation can be enforced against `fabric.expected_plane_count`

---

### 9.11 `PlaneMembership`

Associates graph objects with planes.

```python
class PlaneMembership(NetBoxModel):
    plane = models.ForeignKey("FabricPlane", related_name="memberships", on_delete=models.CASCADE)

    member_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    member_id = models.PositiveBigIntegerField()
    member = GenericForeignKey("member_type", "member_id")

    membership_role = models.CharField(max_length=50, choices=PlaneMembershipRoleChoices)
    metadata = models.JSONField(default=dict, blank=True)
```

Notes:
- in your environment, plane membership is especially meaningful on `AttachmentUnit`
- membership on `SignalLane` is optional but may be useful for debugging
- some passive artifacts may be shared or transit-scoped

---

### 9.12 Optional `PathIntent`

This can be added in phase 2 if you want policy-vs-reality validation.

```python
class PathIntent(NetBoxModel):
    name = models.CharField(max_length=200)
    source_type = models.ForeignKey(ContentType, related_name="+", on_delete=models.CASCADE)
    source_id = models.PositiveBigIntegerField()
    source = GenericForeignKey("source_type", "source_id")

    destination_type = models.ForeignKey(ContentType, related_name="+", on_delete=models.CASCADE)
    destination_id = models.PositiveBigIntegerField()
    destination = GenericForeignKey("destination_type", "destination_id")

    required_plane_count = models.PositiveIntegerField(default=4)
    required_disjointness = models.CharField(max_length=50, choices=DisjointnessChoices)
    metadata = models.JSONField(default=dict, blank=True)
```

---

## 10. Enumerations / choices

At minimum define the following choice sets:

- `PlantNodeTypeChoices`
- `TerminationPointTypeChoices`
- `AttachmentUnitTypeChoices`
- `SignalLaneKindChoices`
- `SignalEncodingChoices`
- `CoarseEdgeTypeChoices`
- `FineEdgeTypeChoices`
- `TransferMapTypeChoices`
- `LaneMapTypeChoices`
- `GraphResolutionChoices`
- `PlaneMembershipRoleChoices`
- `DisjointnessChoices`
- `FabricTierRoleChoices` (leaf, spine, meta-spine — used by `Fabric.tier_depth` validation)

Suggested values:

### `GraphResolutionChoices`
- `container`
- `attachment_unit`
- `signal_lane`

### `TransferMapTypeChoices`
- `identity`
- `shuffle`
- `breakout`
- `polarity_swap`
- `grouping`

### `LaneMapTypeChoices`
- `identity`
- `lane_shuffle`
- `polarity_swap`
- `serdes_grouping`
- `optic_mux`
- `optic_demux`

### `PlaneMembershipRoleChoices`
- `native`
- `shared`
- `transit`
- `cross_plane_exception`

---

## 11. Data source mapping from NetBox core

The plugin’s sync layer should derive graph objects from the following native NetBox objects.

### 11.1 Devices and modules
Used to create `PlantNode` rows for:
- GPU hosts
- leaf switches
- spine switches
- meta-spine switches
- passive devices if represented as devices

### 11.2 Interfaces
Used to create `TerminationPoint`s for:
- physical host and switch ports

May also be used to create `AttachmentUnit`s for:
- child interfaces representing 200G subinterfaces

### 11.3 Front ports / rear ports
Used to create:
- `TerminationPoint`s for passive patch/shuffle objects
- `AttachmentUnit`s where grouped positions are required

### 11.4 Cables
Used to create:
- `CoarseEdge`s

### 11.5 CablePaths (primary sync input)

NetBox 4.5 maintains pre-computed `CablePath` objects — fully traced end-to-end paths through all passive front/rear port infrastructure — and automatically retraces them on cable or `PortMapping` changes via signals (`retrace_cable_paths`, `update_passthrough_port_paths` in `dcim.signals`).

The plugin should use `CablePath` as the **primary Stage 1 extract input** rather than independently re-deriving traces from raw cables:
- each `CablePath` already encodes the ordered sequence of endpoints, cables, and pass-through ports
- the transformer can decompose a `CablePath` into a chain of `FineEdge` objects at attachment-unit resolution in one pass
- `CablePath.is_complete` and `is_split` flags can classify graph segments as resolved or ambiguous, rather than inventing separate integrity-check logic
- for incremental refresh, hook `CablePath` `post_save` / `post_delete` signals — NetBox already coalesces related cable edits into `CablePath` retraces, giving the plugin scoped rebuild triggers for free

Used to derive:
- the complete ordered path from origin to destination, including intermediate pass-through ports
- `FineEdge` chains at attachment-unit resolution
- path completeness and activity status
- split-path detection for multi-destination cable topologies

### 11.6 Cable profiles
Used to derive:
- profile-aware expansion of `FineEdge`s **at sync time by calling `cable.profile_class.get_mapped_position()` directly**
- the transformer should never persist denormalized copies of cable profile `_mapping` dictionaries
- cable profile classes already provide `get_mapped_position(side, connector, position)` and `get_peer_termination(termination, position)` methods
- when NetBox core adds or modifies cable profiles, the plugin picks up the changes on next rebuild automatically

### 11.7 Many-to-many port mappings (`PortMapping`)
Used to derive:
- `TransferMap`s for device-internal passive mappings only (not for cable-level mappings, which are derived from profiles)
- potentially `LaneMap`s if the passive element's structure needs finer drill-down

### 11.8 Custom fields / tags
Used to derive:
- fabric identity (or use plugin's `Fabric` model FK)
- plane number
- roles
- placement hints
- service ownership

---

## 12. Sync architecture

The sync pipeline should be deterministic and idempotent.

### 12.1 Sync stages

#### Stage 1: Extract
Read required NetBox core objects via ORM selectors:
- devices
- interfaces
- child interfaces
- ports
- **`CablePath` objects** (primary path input — each encodes the complete ordered sequence of endpoints, cables, and pass-through ports)
- cables (for `CoarseEdge` creation and profile resolution)
- cable profiles (accessed via `cable.profile_class` at runtime, not stored)
- port mappings (for device-internal `TransferMap` derivation)
- locations / racks if needed

> **Key change:** `CablePath` replaces raw cable-walking as the primary source of path topology. The extractor should query `CablePath.objects` with prefetch of `path_objects`, then decompose each path into graph edges. This avoids reimplementing NetBox’s trace logic and gets incremental refresh for free via existing signals.

#### Stage 2: Normalize
Convert raw NetBox objects into intermediate DTOs:
- `NodeInput`
- `TerminationInput`
- `AttachmentUnitInput`
- `CableInput`
- `CablePathInput` (decomposed from `CablePath.path_objects`)
- `TransferInput` (from `PortMapping` rows only, not from cable profiles)
- `PlaneInput`
- `FabricInput`

#### Stage 3: Materialize graph objects
Create/update:
- `Fabric`
- `PlantNode`
- `TerminationPoint`
- `AttachmentUnit`
- `SignalLane` if configured
- `CoarseEdge`
- `TransferMap` (from `PortMapping` rows only)
- `LaneMap`
- `FabricPlane`
- `PlaneMembership`

#### Stage 4: Derive edges
Build:
- `FineEdge`s at attachment-unit resolution by decomposing `CablePath` segments
  - for cable segments with profiles: call `cable.profile_class.get_mapped_position()` to resolve connector/position → attachment-unit mapping; mark resulting `FineEdge`s with `derived_from_profile=True`
  - for device pass-through segments: use persisted `TransferMap` rows (sourced from `PortMapping`)
- optionally `FineEdge`s at signal-lane resolution

> **Design rule:** The edge derivation stage should never build its own BFS/DFS traversal. It decomposes pre-traced `CablePath` objects into fine edges. The resolver then queries materialized `FineEdge` chains filtered by plane, rather than walking the graph at query time.

#### Stage 5: Validate graph integrity
Run graph consistency checks:
- orphaned nodes
- unattached attachment units
- invalid transfer maps
- ambiguous endpoints
- duplicate plane assignments where forbidden
- `CablePath.is_complete == False` → mark corresponding graph segments as incomplete
- `CablePath.is_split == True` → flag multi-destination paths for review

### 12.2 Full rebuild vs incremental refresh

#### Full rebuild
Use when:
- plugin first installed
- major schema or profile changes
- large inventory import
- operator explicitly requests rebuild

#### Incremental refresh
Use when:
- a `CablePath` is created, updated, or deleted (primary trigger)
- a device changes
- interface/child interface changes
- passive `PortMapping` changes

Recommended approach:
- hook `CablePath` `post_save` / `post_delete` signals — NetBox already coalesces related cable edits into `CablePath` retraces, giving the plugin scoped rebuild triggers for free
- for `PortMapping` changes, hook the existing `update_passthrough_port_paths` signal chain
- coalesce related changes into a single job when possible

### 12.3 Rebuild scoping
The sync layer should support scoped rebuild by:
- `Fabric` (top-level scope boundary)
- object ID
- rack
- pod
- hall
- plane

This prevents every small cable edit from triggering a graph-wide rebuild storm.

---

## 13. Service-layer design

Keep business logic out of views and serializers.

### 13.1 `extractor.py`
Responsibilities:
- query NetBox source objects, with `CablePath` as the primary path-topology input
- batch/prefetch related data (interfaces, cable terminations, `path_objects`)
- return normalized source bundles

### 13.2 `transformer.py`
Responsibilities:
- convert source bundles into graph-oriented inputs
- classify passive structures
- derive container/attachment/lane cardinality
- call `cable.profile_class.get_mapped_position()` to resolve cable-level mappings at transform time (never persist profile-derived mappings as `TransferMap` rows)
- derive `TransferInput` DTOs only from `PortMapping` rows on passive devices
- decompose `CablePath.path_objects` sequences into ordered `FineEdge` chains

### 13.3 `graph_builder.py`
Responsibilities:
- upsert graph models
- manage keys and source relationships
- create/update memberships
- handle deletion of stale derived objects

### 13.4 `resolver.py`
Responsibilities:
- resolve paths at selected graph resolution by querying pre-materialized `FineEdge` chains (not by walking the graph at query time)
- support scope constraints (by `Fabric`, plane, site, location) and plane constraints
- return path structures for both API and UI

### 13.5 `traversal.py`
Responsibilities:
- adjacency functions for blast-radius and subgraph rendering (the only use cases that require live graph walking)
- BFS/DFS helpers for impact-set computation
- cycle detection
- depth guards

> **Note:** The resolver does not use `traversal.py` for normal path resolution. Path resolution queries materialized `FineEdge` chains derived from `CablePath` objects. `traversal.py` is reserved for blast-radius analysis and subgraph rendering, which genuinely require adjacency-based exploration.

### 13.6 `audits.py`
Responsibilities:
- plane diversity checks
- disjointness audits
- cross-plane contamination checks
- passive artifact sharing analysis

### 13.7 `blast_radius.py`
Responsibilities:
- identify impacted nodes, edges, paths for a failed object
- support failure modes by:
  - node
  - coarse edge
  - fine edge
  - lane

---

## 14. Resolution rules

The plugin should expose explicit graph-resolution modes.

### 14.1 Container resolution
Used for:
- inventory adjacency
- high-level visualization
- rough impact analysis

Objects traversed:
- `PlantNode`
- `TerminationPoint`
- `CoarseEdge`

### 14.2 Attachment-unit resolution
Used for:
- default path resolution
- plane-aware fabric analysis
- operational connectivity validation

Objects traversed:
- `AttachmentUnit`
- `FineEdge`
- `TransferMap`

### 14.3 Signal-lane resolution
Used for:
- forensic debugging
- lane continuity validation
- advanced shuffle/cassette troubleshooting

Objects traversed:
- `SignalLane`
- `FineEdge`
- `LaneMap`

### Resolution policy
Default to **attachment-unit** unless the caller requests otherwise.

---

## 15. API design

Expose the graph through both REST and GraphQL.

### 15.1 REST endpoints

Suggested routes:

```text
/plugins/plant-graph/api/fabrics/
/plugins/plant-graph/api/nodes/
/plugins/plant-graph/api/termination-points/
/plugins/plant-graph/api/attachment-units/
/plugins/plant-graph/api/signal-lanes/
/plugins/plant-graph/api/coarse-edges/
/plugins/plant-graph/api/fine-edges/
/plugins/plant-graph/api/fabric-planes/
/plugins/plant-graph/api/plane-memberships/

/plugins/plant-graph/api/resolve-path/
/plugins/plant-graph/api/neighbors/
/plugins/plant-graph/api/blast-radius/
/plugins/plant-graph/api/plane-audit/
/plugins/plant-graph/api/render-subgraph/
/plugins/plant-graph/api/rebuild-graph/
/plugins/plant-graph/api/refresh-scope/
```

### 15.2 `resolve-path` request example

```json
{
  "source_object_type": "netbox_plant_graph.attachmentunit",
  "source_object_id": 12345,
  "destination_object_type": "netbox_plant_graph.attachmentunit",
  "destination_object_id": 67890,
  "plane": 2,
  "resolution": "attachment_unit",
  "max_depth": 128
}
```

### 15.3 `resolve-path` response example

```json
{
  "resolution": "attachment_unit",
  "path_found": true,
  "path": [
    {"object_type": "attachment_unit", "id": 101, "name": "gpu01:p0:au2"},
    {"object_type": "fine_edge", "id": 5001, "edge_type": "derived_cable_segment"},
    {"object_type": "plant_node", "id": 901, "name": "shuffle-module-h1p4-17"},
    {"object_type": "transfer_map", "id": 3201, "mapping_type": "shuffle"},
    {"object_type": "attachment_unit", "id": 202, "name": "leaf17:Eth1/9:au0"}
  ],
  "summary": {
    "planes_touched": [2],
    "shuffle_modules_crossed": 1,
    "coarse_cables_crossed": 3
  }
}
```

### 15.4 GraphQL

GraphQL should expose:
- object retrieval
- adjacency queries
- filtered subgraph traversal
- path resolution for UI clients

Standard GraphQL types, filters, and query fields should be **registry-driven** — generated from `GRAPHQL_OBJECT_SPECS` using builder factories for `DjangoObjectType` subclasses and `FilterSet` subclasses, then registered in `schema.py` by iterating the spec tuple. Custom query fields for procedural operations (resolve-path, blast-radius) should be explicitly authored alongside the generated ones.

Do not try to implement every graph operation as GraphQL first. Use REST for complex procedural path requests; use GraphQL for rich object exploration.

---

## 16. UI design

The first UI should be practical, not cinematic.

### 16.1 Navigation
Navigation menu entries are **registry-driven** — generated by iterating `get_navigation_groups()` from `object_registry.py`. Each object family's `NavigationSpec` defines its group, label, sort order, and whether to show an add button. Groups appear in `MENU_GROUP_ORDER`: Fabrics, Topology, Mappings, Policy, Audit.

Custom pages (graph overview, path resolver, blast radius, plane audit) are added as explicit `PluginMenuItem` entries alongside the generated ones.

Resulting menu:
- **Fabrics**: Fabrics, Fabric Planes
- **Topology**: Plant Nodes, Termination Points, Attachment Units, Signal Lanes, Coarse Edges, Fine Edges
- **Mappings**: Transfer Maps, Lane Maps
- **Policy**: Plane Memberships
- **Audit**: Audit Findings
- *(custom)* Graph Overview, Path Resolver, Plane Audits, Blast Radius, Rebuild Jobs, Settings/Health

### 16.2 Standard list / detail / edit / delete views
All CRUD views for registry objects are **generated from the registry** using the same `type()` metaclass builder pattern as `netbox_rpki` (see section 8.4). The module-level loop in `views.py` iterates `VIEW_OBJECT_SPECS`, generates classes, and injects them into `globals()` so Django URL routing can reference them by name.

For top-level objects with curated detail pages (`Fabric`, `PlantNode`, `FabricPlane`), use a `MetadataDrivenDetailView` base class that reads `detail_specs.py` metadata to render field groups, related tables, and prefilled add buttons. Simpler objects (`CoarseEdge`, `FineEdge`, `TransferMap`, `SignalLane`) use `simple_detail = True` in their `ViewSpec` and get lightweight generated detail pages.

Objects that need custom behavior (e.g. `FabricPlane` detail with health gauge, `PlantNode` detail with miniature graph inset) can override the generated class:

```python
class FabricPlaneDetailView(globals()['FabricPlaneDetailView']):
    def get_extra_context(self, request, instance):
        ctx = super().get_extra_context(request, instance)
        ctx['health'] = compute_plane_health(instance)
        return ctx

globals()['FabricPlaneDetailView'] = FabricPlaneDetailView
```

### 16.3 Graph Overview page
Capabilities:
- scope by site/hall/pod
- filter by plane
- choose resolution
- render summary graph and counts
- jump into object details

### 16.4 Path Resolver page
Inputs:
- source object
- destination object optional
- resolution
- plane optional
- maximum depth

Outputs:
- ordered path elements
- path summary
- breakdown by plant object category
- optional lane drill-down

### 16.5 Plane Audit page
Outputs:
- missing plane paths
- shared passive artifacts across planes
- disjointness violations
- inconsistent memberships
- orphaned child interfaces

### 16.6 Blast Radius page
Inputs:
- object selection
- failure mode
- resolution
- scope limits

Outputs:
- impacted paths
- impacted attachment units
- impacted planes
- shared infrastructure hotspots

### 16.7 Object detail badges
On selected NetBox object detail pages via template extension:
- whether object is represented in plant graph
- graph object counts
- plane memberships
- quick links to graph explorer/path resolver

---

## 17. Sync and rebuild jobs

The plugin should ship with explicit background jobs.

### 17.1 `FullGraphRebuildJob`
Inputs:
- scope
- include_signal_lanes
- dry_run

Behavior:
- rebuild all plugin graph artifacts within scope
- produce counts and summary stats

### 17.2 `IncrementalRefreshJob`
Inputs:
- changed object type
- changed object id
- force_neighbor_refresh

Behavior:
- determine rebuild scope
- refresh only affected graph region

### 17.3 `PlaneAuditJob`
Inputs:
- fabric
- plane set
- scope

Behavior:
- run policy checks and persist/report findings

### 17.4 `BlastRadiusJob`
Inputs:
- target object
- failure mode
- resolution

Behavior:
- compute impact set and return persisted result

---

## 18. Validation and integrity rules

At minimum implement the following checks.

### 18.1 Source uniqueness
A single source object should not accidentally produce duplicate graph objects of the same semantic type.

### 18.2 Attachment-unit containment
Every `AttachmentUnit` must belong to exactly one `TerminationPoint`.

### 18.3 Signal-lane containment
Every `SignalLane` must belong to exactly one `AttachmentUnit`.

### 18.4 Coarse edge endpoint validity
A `CoarseEdge` must connect exactly two valid `TerminationPoint`s.

### 18.5 Fine-edge granularity validity
If `resolution == attachment_unit`, lane fields must be null.  
If `resolution == signal_lane`, attachment-unit fields may be null or auxiliary.

### 18.6 Transfer-map integrity
A `TransferMap` must have exactly one owner context:
- `owner_node` is required (not nullable)
- `TransferMap` rows must only exist for device-internal `PortMapping`-derived mappings, never for cable-profile-derived mappings
- `source_port_mapping` should reference the originating `PortMapping` where applicable

### 18.7 Plane membership sanity
Disallow invalid duplicate memberships where policy says an object must be plane-native to only one plane.

### 18.8 No silent ambiguity
If the plugin cannot deterministically derive a mapping from source data, it should:
- create an explicit warning/finding
- mark the affected graph segment unresolved
- avoid silently fabricating certainty

---

## 19. Policy and audit model

Phase 1 can compute audits on demand.  
Phase 2 should persist findings.

Suggested phase-2 model:

```python
class AuditFinding(NetBoxModel):
    finding_type = models.CharField(max_length=100)
    severity = models.CharField(max_length=32)
    object_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    object = GenericForeignKey("object_type", "object_id")
    message = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)
```

Suggested finding types:
- missing_plane_membership
- duplicate_plane_assignment
- shared_shuffle_artifact
- unresolved_profile_mapping
- orphaned_attachment_unit
- lane_continuity_break
- path_disjointness_violation

---

## 20. Recommended implementation phases

### Phase 0: bootstrap
Deliver:
- plugin skeleton
- `object_specs.py` with frozen dataclasses (ObjectSpec, LabelSpec, RouteSpec, ApiSpec, NavigationSpec, FormSpec, FilterSetSpec, GraphQLSpec, TableSpec, ViewSpec)
- `object_registry.py` with `OBJECT_SPECS` tuple, `build_standard_object_spec()` helper, filtered subsets (`API_OBJECT_SPECS`, `GRAPHQL_OBJECT_SPECS`, `FILTERSET_OBJECT_SPECS`, `FORM_OBJECT_SPECS`, `TABLE_OBJECT_SPECS`, `VIEW_OBJECT_SPECS`, `SIMPLE_DETAIL_VIEW_OBJECT_SPECS`), `MENU_GROUP_ORDER`, `get_navigation_groups()`
- surface builder factories: `build_filterset_class()`, `build_table_class()`, `build_serializer_class()`, `build_viewset_class()`, `build_list_view_class()`, `build_detail_view_class()`, `build_edit_view_class()`, `build_delete_view_class()`, `build_menu_item()`
- registry-driven URL registration, API router registration, navigation generation
- `MetadataDrivenDetailView` base class and shared `object_detail.html` template
- registry-driven smoke tests (`tests/registry_scenarios.py`)
- base models and migrations (including `Fabric`)
- menu and landing page
- health page

Repo status for this snapshot:
- completed: plugin skeleton, object spec dataclasses, object registry, generated UI/API/GraphQL/navigation surfaces, metadata-driven detail template, base models/migration, registry-aware tests
- completed in the current slice: landing/custom pages exist, and graph overview/health/path resolver/plane audit/lane-drilldown/blast-radius pages now render live service results

### Phase 1: container + attachment-unit graph
Deliver:
- `Fabric`
- `PlantNode`
- `TerminationPoint`
- `AttachmentUnit`
- `CoarseEdge`
- `TransferMap` (scoped to `PortMapping`-derived device-internal mappings only)
- `FabricPlane` (with FK to `Fabric`)
- `PlaneMembership`
- `CablePath`-based full rebuild job (extract from `CablePath.path_objects`, derive `FineEdge` chains using `cable.profile_class.get_mapped_position()`)
- path resolver at attachment-unit resolution (query materialized `FineEdge` chains, no live graph walking)
- basic UI pages with `Fabric` as the top-level navigation entry point

Implementation note for this repo snapshot:
- implemented: `Fabric`, `PlantNode`, `TerminationPoint`, `AttachmentUnit`, `CoarseEdge`, `FineEdge`, `TransferMap`, `FabricPlane`, and `PlaneMembership`
- implemented: full rebuild from `CablePath`, attachment-unit path resolution, `Fabric`-scoped rebuilds, and integration fixtures/tests for direct, passthrough, and multiplane shuffle topologies
- sync expands `CablePath.path_objects` with NetBox cable profile `get_mapped_position()` lookups
- channelized parent interfaces map cable positions onto child-interface attachment units
- plane memberships seeded on child interfaces are propagated across connected passive attachment-unit components
- fanout cable objects currently materialize one `CoarseEdge` per termination pair, because the Phase 1 `CoarseEdge` schema is two-ended

### Phase 2: signal-lane support
Deliver:
- `SignalLane`
- `LaneMap`
- signal-lane `FineEdge`s
- lane drill-down UI
- lane-aware resolver

Repo status for this snapshot:
- implemented in the current slice: sync materializes `SignalLane`, signal-lane `FineEdge`, and `LaneMap` rows for channelized topologies
- implemented in the current slice: resolver supports `resolution='signal_lane'` traversal across derived cable segments and `PortMapping`-derived lane maps
- implemented in the current slice: operational UI and GraphQL surfaces accept both plugin graph objects and core NetBox `Interface`/`FrontPort`/`RearPort` objects for resolver and blast-radius workflows
- implemented in the current slice: lane drilldown is available through operational UI, GraphQL, badges, and detail-page supplementary cards
- implemented in the current slice: operational result pages render direct object links and contextual metadata for path, audit, and blast-radius investigation
- not yet complete: richer lane-first GraphQL/query ergonomics and production-ready lane-aware operational workflows

### Phase 3: audits and policy
Deliver:
- plane audit job
- blast-radius analysis
- audit findings persistence
- disjointness and contamination checks

Repo status for this snapshot:
- partial only: on-demand plane audit and blast-radius services/jobs exist
- partial only: `AuditFinding` model/surfaces exist
- partial only: unresolved-input coverage now includes missing cable profiles, missing child interfaces, incomplete child-interface sets, partial profile mappings with missing peer positions, orphaned attachment units, and missing passive `PortMapping` coverage
- partial only: operational audit surfaces now provide linked next actions into resolver, blast-radius, and lane-drilldown workflows
- not yet complete: durable finding persistence and broader policy/disjointness workflow coverage

### Phase 4: polish and performance
Deliver:
- incremental refresh via `CablePath` `post_save`/`post_delete` signal hooks
- caching
- subgraph rendering optimization
- richer template extensions
- user-facing graph summaries on object detail pages

---

## 21. Performance guidance

### 21.1 Keep the canonical graph relational
Do not start with Neo4j or another external graph database unless the relational approach is proven insufficient. The first version should keep operational complexity low.

### 21.2 Use selective materialization
Materialize:
- attachment-unit graph always
- signal-lane graph only if enabled or if required by environment

### 21.3 Prefetch aggressively in sync jobs
When extracting NetBox objects:
- prefetch related interfaces
- prefetch cable terminations and profiles
- avoid ORM N+1 disasters

### 21.4 Build scope-aware rebuilds
Never make “one cable edited” mean “rebuild the entire planet.”

### 21.5 Cache resolved paths when helpful
Possible future optimization:
- cache path results keyed by resolution + scope + object version fingerprints

---

## 22. Testing strategy

### 22.1 Registry-driven smoke tests
Create `tests/registry_scenarios.py` with parameterized test scenarios generated from the object registry. For every `ObjectSpec` in `OBJECT_SPECS`, automatically verify:
- model can be instantiated and saved
- API serializer round-trips correctly
- API viewset returns 200 on list endpoint
- filterset filters without error
- table renders without error
- list view returns 200
- detail view returns 200
- GraphQL type resolves (if `spec.graphql` is not None)

This eliminates the need to hand-write boilerplate smoke tests for each new model. The test file should look like:

```python
import pytest
from netbox_plant_graph.object_registry import OBJECT_SPECS, API_OBJECT_SPECS, VIEW_OBJECT_SPECS

@pytest.mark.parametrize("spec", API_OBJECT_SPECS, ids=lambda s: s.registry_key)
def test_api_list_returns_200(spec, admin_client):
    url = f"/api/plugins/plant-graph/{spec.api.basename}/"
    response = admin_client.get(url)
    assert response.status_code == 200

@pytest.mark.parametrize("spec", VIEW_OBJECT_SPECS, ids=lambda s: s.registry_key)
def test_list_view_returns_200(spec, admin_client):
    url = f"/plugins/plant-graph/{spec.routes.resolved_path_prefix}/"
    response = admin_client.get(url)
    assert response.status_code == 200
```

### 22.2 Unit tests
Cover:
- graph model constraints
- mapping derivation rules
- resolution-specific traversal
- validation rules

### 22.3 Integration tests
Use fixtures representing:
- simple point-to-point path
- GPU 800G port with four 200G child interfaces
- shuffle cable at GPU ↔ leaf
- shuffle module at leaf ↔ spine
- lane remap case at signal-lane resolution
- cross-plane violation case

### 22.4 Regression tests
Every bug in mapping derivation or path resolution gets a fixture and a permanent test.

### 22.5 Performance tests
Test:
- full rebuild on realistic pod-sized topology
- path resolution at container vs attachment vs lane resolution
- blast-radius query for high-fanout shuffle artifacts

---

## 23. Example end-to-end scenario

Assume the following real-ish topology:

- GPU server `gpu-r1-p07-u19`
- physical NIC port `p0` at 800G
- four 200G child transport units:
  - `au0` plane 0
  - `au1` plane 1
  - `au2` plane 2
  - `au3` plane 3
- `au2` traverses:
  - host-side breakout mapping
  - GPU-hall shuffle cable
  - hall shuffle module
  - leaf downlink `leaf17:Eth1/9:au0`

At the graph layer this should become:

### Plant nodes
- GPU host
- shuffle cable assembly
- shuffle module
- leaf switch

### Termination points
- `gpu-r1-p07-u19:p0`
- shuffle cable face A
- shuffle cable face B
- shuffle module port A
- shuffle module port B
- `leaf17:Eth1/9`

### Attachment units
- `gpu-r1-p07-u19:p0:au0`
- `gpu-r1-p07-u19:p0:au1`
- `gpu-r1-p07-u19:p0:au2`
- `gpu-r1-p07-u19:p0:au3`
- shuffle-unit group representing plane-2 path
- `leaf17:Eth1/9:au0`

### Optional signal lanes
- `gpu-r1-p07-u19:p0:au2:l0`
- `gpu-r1-p07-u19:p0:au2:l1`
- ...
- `leaf17:Eth1/9:au0:l0`
- ...

### Mappings
- attachment-unit breakout/selection mapping from host-side container to `au2`
- transfer map through shuffle module
- optional lane shuffle/polarity map

This scenario should be included as a fixture and demonstrated in both:
- path resolution
- blast-radius analysis

---

## 24. Suggested coding conventions

- Use a dedicated service layer for graph derivation and resolution
- Use `GenericForeignKey` sparingly and consistently
- Keep graph object naming deterministic and human-readable
- Treat every derived object as reproducible from source data
- Avoid burying semantics in opaque JSON blobs
- Maintain explicit `source` references back to NetBox objects wherever possible
- Log unresolved derivation cases loudly
- **Never hand-author a form, filterset, table, serializer, viewset, or URL pattern for a standard CRUD object — generate it from the registry**
- **Keep explicit public names (class names, basenames, URL slugs) in the registry rather than deriving them from model names** — stable external names matter more than deduplication purity
- **When an object needs custom behavior beyond what the registry generates, subclass the generated class and re-inject it into the class map** (see section 8.4 override pattern)

---

## 25. Suggested first API contracts

Implement these first:

### `POST /resolve-path/`
Must support:
- source object
- destination optional
- plane optional
- resolution
- max depth

### `POST /render-subgraph/`
Must support:
- scope
- plane
- resolution
- include_passive_objects
- include_orphans

### `POST /plane-audit/`
Must support:
- scope
- expected_plane_count
- disjointness mode

### `POST /blast-radius/`
Must support:
- target object
- failure mode
- resolution

---

## 26. Risks and sharp edges

### 26.1 Source-model ambiguity
Some passive semantics may not be fully represented in NetBox inventory. The plugin must tolerate partial truth and flag gaps.

### 26.2 Over-materialization
Persisting every possible lane-level edge everywhere can create graph bloat. Start conservative.

### 26.3 Event storms
Naive incremental refresh can flood the job queue if many related objects change at once.

### 26.4 Scope creep
Do not try to solve every optical or telecom edge case in the first release.

### 26.5 UX overload
Lane-level detail is useful but should not be the default visual mode.

### 26.6 CablePath coupling
The plugin's sync pipeline depends on NetBox core's `CablePath` pre-computation. If `CablePath` behavior changes in a future NetBox release (e.g. path representation format, signal semantics, or the `path_objects` property), the plugin's extractor/transformer must be updated accordingly. Pin `min_version` and test against each supported NetBox release.

### 26.7 Cable profile evolution
The plugin calls `cable.profile_class.get_mapped_position()` at sync time rather than persisting profile mappings. This avoids stale copies, but means the plugin must tolerate new profile types being added to NetBox core. If a cable has no `profile_class` (profile is blank or unrecognized), the transformer should create an unresolved `FineEdge` and flag it rather than silently skipping.

### 26.8 Multi-fabric scoping leaks
With a first-class `Fabric` model, all queries and rebuild jobs must enforce fabric-scoped isolation. A bug that allows cross-fabric graph contamination (e.g. a `FineEdge` connecting attachment units in different fabrics) could produce silently wrong path resolution results. Add a validation rule that `FineEdge` endpoints must belong to the same `Fabric`.

---

## 27. Implementation advice for the coding agent

If an agentic coding assistant is given this document, the first milestone should be:

### Milestone A
- create plugin skeleton
- implement registry architecture (Phase 0):
  - `object_specs.py` frozen dataclass hierarchy
  - `object_registry.py` with all Phase 1 object specs and filtered subsets
  - surface builder factories for filtersets, tables, forms, serializers, viewsets, views, navigation, GraphQL types, URLs
  - `MetadataDrivenDetailView` base class and `object_detail.html` shared template
  - `detail_specs.py` for curated detail pages on `Fabric`, `PlantNode`, `FabricPlane`
  - registry-driven smoke test scaffold (`tests/registry_scenarios.py`)
- add plugin config and menu (registry-driven navigation)
- implement models:
  - `Fabric`
  - `PlantNode`
  - `TerminationPoint`
  - `AttachmentUnit`
  - `CoarseEdge`
  - `FineEdge` (with `derived_from_profile` flag)
  - `TransferMap` (scoped to `PortMapping`-derived device-internal mappings only)
  - `FabricPlane` (with FK to `Fabric`)
  - `PlaneMembership`
- add migrations
- all standard CRUD surfaces (views, tables, filtersets, forms, serializers, viewsets, GraphQL types, URLs, navigation) generated from the registry — no hand-authored boilerplate
- override generated classes where custom behavior is needed (e.g. `FabricSerializer` with plane summary, `PlantNodeDetailView` with graph inset)
- implement `FullGraphRebuildJob` using `CablePath` as primary sync input:
  - extract from `CablePath.path_objects`
  - derive `FineEdge` chains by calling `cable.profile_class.get_mapped_position()` at sync time
  - persist `TransferMap` rows only for `PortMapping`-sourced device-internal mappings
- implement `resolve_path()` at attachment-unit resolution by querying materialized `FineEdge` chains (no live graph walking)
- create one fixture for:
  - GPU host with one physical 800G port
  - four 200G child units
  - one shuffle module (with `PortMapping` rows)
  - one leaf switch
  - corresponding `CablePath` objects
- write integration tests for path resolution, plane membership, and `Fabric`-scoped queries

Implementation note for this repo snapshot:
- Milestone A is substantially complete in the current repo
- the generated CRUD/UI layer needed follow-up fixes after initial implementation:
  - generated filter forms now set `model` for NetBox mixins
  - generated tables no longer expose nonexistent `changelog` row actions
  - generated list views now advertise only actions backed by real plugin routes
  - route generation now respects explicit public route slugs rather than assuming `registry_key == route name`
- the integration fixture above is now covered by `netbox_plant_graph.tests.topology.build_multiplane_shuffle_topology()`

### Milestone B
- add signal-lane objects and lane maps
- expand resolver for signal-lane resolution
- add blast-radius and plane-audit jobs
- add GraphQL schema and richer UI pages

Status in this repo snapshot:
- complete in the current slice: signal-lane objects are materialized during rebuild and lane maps are persisted from `TransferMap` relationships
- complete in the current slice: resolver supports signal-lane traversal
- already present from earlier slices: blast-radius and plane-audit jobs/services exist in basic form
- still pending: richer GraphQL and UI experiences for lane-first exploration

Do not begin with:
- lane-level visualization
- incremental refresh
- aggressive caching
- exotic policy engine features

Get the graph correct before making it flashy.

---

## 28. V1 Design Decisions And Wave 2 Revisit List

To keep implementation moving, the following decisions are treated as **settled for v1**. They should be revisited explicitly in **Wave 2** once the core graph, resolver, and operational pages are stable.

1. **[SETTLED FOR V1]** 200G child transport units must exist explicitly as NetBox child interfaces. The plugin does not synthesize child interfaces in v1.
2. **[SETTLED FOR V1]** Plane identity is sourced from a child-interface custom field, using the configured default field name. Tags and naming conventions are out of scope for v1.
3. **[SETTLED FOR V1]** Signal lanes are materialized when the plugin's `materialize_signal_lanes` setting is enabled. Per-scope or per-object selective materialization is deferred to Wave 2.
4. **[SETTLED FOR V1]** Shuffle modules are modeled as ordinary NetBox devices with passive ports and `PortMapping` rows. Plugin-native plant-node-only shuffle modeling is deferred.
5. **[SETTLED FOR V1]** The rebuild/sync boundary is `Fabric` scope. Narrower rebuild scopes such as rack, pod, or hall are deferred.
6. **[SETTLED FOR V1]** Cables with no profile set should not be silently treated as identity mappings. They should be treated as unresolved/incomplete topology inputs and surfaced through audit-oriented workflows as that coverage matures.
7. **[SETTLED FOR V1]** Unresolved or ambiguous graph segments are recognized as legitimate outcomes, but v1 does not require a fully separate persistent object model for them.
8. **[SETTLED FOR V1]** `AuditFinding` remains on-demand until the audit workflow stabilizes. Durable finding persistence is deferred to Wave 2.
9. **[SETTLED FOR V1]** `FineEdge` traversal order is reconstructed from endpoint adjacency at query time. Ordered fine-edge chains are deferred unless query behavior proves ambiguous.
10. **[SETTLED]** Fabric identity is modeled as a first-class `Fabric` model, not a CharField or plugin setting. `FabricPlane` uses a FK to `Fabric`.
11. **[SETTLED]** Cable profile mappings are resolved at sync time via `cable.profile_class.get_mapped_position()`, never persisted as `TransferMap` rows. `TransferMap` is scoped to `PortMapping`-derived device-internal mappings only.
12. **[SETTLED]** `CablePath` is the primary sync input for path topology. The plugin hooks `CablePath` signals for incremental refresh rather than designing bespoke change-detection.

Wave 2 revisit list:

- synthesized child transport units where inventory does not expose explicit child interfaces
- alternate plane sources such as tags or naming conventions
- selective signal-lane materialization by scope, object type, or operational mode
- persisted unresolved-segment objects if operators need durable remediation workflows
- persistent `AuditFinding` records with retention/lifecycle semantics
- narrower incremental rebuild scopes if full-`Fabric` rebuilds become operationally too expensive
- richer shuffle modeling if NetBox passive-device abstractions prove insufficient
- ordered fine-edge chains if adjacency reconstruction proves too lossy
- template-extension badges once the operational UX surfaces are finalized

---

## 29. Recommendation summary

Build this as a **NetBox plugin that acts as an adjacent graph engine**.

Keep NetBox as the canonical source for:
- inventory
- physical interfaces and child interfaces
- structured cable objects
- passive port mappings

Use the plugin to provide:
- graph normalization (derived from `CablePath` objects and cable profile classes, not reimplemented trace logic)
- multi-resolution topology
- `Fabric`-scoped, plane-aware path resolution
- optional PAM4 lane drill-down
- blast-radius and validation workflows
- consumer-friendly APIs
- **registry-driven surface generation** (forms, filtersets, tables, serializers, viewsets, views, navigation, GraphQL types, URLs) — eliminating hand-authored boilerplate and enabling new object families to be added with a single registry entry

The most important modeling rule is this:

> **Physical ports are containers.  
> Attachment units are operational endpoints.  
> Signal lanes are forensic endpoints.**

If the implementation preserves that hierarchy cleanly, the plugin will remain both expressive and survivable.

---

## 30. Immediate next step

Milestone A is now substantially complete, with attachment-unit resolution working as the operational default.

The next implementation step should remain **Milestone B**, but shift to the remaining gaps:

- deepen lane-focused API/GraphQL/UI affordances beyond the current drilldown/query surfaces and signal-lane operational queries
- expand unresolved-input audit coverage beyond the current blank-profile, missing-child-interface, partial-child-interface-set, partial-profile-mapping, orphaned-attachment-unit, and missing-port-mapping cases without expanding the persistence model yet
- deepen the operational pages beyond the current health/result rendering and next-action affordances into more guided remediation workflows

Do not add aggressive caching or broad incremental-refresh behavior until the remaining Milestone B graph semantics are test-covered.
