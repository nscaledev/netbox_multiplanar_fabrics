from importlib import import_module

_EXPORTS = {
    'breadth_first_walk': ('netbox_plant_graph.services.graph.traversal', 'breadth_first_walk'),
    'acknowledge_audit_finding': ('netbox_plant_graph.services.graph.finding_state', 'acknowledge_audit_finding'),
    'apply_audit_retention': ('netbox_plant_graph.services.graph.retention', 'apply_audit_retention'),
    'build_audit_run_timeline': ('netbox_plant_graph.services.graph.audit_reporting', 'build_audit_run_timeline'),
    'build_audit_workflow_summary': ('netbox_plant_graph.services.graph.audit_reporting', 'build_audit_workflow_summary'),
    'build_audit_finding_detail': ('netbox_plant_graph.services.graph.finding_details', 'build_audit_finding_detail'),
    'build_audit_finding_details': ('netbox_plant_graph.services.graph.finding_details', 'build_audit_finding_details'),
    'build_durable_audit_finding_detail': ('netbox_plant_graph.services.graph.audit_reporting', 'build_durable_audit_finding_detail'),
    'build_durable_audit_finding_search': ('netbox_plant_graph.services.graph.audit_reporting', 'build_durable_audit_finding_search'),
    'build_contamination_domains': ('netbox_plant_graph.services.graph.contamination', 'build_contamination_domains'),
    'approve_disjointness_exception': ('netbox_plant_graph.services.graph.policy_exceptions', 'approve_disjointness_exception'),
    'build_disjointness_exception_review': ('netbox_plant_graph.services.graph.policy_exceptions', 'build_disjointness_exception_review'),
    'build_policy_dashboard': ('netbox_plant_graph.services.graph.policy_reporting', 'build_policy_dashboard'),
    'build_policy_summary': ('netbox_plant_graph.services.graph.policy_reporting', 'build_policy_summary'),
    'build_lane_allocation_summary': ('netbox_plant_graph.services.graph.lane_allocation', 'build_lane_allocation_summary'),
    'build_lane_drilldown': ('netbox_plant_graph.services.graph.lane_drilldown', 'build_lane_drilldown'),
    'build_lane_workspace': ('netbox_plant_graph.services.graph.lane_workspace', 'build_lane_workspace'),
    'build_lane_set': ('netbox_plant_graph.services.graph.lane_sets', 'build_lane_set'),
    'build_lane_set_for_attachment_unit': ('netbox_plant_graph.services.graph.lane_sets', 'build_lane_set_for_attachment_unit'),
    'build_lane_set_for_coarse_edge': ('netbox_plant_graph.services.graph.lane_sets', 'build_lane_set_for_coarse_edge'),
    'build_lane_set_for_parent_interface': ('netbox_plant_graph.services.graph.lane_sets', 'build_lane_set_for_parent_interface'),
    'build_lane_set_for_plane': ('netbox_plant_graph.services.graph.lane_sets', 'build_lane_set_for_plane'),
    'build_lane_set_for_plant_node': ('netbox_plant_graph.services.graph.lane_sets', 'build_lane_set_for_plant_node'),
    'build_policy_evaluation': ('netbox_plant_graph.services.graph.policy_evaluator', 'build_policy_evaluation'),
    'list_contamination_domains': ('netbox_plant_graph.services.graph.policy_reporting', 'list_contamination_domains'),
    'compare_lane_allocations': ('netbox_plant_graph.services.graph.compare', 'compare_lane_allocations'),
    'collect_unresolved_candidates': ('netbox_plant_graph.services.graph.unresolved_candidates', 'collect_unresolved_candidates'),
    'build_typed_lane_drilldown': ('netbox_plant_graph.services.graph.lane_drilldown', 'build_typed_lane_drilldown'),
    'compute_blast_radius': ('netbox_plant_graph.services.graph.blast_radius', 'compute_blast_radius'),
    'compute_fabric_health': ('netbox_plant_graph.services.graph.health', 'compute_fabric_health'),
    'compute_typed_fabric_health': ('netbox_plant_graph.services.graph.health', 'compute_typed_fabric_health'),
    'build_unresolved_state_dashboard': ('netbox_plant_graph.services.graph.unresolved_reporting', 'build_unresolved_state_dashboard'),
    'build_unresolved_state_overview': ('netbox_plant_graph.services.graph.unresolved_reporting', 'build_unresolved_state_overview'),
    'expire_disjointness_exception': ('netbox_plant_graph.services.graph.policy_exceptions', 'expire_disjointness_exception'),
    'expire_audit_suppressions': ('netbox_plant_graph.services.graph.suppressions', 'expire_audit_suppressions'),
    'list_active_disjointness_exceptions': ('netbox_plant_graph.services.graph.policy_exceptions', 'list_active_disjointness_exceptions'),
    'normalize_lane_workspace_query': ('netbox_plant_graph.services.graph.lane_workspace', 'normalize_lane_workspace_query'),
    'record_audit_finding_event': ('netbox_plant_graph.services.graph.finding_state', 'record_audit_finding_event'),
    'reactivate_disjointness_exception': ('netbox_plant_graph.services.graph.policy_exceptions', 'reactivate_disjointness_exception'),
    'reopen_audit_finding': ('netbox_plant_graph.services.graph.finding_state', 'reopen_audit_finding'),
    'resolve_path': ('netbox_plant_graph.services.graph.resolver', 'resolve_path'),
    'resolve_audit_finding': ('netbox_plant_graph.services.graph.finding_state', 'resolve_audit_finding'),
    'resolve_typed_lane_path': ('netbox_plant_graph.services.graph.resolver', 'resolve_typed_lane_path'),
    'run_persistent_plane_audit': ('netbox_plant_graph.services.graph.persistent_audits', 'run_persistent_plane_audit'),
    'run_plane_audit': ('netbox_plant_graph.services.graph.audits', 'run_plane_audit'),
    'start_audit_finding_remediation': ('netbox_plant_graph.services.graph.finding_state', 'start_audit_finding_remediation'),
    'suppress_audit_finding': ('netbox_plant_graph.services.graph.suppressions', 'suppress_audit_finding'),
    'list_related_unresolved_summaries': ('netbox_plant_graph.services.graph.unresolved_reporting', 'list_related_unresolved_summaries'),
    'sync_unresolved_state': ('netbox_plant_graph.services.graph.unresolved_summaries', 'sync_unresolved_state'),
    'sync_unresolved_state_for_build': ('netbox_plant_graph.services.graph.unresolved_summaries', 'sync_unresolved_state_for_build'),
    'unresolved_summary_persistence_enabled': ('netbox_plant_graph.services.graph.unresolved_summaries', 'unresolved_summary_persistence_enabled'),
    'unsuppress_audit_finding': ('netbox_plant_graph.services.graph.suppressions', 'unsuppress_audit_finding'),
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
