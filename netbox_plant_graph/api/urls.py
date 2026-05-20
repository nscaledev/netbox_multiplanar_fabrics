from django.urls import path
from netbox.api.routers import NetBoxRouter

from netbox_plant_graph.api.views import (
    FabricArchitectureViewSet,
    FabricViewSet,
    PathQueryAPIView,
    RootView,
)


app_name = 'netbox_plant_graph'

router = NetBoxRouter()
router.APIRootView = RootView
router.register('architectures', FabricArchitectureViewSet)
router.register('fabrics', FabricViewSet)

urlpatterns = router.urls + [
    path('path-query/', PathQueryAPIView.as_view(), name='path-query'),
]
