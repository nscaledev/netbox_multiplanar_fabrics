from utilities.choices import ChoiceSet


class PlantNodeTypeChoices(ChoiceSet):
    key = 'PlantGraph.plant_node_type'

    CHOICES = [
        ('device', 'Device'),
        ('patch_panel', 'Patch Panel'),
        ('shuffle_module', 'Shuffle Module'),
        ('cassette', 'Cassette'),
        ('cable_assembly', 'Cable Assembly'),
        ('trunk_bundle', 'Trunk Bundle'),
        ('passive_device', 'Passive Device'),
    ]


class TerminationPointTypeChoices(ChoiceSet):
    key = 'PlantGraph.termination_point_type'

    CHOICES = [
        ('interface', 'Interface'),
        ('front_port', 'Front Port'),
        ('rear_port', 'Rear Port'),
        ('connector', 'Connector'),
        ('panel_face', 'Panel Face'),
    ]


class AttachmentUnitTypeChoices(ChoiceSet):
    key = 'PlantGraph.attachment_unit_type'

    CHOICES = [
        ('child_interface', 'Child Interface'),
        ('passive_group', 'Passive Group'),
        ('logical_slice', 'Logical Slice'),
    ]


class SignalLaneKindChoices(ChoiceSet):
    key = 'PlantGraph.signal_lane_kind'

    CHOICES = [
        ('electrical_tx', 'Electrical TX'),
        ('electrical_rx', 'Electrical RX'),
        ('optical_tx', 'Optical TX'),
        ('optical_rx', 'Optical RX'),
    ]


class SignalEncodingChoices(ChoiceSet):
    key = 'PlantGraph.signal_encoding'

    CHOICES = [
        ('pam4', 'PAM4'),
        ('nrz', 'NRZ'),
        ('unknown', 'Unknown'),
    ]


class CoarseEdgeTypeChoices(ChoiceSet):
    key = 'PlantGraph.coarse_edge_type'

    CHOICES = [
        ('cable', 'Cable'),
        ('logical', 'Logical'),
    ]


class FineEdgeTypeChoices(ChoiceSet):
    key = 'PlantGraph.fine_edge_type'

    CHOICES = [
        ('derived_cable_segment', 'Derived Cable Segment'),
        ('passthrough_map', 'Pass-Through Map'),
        ('lane_segment', 'Lane Segment'),
    ]


class GraphResolutionChoices(ChoiceSet):
    key = 'PlantGraph.graph_resolution'

    CHOICES = [
        ('container', 'Container'),
        ('attachment_unit', 'Attachment Unit'),
        ('signal_lane', 'Signal Lane'),
    ]


class TransferMapTypeChoices(ChoiceSet):
    key = 'PlantGraph.transfer_map_type'

    CHOICES = [
        ('identity', 'Identity'),
        ('shuffle', 'Shuffle'),
        ('breakout', 'Breakout'),
        ('polarity_swap', 'Polarity Swap'),
        ('grouping', 'Grouping'),
    ]


class LaneMapTypeChoices(ChoiceSet):
    key = 'PlantGraph.lane_map_type'

    CHOICES = [
        ('identity', 'Identity'),
        ('lane_shuffle', 'Lane Shuffle'),
        ('polarity_swap', 'Polarity Swap'),
        ('serdes_grouping', 'SerDes Grouping'),
        ('optic_mux', 'Optic Mux'),
        ('optic_demux', 'Optic Demux'),
    ]


class PlaneMembershipRoleChoices(ChoiceSet):
    key = 'PlantGraph.plane_membership_role'

    CHOICES = [
        ('native', 'Native'),
        ('shared', 'Shared'),
        ('transit', 'Transit'),
        ('cross_plane_exception', 'Cross-Plane Exception'),
    ]


class DisjointnessChoices(ChoiceSet):
    key = 'PlantGraph.disjointness'

    CHOICES = [
        ('full', 'Full'),
        ('tier_aware', 'Tier Aware'),
        ('best_effort', 'Best Effort'),
    ]
