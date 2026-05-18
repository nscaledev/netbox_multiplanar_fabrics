from importlib import import_module

_EXPORTS = {
    'build_audit_run_timeline': ('netbox_plant_graph.services.graph.audit_reporting', 'build_audit_run_timeline'),
    'build_audit_workflow_summary': ('netbox_plant_graph.services.graph.audit_reporting', 'build_audit_workflow_summary'),
    'build_durable_audit_finding_detail': ('netbox_plant_graph.services.graph.audit_reporting', 'build_durable_audit_finding_detail'),
    'build_durable_audit_finding_search': ('netbox_plant_graph.services.graph.audit_reporting', 'build_durable_audit_finding_search'),
    'build_lane_allocation_summary': ('netbox_plant_graph.services.graph.lane_allocation', 'build_lane_allocation_summary'),
    'build_lane_drilldown': ('netbox_plant_graph.services.graph.lane_drilldown', 'build_lane_drilldown'),
    'build_typed_lane_drilldown': ('netbox_plant_graph.services.graph.lane_drilldown', 'build_typed_lane_drilldown'),
    'build_lane_workspace': ('netbox_plant_graph.services.graph.lane_workspace', 'build_lane_workspace'),
    'normalize_lane_workspace_query': ('netbox_plant_graph.services.graph.lane_workspace', 'normalize_lane_workspace_query'),
    'build_lane_set': ('netbox_plant_graph.services.graph.lane_sets', 'build_lane_set'),
    'build_lane_set_for_attachment_unit': ('netbox_plant_graph.services.graph.lane_sets', 'build_lane_set_for_attachment_unit'),
    'build_lane_set_for_coarse_edge': ('netbox_plant_graph.services.graph.lane_sets', 'build_lane_set_for_coarse_edge'),
    'build_lane_set_for_parent_interface': ('netbox_plant_graph.services.graph.lane_sets', 'build_lane_set_for_parent_interface'),
    'build_lane_set_for_plane': ('netbox_plant_graph.services.graph.lane_sets', 'build_lane_set_for_plane'),
    'build_lane_set_for_plant_node': ('netbox_plant_graph.services.graph.lane_sets', 'build_lane_set_for_plant_node'),
    'build_audit_finding_detail': ('netbox_plant_graph.services.graph.finding_details', 'build_audit_finding_detail'),
    'build_audit_finding_details': ('netbox_plant_graph.services.graph.finding_details', 'build_audit_finding_details'),
    'acknowledge_audit_finding': ('netbox_plant_graph.services.graph.finding_state', 'acknowledge_audit_finding'),
    'record_audit_finding_event': ('netbox_plant_graph.services.graph.finding_state', 'record_audit_finding_event'),
    'reopen_audit_finding': ('netbox_plant_graph.services.graph.finding_state', 'reopen_audit_finding'),
    'resolve_audit_finding': ('netbox_plant_graph.services.graph.finding_state', 'resolve_audit_finding'),
    'start_audit_finding_remediation': ('netbox_plant_graph.services.graph.finding_state', 'start_audit_finding_remediation'),
    'compare_lane_allocations': ('netbox_plant_graph.services.graph.compare', 'compare_lane_allocations'),
    'build_contamination_domains': ('netbox_plant_graph.services.graph.contamination', 'build_contamination_domains'),
    'approve_disjointness_exception': ('netbox_plant_graph.services.graph.policy_exceptions', 'approve_disjointness_exception'),
    'build_disjointness_exception_review': ('netbox_plant_graph.services.graph.policy_exceptions', 'build_disjointness_exception_review'),
    'expire_disjointness_exception': ('netbox_plant_graph.services.graph.policy_exceptions', 'expire_disjointness_exception'),
    'list_active_disjointness_exceptions': ('netbox_plant_graph.services.graph.policy_exceptions', 'list_active_disjointness_exceptions'),
    'reactivate_disjointness_exception': ('netbox_plant_graph.services.graph.policy_exceptions', 'reactivate_disjointness_exception'),
    'build_policy_evaluation': ('netbox_plant_graph.services.graph.policy_evaluator', 'build_policy_evaluation'),
    'build_policy_dashboard': ('netbox_plant_graph.services.graph.policy_reporting', 'build_policy_dashboard'),
    'build_policy_summary': ('netbox_plant_graph.services.graph.policy_reporting', 'build_policy_summary'),
    'list_contamination_domains': ('netbox_plant_graph.services.graph.policy_reporting', 'list_contamination_domains'),
    'describe_signal_resolution_error': ('netbox_plant_graph.services.graph.resolver', 'describe_signal_resolution_error'),
    'resolve_path': ('netbox_plant_graph.services.graph.resolver', 'resolve_path'),
    'resolve_typed_lane_path': ('netbox_plant_graph.services.graph.resolver', 'resolve_typed_lane_path'),
    'compute_blast_radius': ('netbox_plant_graph.services.graph.blast_radius', 'compute_blast_radius'),
    'run_plane_audit': ('netbox_plant_graph.services.graph.audits', 'run_plane_audit'),
    'run_persistent_plane_audit': ('netbox_plant_graph.services.graph.persistent_audits', 'run_persistent_plane_audit'),
    'apply_audit_retention': ('netbox_plant_graph.services.graph.retention', 'apply_audit_retention'),
    'expire_audit_suppressions': ('netbox_plant_graph.services.graph.suppressions', 'expire_audit_suppressions'),
    'suppress_audit_finding': ('netbox_plant_graph.services.graph.suppressions', 'suppress_audit_finding'),
    'unsuppress_audit_finding': ('netbox_plant_graph.services.graph.suppressions', 'unsuppress_audit_finding'),
    'compute_fabric_health': ('netbox_plant_graph.services.graph.health', 'compute_fabric_health'),
    'compute_typed_fabric_health': ('netbox_plant_graph.services.graph.health', 'compute_typed_fabric_health'),
    'build_unresolved_state_dashboard': ('netbox_plant_graph.services.graph.unresolved_reporting', 'build_unresolved_state_dashboard'),
    'build_unresolved_state_overview': ('netbox_plant_graph.services.graph.unresolved_reporting', 'build_unresolved_state_overview'),
    'list_related_unresolved_summaries': ('netbox_plant_graph.services.graph.unresolved_reporting', 'list_related_unresolved_summaries'),
    'stamp_cable_assembly': ('netbox_plant_graph.services.assembly_stamp', 'stamp_cable_assembly'),
    'stamp_passive_device': ('netbox_plant_graph.services.assembly_stamp', 'stamp_passive_device'),
    'execute_plan': ('netbox_plant_graph.services.plan_execution', 'execute_plan'),
    'rollback_plan': ('netbox_plant_graph.services.plan_execution', 'rollback_plan'),
    'stamp_rack_population': ('netbox_plant_graph.services.rack_population_stamp', 'stamp_rack_population'),
    'stamp_spatial_template': ('netbox_plant_graph.services.spatial_stamp', 'stamp_spatial_template'),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    module_path, attr_name = _EXPORTS[name]
    module = import_module(module_path)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
