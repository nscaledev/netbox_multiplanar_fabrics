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


class DisjointnessExceptionTypeChoices(ChoiceSet):
    key = 'PlantGraph.disjointness_exception_type'

    CHOICES = [
        ('shared_passive_artifact', 'Shared Passive Artifact'),
        ('cross_plane_fine_edge', 'Cross-Plane Fine Edge'),
        ('contamination_domain', 'Contamination Domain'),
    ]


class DisjointnessExceptionScopeChoices(ChoiceSet):
    key = 'PlantGraph.disjointness_exception_scope'

    CHOICES = [
        ('artifact', 'Artifact'),
        ('edge', 'Edge'),
        ('domain', 'Domain'),
        ('path_pair', 'Path Pair'),
        ('lane_group', 'Lane Group'),
    ]


class DisjointnessExceptionStatusChoices(ChoiceSet):
    key = 'PlantGraph.disjointness_exception_status'

    CHOICES = [
        ('draft', 'Draft'),
        ('approved', 'Approved'),
        ('expired', 'Expired'),
    ]


class AuditRunStatusChoices(ChoiceSet):
    key = 'PlantGraph.audit_run_status'

    CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]


class GraphBuildRunStatusChoices(ChoiceSet):
    key = 'PlantGraph.graph_build_run_status'

    CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]


class GraphBuildRunTriggerModeChoices(ChoiceSet):
    key = 'PlantGraph.graph_build_run_trigger_mode'

    CHOICES = [
        ('manual', 'Manual'),
        ('job', 'Job'),
        ('incremental', 'Incremental'),
        ('post_change', 'Post Change'),
    ]


class UnresolvedStateSummaryKindChoices(ChoiceSet):
    key = 'PlantGraph.unresolved_state_summary_kind'

    CHOICES = [
        ('lane_group', 'Lane Group'),
        ('segment', 'Segment'),
    ]


class UnresolvedStateCauseChoices(ChoiceSet):
    key = 'PlantGraph.unresolved_state_cause'

    CHOICES = [
        ('missing_cable_profile', 'Missing Cable Profile'),
        ('missing_child_interface', 'Missing Child Interface'),
        ('incomplete_child_interface_set', 'Incomplete Child Interface Set'),
        ('missing_port_mapping', 'Missing Port Mapping'),
        ('profile_returned_none', 'Profile Returned None'),
        ('profile_error', 'Profile Error'),
        ('missing_peer_position', 'Missing Peer Position'),
        ('orphaned_attachment_unit', 'Orphaned Attachment Unit'),
        ('missing_breakout_profile', 'Missing Breakout Profile'),
    ]


class AuditRunTriggerModeChoices(ChoiceSet):
    key = 'PlantGraph.audit_run_trigger_mode'

    CHOICES = [
        ('manual', 'Manual'),
        ('job', 'Job'),
        ('post_rebuild', 'Post Rebuild'),
    ]


class AuditFindingStatusChoices(ChoiceSet):
    key = 'PlantGraph.audit_finding_status'

    CHOICES = [
        ('open', 'Open'),
        ('acknowledged', 'Acknowledged'),
        ('in_progress', 'In Progress'),
        ('suppressed', 'Suppressed'),
        ('resolved', 'Resolved'),
    ]


class AuditFindingEventTypeChoices(ChoiceSet):
    key = 'PlantGraph.audit_finding_event_type'

    CHOICES = [
        ('opened', 'Opened'),
        ('reopened', 'Reopened'),
        ('status_changed', 'Status Changed'),
        ('suppressed', 'Suppressed'),
        ('unsuppressed', 'Unsuppressed'),
        ('expired', 'Expired'),
        ('resolved', 'Resolved'),
        ('auto_resolved', 'Auto Resolved'),
    ]


class BreakoutProfileMappingModeChoices(ChoiceSet):
    key = 'PlantGraph.breakout_profile_mapping_mode'

    CHOICES = [
        ('sequential', 'Sequential (child ordinal = parent position \u2212 1)'),
        ('explicit', 'Explicit (use position_map JSON)'),
    ]


# --- Planning / Assembly Template choices ---


class AssemblyTypeChoices(ChoiceSet):
    key = 'PlantGraph.assembly_type'

    CHOICES = [
        ('shuffle_trunk', 'Shuffle Trunk'),
        ('shuffle_board', 'Shuffle Board'),
        ('breakout_cassette', 'Breakout Cassette'),
        ('patch_panel', 'Patch Panel'),
        ('trunk_bundle', 'Trunk Bundle'),
        ('custom', 'Custom'),
    ]


class AssemblyConnectorSideChoices(ChoiceSet):
    key = 'PlantGraph.assembly_connector_side'

    CHOICES = [
        ('A', 'A'),
        ('B', 'B'),
    ]


class AssemblyConnectorTypeChoices(ChoiceSet):
    key = 'PlantGraph.assembly_connector_type'

    CHOICES = [
        ('mpo-8', 'MPO-8'),
        ('mpo-12', 'MPO-12'),
        ('mpo-16', 'MPO-16'),
        ('mpo-24', 'MPO-24'),
        ('mtp-16', 'MTP-16'),
        ('mtp-24', 'MTP-24'),
        ('lc-duplex', 'LC Duplex'),
        ('lc-simplex', 'LC Simplex'),
        ('sc-duplex', 'SC Duplex'),
        ('custom', 'Custom'),
    ]


class AssemblyMappingTypeChoices(ChoiceSet):
    key = 'PlantGraph.assembly_mapping_type'

    CHOICES = [
        ('identity', 'Identity'),
        ('shuffle', 'Shuffle'),
        ('breakout', 'Breakout'),
        ('polarity_swap', 'Polarity Swap'),
    ]


# --- Planning / Spatial Placement choices ---


class CoordinateUnitChoices(ChoiceSet):
    key = 'PlantGraph.coordinate_unit'

    CHOICES = [
        ('meters', 'Meters'),
        ('feet', 'Feet'),
        ('rack_units', 'Rack Units'),
    ]


# --- Planning / Spatial Template choices ---


class SpatialNodeTypeChoices(ChoiceSet):
    key = 'PlantGraph.spatial_node_type'

    CHOICES = [
        ('site', 'Site'),
        ('building', 'Building'),
        ('hall', 'Hall'),
        ('floor', 'Floor'),
        ('room', 'Room'),
        ('pod', 'Pod'),
        ('row', 'Row'),
        ('rack_group', 'Rack Group'),
        ('rack_position', 'Rack Position'),
        ('custom', 'Custom'),
    ]


# --- Planning / Deployment Plan & Stamp Record choices ---


class DeploymentPlanStatusChoices(ChoiceSet):
    key = 'PlantGraph.deployment_plan_status'

    CHOICES = [
        ('draft', 'Draft'),
        ('review', 'Review'),
        ('approved', 'Approved'),
        ('stamping', 'Stamping'),
        ('active', 'Active'),
        ('completed', 'Completed'),
        ('rolled_back', 'Rolled Back'),
    ]


class StampRecordStatusChoices(ChoiceSet):
    key = 'PlantGraph.stamp_record_status'

    CHOICES = [
        ('pending', 'Pending'),
        ('stamped', 'Stamped'),
        ('validated', 'Validated'),
        ('failed', 'Failed'),
        ('rolled_back', 'Rolled Back'),
    ]


# --- Planning / Rack Population choices ---


class RackFaceChoices(ChoiceSet):
    key = 'PlantGraph.rack_face'

    CHOICES = [
        ('front', 'Front'),
        ('rear', 'Rear'),
    ]
