from utilities.choices import ChoiceSet


class ArchitectureStatusChoices(ChoiceSet):
    key = 'PlantGraphV2.architecture_status'

    CHOICES = [
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('retired', 'Retired'),
    ]


class FabricClassChoices(ChoiceSet):
    key = 'PlantGraphV2.fabric_class'

    CHOICES = [
        ('roce_backend', 'RoCE Backend'),
        ('ethernet_frontend', 'Ethernet Frontend'),
        ('management', 'Management'),
        ('storage', 'Storage'),
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
        ('shuffle_1x4', '1x4 Shuffle'),
        ('shuffle_2x2_mpo24', '2x2 MPO-24 Shuffle'),
        ('shuffle_4x4', '4x4 Shuffle'),
        ('direct_attach', 'Direct Attach'),
        ('polarity_type_b', 'Polarity Type B'),
        ('polarity_type_c', 'Polarity Type C'),
        ('shuffle_nxm', 'NxM Shuffle'),
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
        ('topology_integrity', 'Topology Integrity Audit'),
        ('operational_impact', 'Operational Impact Analysis'),
        ('import_reconciliation', 'Import Reconciliation'),
    ]


class OperationRunStatusChoices(ChoiceSet):
    key = 'PlantGraphV2.operation_run_status'

    CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]


class OnboardingWorkspaceStatusChoices(ChoiceSet):
    key = 'PlantGraphV2.onboarding_workspace_status'

    CHOICES = [
        ('draft', 'Draft'),
        ('collecting_sources', 'Collecting Sources'),
        ('normalizing', 'Normalizing'),
        ('planning', 'Planning'),
        ('blocked', 'Blocked'),
        ('awaiting_approval', 'Awaiting Approval'),
        ('approved', 'Approved'),
        ('applying', 'Applying'),
        ('applied', 'Applied'),
        ('readiness_failed', 'Readiness Failed'),
        ('published', 'Published'),
        ('archived', 'Archived'),
        ('cancelled', 'Cancelled'),
    ]


class OnboardingSourceArtifactTypeChoices(ChoiceSet):
    key = 'PlantGraphV2.onboarding_source_artifact_type'

    CHOICES = [
        ('blueprint_bundle', 'Blueprint Bundle'),
        ('spreadsheet', 'Spreadsheet'),
        ('diagram', 'Diagram'),
        ('cable_schedule', 'Cable Schedule'),
        ('rack_plan', 'Rack Plan'),
        ('bom', 'Bill of Materials'),
        ('manual_entry', 'Manual Entry'),
        ('api_payload', 'API Payload'),
        ('note', 'Note'),
        ('other', 'Other'),
    ]


class OnboardingSourceArtifactStatusChoices(ChoiceSet):
    key = 'PlantGraphV2.onboarding_source_artifact_status'

    CHOICES = [
        ('received', 'Received'),
        ('normalized', 'Normalized'),
        ('failed', 'Failed'),
        ('superseded', 'Superseded'),
        ('ignored', 'Ignored'),
    ]


class OnboardingDesignItemStatusChoices(ChoiceSet):
    key = 'PlantGraphV2.onboarding_design_item_status'

    CHOICES = [
        ('pending', 'Pending'),
        ('valid', 'Valid'),
        ('warning', 'Warning'),
        ('conflict', 'Conflict'),
        ('ignored', 'Ignored'),
    ]


class OnboardingPrerequisiteResolutionModeChoices(ChoiceSet):
    key = 'PlantGraphV2.onboarding_prerequisite_resolution_mode'

    CHOICES = [
        ('unresolved', 'Unresolved'),
        ('bind', 'Bind Existing'),
        ('create', 'Create'),
        ('defer', 'Defer'),
        ('not_required', 'Not Required'),
    ]


class OnboardingPrerequisiteStatusChoices(ChoiceSet):
    key = 'PlantGraphV2.onboarding_prerequisite_status'

    CHOICES = [
        ('open', 'Open'),
        ('resolved', 'Resolved'),
        ('deferred', 'Deferred'),
        ('blocked', 'Blocked'),
    ]


class OnboardingPlanStatusChoices(ChoiceSet):
    key = 'PlantGraphV2.onboarding_plan_status'

    CHOICES = [
        ('draft', 'Draft'),
        ('generated', 'Generated'),
        ('blocked', 'Blocked'),
        ('awaiting_approval', 'Awaiting Approval'),
        ('approved', 'Approved'),
        ('applying', 'Applying'),
        ('applied', 'Applied'),
        ('failed', 'Failed'),
        ('superseded', 'Superseded'),
        ('cancelled', 'Cancelled'),
    ]


class OnboardingStageStatusChoices(ChoiceSet):
    key = 'PlantGraphV2.onboarding_stage_status'

    CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('skipped', 'Skipped'),
        ('rolled_back', 'Rolled Back'),
    ]


class ArchitectureWorkspaceStatusChoices(ChoiceSet):
    key = 'PlantGraphV2.architecture_workspace_status'

    CHOICES = [
        ('draft', 'Draft'),
        ('collecting_sources', 'Collecting Sources'),
        ('normalizing', 'Normalizing'),
        ('validating', 'Validating'),
        ('blocked', 'Blocked'),
        ('ready', 'Ready'),
        ('awaiting_approval', 'Awaiting Approval'),
        ('approved', 'Approved'),
        ('publishing', 'Publishing'),
        ('published', 'Published'),
        ('archived', 'Archived'),
        ('cancelled', 'Cancelled'),
    ]


class ArchitectureWorkspaceKindChoices(ChoiceSet):
    key = 'PlantGraphV2.architecture_workspace_kind'

    CHOICES = [
        ('new_blueprint', 'New Blueprint'),
        ('new_version', 'New Version'),
        ('revision', 'Revision'),
        ('comparison', 'Comparison'),
    ]


class ArchitectureSourceArtifactTypeChoices(ChoiceSet):
    key = 'PlantGraphV2.architecture_source_artifact_type'

    CHOICES = [
        ('blueprint_bundle', 'Blueprint Bundle'),
        ('schema_json', 'Schema JSON'),
        ('stamp_template', 'Stamp Template'),
        ('diagram', 'Diagram'),
        ('spreadsheet', 'Spreadsheet'),
        ('api_payload', 'API Payload'),
        ('manual_entry', 'Manual Entry'),
        ('note', 'Note'),
        ('other', 'Other'),
    ]


class ArchitectureSourceArtifactStatusChoices(ChoiceSet):
    key = 'PlantGraphV2.architecture_source_artifact_status'

    CHOICES = [
        ('received', 'Received'),
        ('normalized', 'Normalized'),
        ('failed', 'Failed'),
        ('superseded', 'Superseded'),
        ('ignored', 'Ignored'),
    ]


class ArchitectureComponentStatusChoices(ChoiceSet):
    key = 'PlantGraphV2.architecture_component_status'

    CHOICES = [
        ('pending', 'Pending'),
        ('valid', 'Valid'),
        ('warning', 'Warning'),
        ('conflict', 'Conflict'),
        ('ignored', 'Ignored'),
    ]


class ArchitectureValidationStatusChoices(ChoiceSet):
    key = 'PlantGraphV2.architecture_validation_status'

    CHOICES = [
        ('pending', 'Pending'),
        ('passed', 'Passed'),
        ('warning', 'Warning'),
        ('failed', 'Failed'),
    ]


class ArchitecturePublishPlanStatusChoices(ChoiceSet):
    key = 'PlantGraphV2.architecture_publish_plan_status'

    CHOICES = [
        ('generated', 'Generated'),
        ('blocked', 'Blocked'),
        ('approved', 'Approved'),
        ('publishing', 'Publishing'),
        ('published', 'Published'),
        ('failed', 'Failed'),
        ('superseded', 'Superseded'),
        ('cancelled', 'Cancelled'),
    ]
