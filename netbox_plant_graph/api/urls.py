from netbox.api.routers import NetBoxRouter

from django.urls import path

from netbox_plant_graph.api.views import (
    AuditWorkflowFindingDetailAPIView,
    AuditWorkflowFindingSearchAPIView,
    AuditWorkflowRunTimelineAPIView,
    AuditWorkflowSummaryAPIView,
    RootView,
    SpatialPlacementReconcileAPIView,
    StampPreviewAPIView,
    VIEWSET_CLASS_MAP,
)
from netbox_plant_graph.object_registry import API_OBJECT_SPECS

app_name = 'netbox_plant_graph'

router = NetBoxRouter()
router.APIRootView = RootView
for spec in API_OBJECT_SPECS:
    router.register(spec.api.basename, VIEWSET_CLASS_MAP[spec.registry_key], basename=spec.api.basename)

urlpatterns = router.urls
urlpatterns += [
    path('workflow/summary/', AuditWorkflowSummaryAPIView.as_view(), name='workflow-summary'),
    path('workflow/findings/', AuditWorkflowFindingSearchAPIView.as_view(), name='workflow-findings'),
    path('workflow/findings/<int:pk>/', AuditWorkflowFindingDetailAPIView.as_view(), name='workflow-finding-detail'),
    path('workflow/runs/', AuditWorkflowRunTimelineAPIView.as_view(), name='workflow-runs'),
    path('floorplan/reconcile/', SpatialPlacementReconcileAPIView.as_view(), name='spatial-placement-reconcile'),
    path('stamps/preview/', StampPreviewAPIView.as_view(), name='stamp-preview'),
]
