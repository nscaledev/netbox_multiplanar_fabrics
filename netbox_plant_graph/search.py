from .models import AttachmentUnit, Fabric, FabricPlane, PlantNode, TerminationPoint

search_index = (
    (Fabric, ('name', 'description')),
    (FabricPlane, ('plane',)),
    (PlantNode, ('name', 'node_type')),
    (TerminationPoint, ('name',)),
    (AttachmentUnit, ('name',)),
)
