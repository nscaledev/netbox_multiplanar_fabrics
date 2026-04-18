from django.urls import path

from .object_registry import VIEW_OBJECT_SPECS
from .views import BlastRadiusView, GraphOverviewView, LaneDrilldownView, PathResolverView, PlaneAuditView
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
    path('path-resolver/', PathResolverView.as_view(), name='path_resolver'),
    path('plane-audit/', PlaneAuditView.as_view(), name='plane_audit'),
    path('lane-drilldown/', LaneDrilldownView.as_view(), name='lane_drilldown'),
    path('blast-radius/', BlastRadiusView.as_view(), name='blast_radius'),
]

for spec in VIEW_OBJECT_SPECS:
    urlpatterns.extend(build_object_urlpatterns(spec))
