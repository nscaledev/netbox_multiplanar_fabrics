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
    path('architectures/', views.FabricArchitectureListView.as_view(), name='architecture_list'),
    path('path-query/', views.PathQueryView.as_view(), name='path_query'),
]

for object_spec in V2_OBJECT_SPECS:
    urlpatterns.extend(build_v2_object_urlpatterns(object_spec))
