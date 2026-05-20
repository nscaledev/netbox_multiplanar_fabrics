from .models import Endpoint, Fabric, FabricArchitecture, FabricNode


search_index = (
    (FabricArchitecture, ('name', 'slug', 'version', 'description')),
    (Fabric, ('name', 'slug', 'status')),
    (FabricNode, ('name', 'address', 'node_kind')),
    (Endpoint, ('name', 'address', 'endpoint_kind', 'connector_kind')),
)
