from django.urls import path

from .views import (
    FabricArchitectureListView,
    FabricArchitectureView,
    FabricArchitectureDeleteView,
    FabricArchitectureEditView,
    FabricArchitectureChangeLogView,
    FabricArchitectureJournalView,
    FabricChangeLogView,
    FabricDeleteView,
    FabricEditView,
    FabricJournalView,
    FabricListView,
    FabricView,
    HomeView,
    PathQueryView,
    SeedV2ProofView,
)


app_name = 'netbox_plant_graph'

urlpatterns = [
    path('', HomeView.as_view(), name='home'),
    path('seed-v2-proof/', SeedV2ProofView.as_view(), name='seed_v2_proof'),
    path('architectures/', FabricArchitectureListView.as_view(), name='architecture_list'),
    path('architectures/', FabricArchitectureListView.as_view(), name='fabricarchitecture_list'),
    path('architectures/add/', FabricArchitectureEditView.as_view(), name='fabricarchitecture_add'),
    path('architectures/<int:pk>/', FabricArchitectureView.as_view(), name='fabricarchitecture'),
    path('architectures/<int:pk>/edit/', FabricArchitectureEditView.as_view(), name='fabricarchitecture_edit'),
    path('architectures/<int:pk>/delete/', FabricArchitectureDeleteView.as_view(), name='fabricarchitecture_delete'),
    path('architectures/<int:pk>/changelog/', FabricArchitectureChangeLogView.as_view(), name='fabricarchitecture_changelog'),
    path('architectures/<int:pk>/journal/', FabricArchitectureJournalView.as_view(), name='fabricarchitecture_journal'),
    path('fabrics/', FabricListView.as_view(), name='fabric_list'),
    path('fabrics/add/', FabricEditView.as_view(), name='fabric_add'),
    path('fabrics/<int:pk>/', FabricView.as_view(), name='fabric'),
    path('fabrics/<int:pk>/edit/', FabricEditView.as_view(), name='fabric_edit'),
    path('fabrics/<int:pk>/delete/', FabricDeleteView.as_view(), name='fabric_delete'),
    path('fabrics/<int:pk>/changelog/', FabricChangeLogView.as_view(), name='fabric_changelog'),
    path('fabrics/<int:pk>/journal/', FabricJournalView.as_view(), name='fabric_journal'),
    path('path-query/', PathQueryView.as_view(), name='path_query'),
]
