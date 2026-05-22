from django.urls import path

from . import views
from .v2_registry import V2_OBJECT_SPECS


app_name = 'netbox_plant_graph'


def build_v2_object_urlpatterns(spec):
    path_prefix = spec.path_prefix
    route_slug = spec.route_slug
    model_slug = spec.model._meta.model_name
    list_view = getattr(views, spec.list_view_name)
    detail_view = getattr(views, spec.detail_view_name)
    edit_view = getattr(views, spec.edit_view_name)
    delete_view = getattr(views, spec.delete_view_name)
    changelog_view = getattr(views, spec.changelog_view_name)
    journal_view = getattr(views, spec.journal_view_name)

    names = (route_slug,) if route_slug == model_slug else (route_slug, model_slug)
    urlpatterns = []
    for name in names:
        urlpatterns.extend([
            path(f'{path_prefix}/', list_view.as_view(), name=f'{name}_list'),
            path(f'{path_prefix}/add/', edit_view.as_view(), name=f'{name}_add'),
            path(f'{path_prefix}/<int:pk>/', detail_view.as_view(), name=name),
            path(f'{path_prefix}/<int:pk>/edit/', edit_view.as_view(), name=f'{name}_edit'),
            path(f'{path_prefix}/<int:pk>/delete/', delete_view.as_view(), name=f'{name}_delete'),
            path(f'{path_prefix}/<int:pk>/changelog/', changelog_view.as_view(), name=f'{name}_changelog'),
            path(f'{path_prefix}/<int:pk>/journal/', journal_view.as_view(), name=f'{name}_journal'),
        ])
    return urlpatterns


urlpatterns = [
    path('', views.HomeView.as_view(), name='home'),
    path('seed-v2-proof/', views.SeedV2ProofView.as_view(), name='seed_v2_proof'),
    path('graph-overview/', views.GraphOverviewView.as_view(), name='graph_overview'),
    path('fabric/onboard/', views.FabricOnboardView.as_view(), name='fabric_onboard'),
    path('fabrics/<int:pk>/operations/', views.FabricOperationsView.as_view(), name='fabric_operations'),
    path('fabrics/<int:pk>/assign-planes/', views.FabricPlaneAssignmentView.as_view(), name='fabric_assign_planes'),
    path('health/', views.HealthView.as_view(), name='health'),
    path('audit-dashboard/', views.AuditDashboardView.as_view(), name='audit_dashboard'),
    path('audit-triage/', views.AuditTriageView.as_view(), name='audit_triage'),
    path('policy-review/', views.PolicyReviewView.as_view(), name='policy_review'),
    path('plane-audit/', views.PlaneAuditView.as_view(), name='plane_audit'),
    path(
        'audit-findings/<int:pk>/acknowledge/',
        views.AuditFindingAcknowledgeView.as_view(),
        name='audit_finding_acknowledge',
    ),
    path(
        'audit-findings/<int:pk>/start-remediation/',
        views.AuditFindingStartRemediationView.as_view(),
        name='audit_finding_start_remediation',
    ),
    path(
        'audit-findings/<int:pk>/suppress/',
        views.AuditFindingSuppressView.as_view(),
        name='audit_finding_suppress',
    ),
    path(
        'audit-findings/<int:pk>/unsuppress/',
        views.AuditFindingUnsuppressView.as_view(),
        name='audit_finding_unsuppress',
    ),
    path(
        'audit-findings/<int:pk>/resolve/',
        views.AuditFindingResolveView.as_view(),
        name='audit_finding_resolve',
    ),
    path(
        'audit-findings/<int:pk>/reopen/',
        views.AuditFindingReopenView.as_view(),
        name='audit_finding_reopen',
    ),
    path(
        'disjointness-exceptions/request/',
        views.DisjointnessExceptionRequestView.as_view(),
        name='disjointness_exception_request',
    ),
    path(
        'disjointness-exceptions/<int:pk>/approve/',
        views.DisjointnessExceptionApproveView.as_view(),
        name='disjointness_exception_approve',
    ),
    path(
        'disjointness-exceptions/<int:pk>/expire/',
        views.DisjointnessExceptionExpireView.as_view(),
        name='disjointness_exception_expire',
    ),
    path(
        'disjointness-exceptions/<int:pk>/reactivate/',
        views.DisjointnessExceptionReactivateView.as_view(),
        name='disjointness_exception_reactivate',
    ),
    path('template-library/', views.TemplateLibraryView.as_view(), name='template_library'),
    path(
        'assembly-templates/<int:pk>/stamp/',
        views.AssemblyStampWizardView.as_view(),
        name='assembly_stamp_wizard',
    ),
    path(
        'assembly-templates/<int:pk>/stamp-graph/',
        views.AssemblyGraphStampWizardView.as_view(),
        name='assembly_graph_stamp_wizard',
    ),
    path(
        'assembly-templates/<int:pk>/build/',
        views.AssemblyTemplateBuildView.as_view(),
        name='assembly_template_build',
    ),
    path(
        'device-breakout-templates/<int:pk>/stamp/',
        views.BreakoutStampWizardView.as_view(),
        name='breakout_stamp_wizard',
    ),
    path(
        'spatial-templates/<int:pk>/stamp/',
        views.SpatialStampWizardView.as_view(),
        name='spatial_stamp_wizard',
    ),
    path(
        'spatial-templates/<int:pk>/compose/',
        views.SpatialTemplateComposeView.as_view(),
        name='spatial_template_compose',
    ),
    path(
        'spatial-templates/<int:pk>/connections/',
        views.ConnectionTemplateBuilderView.as_view(),
        name='connection_template_builder',
    ),
    path(
        'rack-population-templates/<int:pk>/stamp/',
        views.RackPopulationStampWizardView.as_view(),
        name='rack_population_stamp_wizard',
    ),
    path(
        'deployment-plans/<int:pk>/workflow/',
        views.DeploymentPlanWorkflowView.as_view(),
        name='deployment_plan_workflow',
    ),
    path(
        'deployment-plans/<int:pk>/execute/',
        views.DeploymentPlanExecuteView.as_view(),
        name='deployment_plan_execute',
    ),
    path(
        'deployment-plans/<int:pk>/rollback/',
        views.DeploymentPlanRollbackView.as_view(),
        name='deployment_plan_rollback',
    ),
    path('architectures/', views.FabricArchitectureListView.as_view(), name='architecture_list'),
    path('stamp-templates/<int:pk>/execute/', views.StampTemplateExecuteView.as_view(), name='stamptemplate_execute'),
    path('path-resolver/', views.PathResolverView.as_view(), name='path_resolver'),
    path('path-query/', views.PathQueryView.as_view(), name='path_query'),
    path('interface-fanout-trace/', views.InterfaceFanoutTraceView.as_view(), name='interface_fanout_trace'),
    path('lane-drilldown/', views.LaneDrilldownView.as_view(), name='lane_drilldown'),
    path('lane-compare/', views.LaneCompareView.as_view(), name='lane_compare'),
    path('blast-radius/', views.BlastRadiusView.as_view(), name='blast_radius'),
    path('lane-workspace/', views.LaneWorkspaceView.as_view(), name='lane_workspace'),
    path('policy-dashboard/', views.PolicyDashboardView.as_view(), name='policy_dashboard'),
    path('operations/', views.OperationsCenterView.as_view(), name='operations_center'),
    path('imports/preview/', views.ImportPreviewView.as_view(), name='import_preview'),
    path('impact-reports/', views.ImpactReportsView.as_view(), name='impact_reports'),
    path('impact-reports/<int:pk>/export.json', views.ImpactReportExportView.as_view(), name='impact_report_export'),
    path('graph-build-runs/', views.StampRunListView.as_view(), name='graph-build-run_list'),
    path('audit-runs/', views.OperationRunListView.as_view(), name='audit-run_list'),
    path('coordinate-layout/', views.CoordinateLayoutView.as_view(), name='coordinate_layout'),
    path('suppression-rules/<int:pk>/revoke/', views.SuppressionRuleRevokeView.as_view(), name='suppressionrule_revoke'),
]

for object_spec in V2_OBJECT_SPECS:
    urlpatterns.extend(build_v2_object_urlpatterns(object_spec))
