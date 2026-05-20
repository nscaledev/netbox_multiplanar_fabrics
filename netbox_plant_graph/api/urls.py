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
]
