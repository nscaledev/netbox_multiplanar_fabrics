from .models import (
    AssemblyTemplate,
    AttachmentUnit,
    DeploymentPlan,
    Fabric,
    FabricPlane,
    PlantNode,
    RackPopulationTemplate,
    SpatialTemplate,
    TerminationPoint,
)

search_index = (
    (Fabric, ('name', 'description')),
    (FabricPlane, ('plane',)),
    (PlantNode, ('name', 'node_type')),
    (TerminationPoint, ('name',)),
    (AttachmentUnit, ('name',)),
    (AssemblyTemplate, ('name', 'description', 'part_number')),
    (SpatialTemplate, ('name', 'description')),
    (DeploymentPlan, ('name', 'description')),
    (RackPopulationTemplate, ('name', 'description')),
)
