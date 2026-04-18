from dataclasses import dataclass


@dataclass(frozen=True)
class DetailFieldSpec:
    name: str
    label: str


DETAIL_SPECS = {
    'fabric': (
        DetailFieldSpec(name='name', label='Name'),
        DetailFieldSpec(name='description', label='Description'),
        DetailFieldSpec(name='expected_plane_count', label='Expected Plane Count'),
        DetailFieldSpec(name='tier_depth', label='Tier Depth'),
        DetailFieldSpec(name='disjointness_policy', label='Disjointness Policy'),
    ),
    'fabricplane': (
        DetailFieldSpec(name='fabric', label='Fabric'),
        DetailFieldSpec(name='plane_number', label='Plane Number'),
        DetailFieldSpec(name='description', label='Description'),
    ),
    'plantnode': (
        DetailFieldSpec(name='fabric', label='Fabric'),
        DetailFieldSpec(name='name', label='Name'),
        DetailFieldSpec(name='node_type', label='Node Type'),
        DetailFieldSpec(name='role', label='Role'),
        DetailFieldSpec(name='status', label='Status'),
    ),
}
