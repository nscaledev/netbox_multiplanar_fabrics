from utilities.choices import ChoiceSet


class ArchitectureStatusChoices(ChoiceSet):
    key = 'PlantGraphV2.architecture_status'

    CHOICES = [
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('retired', 'Retired'),
    ]


class FabricStatusChoices(ChoiceSet):
    key = 'PlantGraphV2.fabric_status'

    CHOICES = [
        ('draft', 'Draft'),
        ('planned', 'Planned'),
        ('active', 'Active'),
        ('retired', 'Retired'),
    ]


class NodeKindChoices(ChoiceSet):
    key = 'PlantGraphV2.node_kind'

    CHOICES = [
        ('active_device', 'Active Device'),
        ('passive_assembly', 'Passive Assembly'),
        ('logical_container', 'Logical Container'),
    ]


class EndpointKindChoices(ChoiceSet):
    key = 'PlantGraphV2.endpoint_kind'

    CHOICES = [
        ('netbox_port', 'NetBox Port Anchor'),
        ('plugin_port', 'Plugin Port'),
        ('connector', 'Connector'),
        ('subconnector', 'Sub-Connector'),
    ]


class ConnectorKindChoices(ChoiceSet):
    key = 'PlantGraphV2.connector_kind'

    CHOICES = [
        ('osfp', 'OSFP'),
        ('qsfp-dd', 'QSFP-DD'),
        ('mpo-8', 'MPO-8'),
        ('mpo-12', 'MPO-12'),
        ('mpo-16', 'MPO-16'),
        ('mpo-24', 'MPO-24'),
        ('lc', 'LC'),
        ('virtual', 'Virtual'),
        ('other', 'Other'),
    ]


class SegmentKindChoices(ChoiceSet):
    key = 'PlantGraphV2.segment_kind'

    CHOICES = [
        ('jumper', 'Jumper'),
        ('trunk', 'Trunk'),
        ('internal', 'Internal'),
        ('external_plant', 'External Plant'),
    ]


class LaneDirectionChoices(ChoiceSet):
    key = 'PlantGraphV2.lane_direction'

    CHOICES = [
        ('send', 'Send'),
        ('receive', 'Receive'),
    ]


class TransferMapKindChoices(ChoiceSet):
    key = 'PlantGraphV2.transfer_map_kind'

    CHOICES = [
        ('identity', 'Identity'),
        ('polarity_swap', 'Polarity Swap'),
        ('shuffle_2x2', '2x2 Shuffle'),
        ('stagger', 'Stagger'),
        ('breakout', 'Breakout'),
        ('custom', 'Custom'),
    ]


class StampRunStatusChoices(ChoiceSet):
    key = 'PlantGraphV2.stamp_run_status'

    CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]


class SuppressionStatusChoices(ChoiceSet):
    key = 'PlantGraphV2.suppression_status'

    CHOICES = [
        ('pending', 'Pending'),
        ('active', 'Active'),
        ('revoked', 'Revoked'),
        ('expired', 'Expired'),
    ]


class AuditEventTypeChoices(ChoiceSet):
    key = 'PlantGraphV2.audit_event_type'

    CHOICES = [
        ('stamp', 'Stamp'),
        ('path_resolve', 'Path Resolve'),
        ('suppression_change', 'Suppression Change'),
        ('operation_run', 'Operation Run'),
        ('policy_eval', 'Policy Evaluation'),
    ]


class OperationProfileChoices(ChoiceSet):
    key = 'PlantGraphV2.operation_profile'

    CHOICES = [
        ('generic_roce', 'Generic RoCE'),
        ('madison_default', 'Madison Default'),
    ]


class OperationRunStatusChoices(ChoiceSet):
    key = 'PlantGraphV2.operation_run_status'

    CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
