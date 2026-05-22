from django.urls import path
from netbox.api.routers import NetBoxRouter

from netbox_plant_graph.api import views
from netbox_plant_graph.v2_registry import V2_OBJECT_SPECS


app_name = 'netbox_plant_graph'

router = NetBoxRouter()
router.APIRootView = views.RootView

for spec in V2_OBJECT_SPECS:
    router.register(spec.api_basename, getattr(views, spec.viewset_name))

urlpatterns = router.urls + [
    path('path-query/', views.PathQueryAPIView.as_view(), name='path-query'),
    path(
        'impact/cable-assembly-cut/',
        views.CableAssemblyCutImpactAPIView.as_view(),
        name='impact-cable-assembly-cut',
    ),
    path(
        'impact/mpo-connector-unplug/',
        views.MPOConnectorUnplugImpactAPIView.as_view(),
        name='impact-mpo-connector-unplug',
    ),
    path(
        'impact/osfp-transceiver-unseat/',
        views.OSFPTransceiverUnseatImpactAPIView.as_view(),
        name='impact-osfp-transceiver-unseat',
    ),
    path('suppression-summary/', views.SuppressionSummaryAPIView.as_view(), name='suppression-summary'),
    path('audit-timeline/', views.AuditTimelineAPIView.as_view(), name='audit-timeline'),
    path('workflow/summary/', views.WorkflowSummaryAPIView.as_view(), name='workflow-summary'),
    path('workflow/findings/', views.WorkflowFindingsAPIView.as_view(), name='workflow-findings'),
    path('workflow/findings/<int:pk>/', views.WorkflowFindingDetailAPIView.as_view(), name='workflow-finding-detail'),
    path(
        'workflow/findings/<int:pk>/acknowledge/',
        views.WorkflowFindingAcknowledgeAPIView.as_view(),
        name='workflow-finding-acknowledge',
    ),
    path(
        'workflow/findings/<int:pk>/start-remediation/',
        views.WorkflowFindingStartRemediationAPIView.as_view(),
        name='workflow-finding-start-remediation',
    ),
    path(
        'workflow/findings/<int:pk>/suppress/',
        views.WorkflowFindingSuppressAPIView.as_view(),
        name='workflow-finding-suppress',
    ),
    path(
        'workflow/findings/<int:pk>/unsuppress/',
        views.WorkflowFindingUnsuppressAPIView.as_view(),
        name='workflow-finding-unsuppress',
    ),
    path(
        'workflow/findings/<int:pk>/resolve/',
        views.WorkflowFindingResolveAPIView.as_view(),
        name='workflow-finding-resolve',
    ),
    path(
        'workflow/findings/<int:pk>/reopen/',
        views.WorkflowFindingReopenAPIView.as_view(),
        name='workflow-finding-reopen',
    ),
    path('workflow/runs/', views.WorkflowRunsAPIView.as_view(), name='workflow-runs'),
    path(
        'disjointness-exceptions/request/',
        views.DisjointnessExceptionRequestAPIView.as_view(),
        name='disjointness-exception-request',
    ),
    path(
        'disjointness-exceptions/<int:pk>/approve/',
        views.DisjointnessExceptionApproveAPIView.as_view(),
        name='disjointness-exception-approve',
    ),
    path(
        'disjointness-exceptions/<int:pk>/expire/',
        views.DisjointnessExceptionExpireAPIView.as_view(),
        name='disjointness-exception-expire',
    ),
    path(
        'disjointness-exceptions/<int:pk>/reactivate/',
        views.DisjointnessExceptionReactivateAPIView.as_view(),
        name='disjointness-exception-reactivate',
    ),
    path(
        'stamp-templates/<int:pk>/execute/',
        views.StampTemplateExecuteAPIView.as_view(),
        name='stamp-template-execute',
    ),
    path(
        'stamp-runs/<int:pk>/rollback/',
        views.StampRunRollbackAPIView.as_view(),
        name='stamp-run-rollback',
    ),
    path('stamps/preview/', views.StampPreviewAPIView.as_view(), name='stamp-preview'),
    path('stamps/preview/', views.StampPreviewAPIView.as_view(), name='stamps-preview'),
    path('operation-runs/', views.OperationRunSummaryAPIView.as_view(), name='operation-runs'),
]
