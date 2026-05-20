from django.urls import path

from .views import (
    FabricArchitectureListView,
    FabricArchitectureView,
    FabricArchitectureEditView,
    FabricEditView,
    FabricListView,
    FabricView,
    PathQueryView,
)


app_name = 'netbox_plant_graph'

urlpatterns = [
    path('architectures/', FabricArchitectureListView.as_view(), name='architecture_list'),
    path('architectures/add/', FabricArchitectureEditView.as_view(), name='fabricarchitecture_add'),
    path('architectures/<int:pk>/', FabricArchitectureView.as_view(), name='fabricarchitecture'),
    path('architectures/<int:pk>/edit/', FabricArchitectureEditView.as_view(), name='fabricarchitecture_edit'),
    path('fabrics/', FabricListView.as_view(), name='fabric_list'),
    path('fabrics/add/', FabricEditView.as_view(), name='fabric_add'),
    path('fabrics/<int:pk>/', FabricView.as_view(), name='fabric'),
    path('fabrics/<int:pk>/edit/', FabricEditView.as_view(), name='fabric_edit'),
    path('path-query/', PathQueryView.as_view(), name='path_query'),
]
