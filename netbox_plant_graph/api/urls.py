from netbox.api.routers import NetBoxRouter

from netbox_plant_graph.api.views import RootView, VIEWSET_CLASS_MAP
from netbox_plant_graph.object_registry import API_OBJECT_SPECS

app_name = 'netbox_plant_graph'

router = NetBoxRouter()
router.APIRootView = RootView
for spec in API_OBJECT_SPECS:
    router.register(spec.api.basename, VIEWSET_CLASS_MAP[spec.registry_key], basename=spec.api.basename)

urlpatterns = router.urls
