from django.urls import path

from .object_registry import VIEW_OBJECT_SPECS
from .views import (
    AuditDashboardView,
    AuditFindingAcknowledgeView,
    AuditFindingReopenView,
    AuditFindingResolveView,
    AuditFindingStartRemediationView,
    AuditFindingSuppressView,
    AuditFindingUnsuppressView,
    AuditTriageView,
    BlastRadiusView,
    GraphOverviewView,
    HealthView,
    LaneCompareView,
    LaneDrilldownView,
    LaneWorkspaceView,
    PathResolverView,
    PlaneAuditView,
    PolicyReviewView,
    DisjointnessExceptionApproveView,
    DisjointnessExceptionExpireView,
    DisjointnessExceptionReactivateView,
    DisjointnessExceptionRequestView,
    AssemblyStampWizardView,
    AssemblyTemplateBuilderView,
    BreakoutStampWizardView,
    ConnectionTemplateBuilderView,
    SpatialStampWizardView,
    SpatialTemplateComposerView,
    RackPopulationStampWizardView,
    DeploymentPlanExecuteView,
    DeploymentPlanRollbackView,
    DeploymentPlanWorkflowView,
    CoordinateLayoutView,
    TemplateLibraryView,
    FabricOnboardView,
    FabricOperationsView,
    FabricPlaneAssignmentView,
)
from . import views


app_name = 'netbox_plant_graph'


def build_object_urlpatterns(spec):
    path_prefix = spec.routes.resolved_path_prefix
    route_slug = spec.routes.slug
    list_view = getattr(views, spec.view.list_class_name)
    detail_view = getattr(views, spec.view.detail_class_name)
    urlpatterns = [
        path(f'{path_prefix}/', list_view.as_view(), name=f'{route_slug}_list'),
        path(f'{path_prefix}/<int:pk>/', detail_view.as_view(), name=route_slug),
    ]
    if spec.view.edit_class_name is not None:
        edit_view = getattr(views, spec.view.edit_class_name)
        urlpatterns.extend([
            path(f'{path_prefix}/add/', edit_view.as_view(), name=f'{route_slug}_add'),
            path(f'{path_prefix}/<int:pk>/edit/', edit_view.as_view(), name=f'{route_slug}_edit'),
        ])
    if spec.view.delete_class_name is not None:
        delete_view = getattr(views, spec.view.delete_class_name)
        urlpatterns.append(path(f'{path_prefix}/<int:pk>/delete/', delete_view.as_view(), name=f'{route_slug}_delete'))
    return urlpatterns


urlpatterns = [
    path('graph-overview/', GraphOverviewView.as_view(), name='graph_overview'),
    path('health/', HealthView.as_view(), name='health'),
    path('audit-dashboard/', AuditDashboardView.as_view(), name='audit_dashboard'),
    path('audit-triage/', AuditTriageView.as_view(), name='audit_triage'),
    path('policy-review/', PolicyReviewView.as_view(), name='policy_review'),
    path('path-resolver/', PathResolverView.as_view(), name='path_resolver'),
    path('plane-audit/', PlaneAuditView.as_view(), name='plane_audit'),
    path('lane-drilldown/', LaneDrilldownView.as_view(), name='lane_drilldown'),
    path('lane-workspace/', LaneWorkspaceView.as_view(), name='lane_workspace'),
    path('lane-compare/', LaneCompareView.as_view(), name='lane_compare'),
    path('blast-radius/', BlastRadiusView.as_view(), name='blast_radius'),
    path('audit-findings/<int:pk>/acknowledge/', AuditFindingAcknowledgeView.as_view(), name='audit_finding_acknowledge'),
    path('audit-findings/<int:pk>/start-remediation/', AuditFindingStartRemediationView.as_view(), name='audit_finding_start_remediation'),
    path('audit-findings/<int:pk>/suppress/', AuditFindingSuppressView.as_view(), name='audit_finding_suppress'),
    path('audit-findings/<int:pk>/unsuppress/', AuditFindingUnsuppressView.as_view(), name='audit_finding_unsuppress'),
    path('audit-findings/<int:pk>/resolve/', AuditFindingResolveView.as_view(), name='audit_finding_resolve'),
    path('audit-findings/<int:pk>/reopen/', AuditFindingReopenView.as_view(), name='audit_finding_reopen'),
    path('disjointness-exceptions/<int:pk>/approve/', DisjointnessExceptionApproveView.as_view(), name='disjointness_exception_approve'),
    path('disjointness-exceptions/<int:pk>/expire/', DisjointnessExceptionExpireView.as_view(), name='disjointness_exception_expire'),
    path('disjointness-exceptions/<int:pk>/reactivate/', DisjointnessExceptionReactivateView.as_view(), name='disjointness_exception_reactivate'),
    path('disjointness-exceptions/request/', DisjointnessExceptionRequestView.as_view(), name='disjointness_exception_request'),
    # Planning wizard and action URLs
    path('assembly-templates/<int:pk>/stamp/', AssemblyStampWizardView.as_view(), name='assembly_stamp_wizard'),
    path('assembly-templates/<int:pk>/build/', AssemblyTemplateBuilderView.as_view(), name='assembly_template_build'),
    path('device-breakout-templates/<int:pk>/stamp/', BreakoutStampWizardView.as_view(), name='breakout_stamp_wizard'),
    path('spatial-templates/<int:pk>/stamp/', SpatialStampWizardView.as_view(), name='spatial_stamp_wizard'),
    path('spatial-templates/<int:pk>/compose/', SpatialTemplateComposerView.as_view(), name='spatial_template_compose'),
    path('spatial-templates/<int:pk>/connections/', ConnectionTemplateBuilderView.as_view(), name='connection_template_builder'),
    path('rack-population-templates/<int:pk>/stamp/', RackPopulationStampWizardView.as_view(), name='rack_population_stamp_wizard'),
    path('deployment-plans/<int:pk>/execute/', DeploymentPlanExecuteView.as_view(), name='deployment_plan_execute'),
    path('deployment-plans/<int:pk>/rollback/', DeploymentPlanRollbackView.as_view(), name='deployment_plan_rollback'),
    path('deployment-plans/<int:pk>/workflow/', DeploymentPlanWorkflowView.as_view(), name='deployment_plan_workflow'),
    path('coordinate-layout/', CoordinateLayoutView.as_view(), name='coordinate_layout'),
    path('template-library/', TemplateLibraryView.as_view(), name='template_library'),
    # Workflow UX pages
    path('fabric/onboard/', FabricOnboardView.as_view(), name='fabric_onboard'),
    path('fabrics/<int:pk>/operations/', FabricOperationsView.as_view(), name='fabric_operations'),
    path('fabrics/<int:pk>/assign-planes/', FabricPlaneAssignmentView.as_view(), name='fabric_assign_planes'),
]

for spec in VIEW_OBJECT_SPECS:
    urlpatterns.extend(build_object_urlpatterns(spec))
