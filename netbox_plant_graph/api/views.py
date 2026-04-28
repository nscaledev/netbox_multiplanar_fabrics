from rest_framework.views import APIView
from rest_framework.routers import APIRootView
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from netbox.api.viewsets import NetBoxModelViewSet

from netbox_plant_graph import filtersets as filterset_module
from netbox_plant_graph.api.serializers import (
    AssemblyTemplateStampSerializer,
    AuditFindingActionSerializer,
    AuditFindingSuppressActionSerializer,
    AuditWorkflowFindingDetailPayloadSerializer,
    AuditWorkflowFindingRecordPayloadSerializer,
    AuditWorkflowFindingSearchQuerySerializer,
    AuditWorkflowRunPayloadSerializer,
    AuditWorkflowRunTimelineQuerySerializer,
    AuditWorkflowSummaryPayloadSerializer,
    AuditWorkflowSummaryQuerySerializer,
    DeploymentPlanActionSerializer,
    DisjointnessExceptionActionSerializer,
    RackPopulationTemplateStampSerializer,
    SERIALIZER_CLASS_MAP,
    SpatialPlacementReconcilePayloadSerializer,
    SpatialPlacementReconcileSerializer,
    SpatialTemplateStampSerializer,
    StampPreviewSerializer,
)
from netbox_plant_graph.models import AuditFinding, AuditRun, Fabric
from netbox_plant_graph.object_registry import API_OBJECT_SPECS
from netbox_plant_graph.services import (
    acknowledge_audit_finding,
    approve_disjointness_exception,
    build_audit_run_timeline,
    build_audit_workflow_summary,
    build_durable_audit_finding_detail,
    build_durable_audit_finding_search,
    execute_plan,
    expire_disjointness_exception,
    reactivate_disjointness_exception,
    reopen_audit_finding,
    resolve_audit_finding,
    rollback_plan,
    stamp_cable_assembly,
    stamp_passive_device,
    stamp_rack_population,
    stamp_spatial_template,
    start_audit_finding_remediation,
    suppress_audit_finding,
    unsuppress_audit_finding,
)


class RootView(APIRootView):
    def get_view_name(self):
        return 'plant-graph'


class AuditWorkflowSummaryAPIView(APIView):
    queryset = Fabric.objects.all()

    def get(self, request):
        serializer = AuditWorkflowSummaryQuerySerializer(data=request.GET)
        serializer.is_valid(raise_exception=True)
        payload = build_audit_workflow_summary(fabric=serializer.validated_data.get('fabric'))
        return Response(AuditWorkflowSummaryPayloadSerializer(payload).data)


class AuditWorkflowFindingSearchAPIView(APIView):
    queryset = AuditFinding.objects.all()

    def get(self, request):
        serializer = AuditWorkflowFindingSearchQuerySerializer(data=request.GET)
        serializer.is_valid(raise_exception=True)
        payload = build_durable_audit_finding_search(**serializer.validated_data)
        return Response({
            'count': len(payload),
            'results': AuditWorkflowFindingRecordPayloadSerializer(payload, many=True).data,
        })


class AuditWorkflowFindingDetailAPIView(APIView):
    queryset = AuditFinding.objects.all()

    def get(self, request, pk):
        payload = build_durable_audit_finding_detail(finding=pk)
        if payload is None:
            raise NotFound('Audit finding not found.')
        return Response(AuditWorkflowFindingDetailPayloadSerializer(payload).data)


class AuditWorkflowRunTimelineAPIView(APIView):
    queryset = AuditRun.objects.all()

    def get(self, request):
        serializer = AuditWorkflowRunTimelineQuerySerializer(data=request.GET)
        serializer.is_valid(raise_exception=True)
        payload = build_audit_run_timeline(**serializer.validated_data)
        return Response({
            'count': len(payload),
            'results': AuditWorkflowRunPayloadSerializer(payload, many=True).data,
        })


class SpatialPlacementReconcileAPIView(APIView):
    queryset = Fabric.objects.none()

    def post(self, request):
        serializer = SpatialPlacementReconcileSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if not request.user.has_perm('netbox_plant_graph.change_spatialplacement'):
            raise PermissionDenied('This user does not have permission to reconcile spatial placements.')

        from dcim.models import Location, Site
        from netbox_plant_graph.services.floorplan_bridge import reconcile_floorplan_to_placements

        model_class = Site if data['scope_type'] == 'site' else Location
        scope = model_class.objects.filter(pk=data['scope_id']).first()
        if scope is None:
            raise NotFound(f'{model_class.__name__} not found.')

        result = reconcile_floorplan_to_placements(
            scope,
            create_missing=data.get('create_missing', False),
        )

        payload = {
            'scope_type': data['scope_type'],
            'scope_id': scope.pk,
            'floorplan_id': getattr(getattr(result, 'floorplan', None), 'pk', None),
            'updated_placements': int(getattr(result, 'updated_placements', 0) or 0),
            'created_placements': int(getattr(result, 'created_placements', 0) or 0),
            'skipped_unmanaged': int(getattr(result, 'skipped_unmanaged', 0) or 0),
            'skipped_missing_rack': int(getattr(result, 'skipped_missing_rack', 0) or 0),
            'skipped_missing_placement': int(getattr(result, 'skipped_missing_placement', 0) or 0),
            'errors': [str(error) for error in (getattr(result, 'errors', None) or [])],
        }
        return Response(SpatialPlacementReconcilePayloadSerializer(payload).data)


def build_viewset_class(spec):
    namespace = {
        '__module__': __name__,
        'queryset': spec.model.objects.all(),
        'serializer_class': SERIALIZER_CLASS_MAP[spec.registry_key],
        'filterset_class': getattr(filterset_module, spec.filterset.class_name),
    }
    if spec.api.read_only:
        namespace['http_method_names'] = ['get', 'head', 'options']
    return type(
        spec.api.viewset_name,
        (NetBoxModelViewSet,),
        namespace,
    )


VIEWSET_CLASS_MAP = {}
for object_spec in API_OBJECT_SPECS:
    viewset_class = build_viewset_class(object_spec)
    VIEWSET_CLASS_MAP[object_spec.registry_key] = viewset_class
    globals()[object_spec.api.viewset_name] = viewset_class


class AuditFindingViewSet(VIEWSET_CLASS_MAP['auditfinding']):
    http_method_names = ['get', 'head', 'options', 'post']
    workflow_actions = {'acknowledge', 'start_remediation', 'resolve', 'reopen', 'suppress', 'unsuppress'}

    def get_queryset(self):
        queryset = super().get_queryset()

        if getattr(self, 'action', None) in self.workflow_actions and self.request.user.is_authenticated:
            return self.queryset.model.objects.restrict(self.request.user, 'change')

        return queryset

    def _serialize_finding(self, finding):
        payload = SERIALIZER_CLASS_MAP['auditfinding'](
            finding,
            context={'request': self.request},
        ).data
        active_suppression = finding.suppressions.filter(active=True).order_by('-created', '-pk').first()
        payload['active_suppression'] = (
            SERIALIZER_CLASS_MAP['auditsuppression'](
                active_suppression,
                context={'request': self.request},
            ).data
            if active_suppression is not None
            else None
        )
        return payload

    def _check_change_permission(self, finding):
        if not self.request.user.has_perm('netbox_plant_graph.change_auditfinding', finding):
            raise PermissionDenied('This user does not have permission to change this audit finding.')

    def _run_action(self, request, pk, *, serializer_class, handler):
        finding = self.get_object()
        self._check_change_permission(finding)
        input_serializer = serializer_class(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        handler(finding, input_serializer.validated_data)
        finding.refresh_from_db()
        return Response(self._serialize_finding(finding))

    @action(detail=True, methods=['post'])
    def acknowledge(self, request, pk=None):
        return self._run_action(
            request,
            pk,
            serializer_class=AuditFindingActionSerializer,
            handler=lambda finding, data: acknowledge_audit_finding(
                finding=finding,
                actor=request.user,
                note=data.get('note', ''),
            ),
        )

    @action(detail=True, methods=['post'], url_path='start-remediation')
    def start_remediation(self, request, pk=None):
        return self._run_action(
            request,
            pk,
            serializer_class=AuditFindingActionSerializer,
            handler=lambda finding, data: start_audit_finding_remediation(
                finding=finding,
                actor=request.user,
                note=data.get('note', ''),
            ),
        )

    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        return self._run_action(
            request,
            pk,
            serializer_class=AuditFindingActionSerializer,
            handler=lambda finding, data: resolve_audit_finding(
                finding=finding,
                actor=request.user,
                note=data.get('note', ''),
            ),
        )

    @action(detail=True, methods=['post'])
    def reopen(self, request, pk=None):
        return self._run_action(
            request,
            pk,
            serializer_class=AuditFindingActionSerializer,
            handler=lambda finding, data: reopen_audit_finding(
                finding=finding,
                actor=request.user,
                note=data.get('note', ''),
            ),
        )

    @action(detail=True, methods=['post'])
    def suppress(self, request, pk=None):
        return self._run_action(
            request,
            pk,
            serializer_class=AuditFindingSuppressActionSerializer,
            handler=lambda finding, data: suppress_audit_finding(
                finding=finding,
                actor=request.user,
                reason=data.get('note', ''),
                days=data.get('days'),
            ),
        )

    @action(detail=True, methods=['post'])
    def unsuppress(self, request, pk=None):
        return self._run_action(
            request,
            pk,
            serializer_class=AuditFindingActionSerializer,
            handler=lambda finding, data: unsuppress_audit_finding(
                finding=finding,
                actor=request.user,
                reason=data.get('note', ''),
            ),
        )


VIEWSET_CLASS_MAP['auditfinding'] = AuditFindingViewSet
globals()['AuditFindingViewSet'] = AuditFindingViewSet


class DisjointnessExceptionViewSet(VIEWSET_CLASS_MAP['disjointnessexception']):
    http_method_names = ['get', 'head', 'options', 'post']
    workflow_actions = {'approve', 'expire', 'reactivate'}

    def get_queryset(self):
        queryset = super().get_queryset()

        if getattr(self, 'action', None) in self.workflow_actions and self.request.user.is_authenticated:
            return self.queryset.model.objects.restrict(self.request.user, 'change')

        return queryset

    def _serialize_exception(self, exception):
        return SERIALIZER_CLASS_MAP['disjointnessexception'](
            exception,
            context={'request': self.request},
        ).data

    def _check_change_permission(self, exception):
        if not self.request.user.has_perm('netbox_plant_graph.change_disjointnessexception', exception):
            raise PermissionDenied('This user does not have permission to change this disjointness exception.')

    def _run_action(self, request, pk, *, handler):
        exception = self.get_object()
        self._check_change_permission(exception)
        input_serializer = DisjointnessExceptionActionSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        handler(exception)
        exception.refresh_from_db()
        return Response(self._serialize_exception(exception))

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        return self._run_action(
            request,
            pk,
            handler=lambda exception: approve_disjointness_exception(
                exception=exception,
                actor=request.user,
            ),
        )

    @action(detail=True, methods=['post'])
    def expire(self, request, pk=None):
        return self._run_action(
            request,
            pk,
            handler=lambda exception: expire_disjointness_exception(
                exception=exception,
                actor=request.user,
            ),
        )

    @action(detail=True, methods=['post'])
    def reactivate(self, request, pk=None):
        return self._run_action(
            request,
            pk,
            handler=lambda exception: reactivate_disjointness_exception(
                exception=exception,
                actor=request.user,
            ),
        )


VIEWSET_CLASS_MAP['disjointnessexception'] = DisjointnessExceptionViewSet
globals()['DisjointnessExceptionViewSet'] = DisjointnessExceptionViewSet


# --- Planning / DeploymentPlan action endpoints ---


class DeploymentPlanViewSet(VIEWSET_CLASS_MAP['deploymentplan']):
    """Extends the generated DeploymentPlan viewset with execute/rollback actions."""

    @action(detail=True, methods=['post'])
    def execute(self, request, pk=None):
        plan = self.get_object()
        if not request.user.has_perm('netbox_plant_graph.change_deploymentplan', plan):
            raise PermissionDenied('This user does not have permission to execute this plan.')
        try:
            plan = execute_plan(plan, user=request.user)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=400)
        serializer = self.get_serializer(plan)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def rollback(self, request, pk=None):
        plan = self.get_object()
        if not request.user.has_perm('netbox_plant_graph.change_deploymentplan', plan):
            raise PermissionDenied('This user does not have permission to rollback this plan.')
        try:
            plan = rollback_plan(plan, user=request.user)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=400)
        serializer = self.get_serializer(plan)
        return Response(serializer.data)


VIEWSET_CLASS_MAP['deploymentplan'] = DeploymentPlanViewSet
globals()['DeploymentPlanViewSet'] = DeploymentPlanViewSet


# --- Planning / Template stamp action endpoints ---


class AssemblyTemplateViewSet(VIEWSET_CLASS_MAP['assemblytemplate']):
    """Extends the generated AssemblyTemplate viewset with a stamp action."""

    @action(detail=True, methods=['post'])
    def stamp(self, request, pk=None):
        template = self.get_object()
        serializer = AssemblyTemplateStampSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        from dcim.models import DeviceRole, Location, Rack, Site
        from netbox_plant_graph.models import DeploymentPlan

        site = Site.objects.get(pk=data['site'])
        location = Location.objects.get(pk=data['location']) if data.get('location') else None
        rack = Rack.objects.get(pk=data['rack']) if data.get('rack') else None
        device_role = DeviceRole.objects.get(pk=data['device_role'])
        plan = DeploymentPlan.objects.get(pk=data['plan']) if data.get('plan') else None

        result = stamp_passive_device(
            template=template,
            site=site,
            location=location,
            rack=rack,
            position=data.get('position'),
            face=data.get('face', 'front'),
            device_role=device_role,
            name=data['name'],
            plan=plan,
            user=request.user,
            dry_run=data.get('dry_run', False),
        )

        if data.get('dry_run', False):
            return Response({'status': 'dry_run', 'detail': 'Validation passed; no objects created.'})

        return Response({
            'status': 'stamped',
            'device_id': result.device.pk if result.device else None,
            'rear_port_count': len(result.rear_ports),
            'front_port_count': len(result.front_ports),
            'port_mapping_count': len(result.port_mappings),
        })


VIEWSET_CLASS_MAP['assemblytemplate'] = AssemblyTemplateViewSet
globals()['AssemblyTemplateViewSet'] = AssemblyTemplateViewSet


class SpatialTemplateViewSet(VIEWSET_CLASS_MAP['spatialtemplate']):
    """Extends the generated SpatialTemplate viewset with a stamp action."""

    @action(detail=True, methods=['post'])
    def stamp(self, request, pk=None):
        template = self.get_object()
        serializer = SpatialTemplateStampSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        from dcim.models import Location, Site
        from netbox_plant_graph.models import DeploymentPlan
        from netbox_plant_graph.services.spatial_stamp import build_floorplan_sync_summary

        site = Site.objects.get(pk=data['site'])
        parent_location = Location.objects.get(pk=data['parent_location']) if data.get('parent_location') else None
        plan = DeploymentPlan.objects.get(pk=data['plan']) if data.get('plan') else None

        scope = parent_location if parent_location else site

        result = stamp_spatial_template(
            template=template,
            scope=scope,
            plan=plan,
            user=request.user,
            dry_run=data.get('dry_run', False),
            sync_floorplan=data.get('sync_floorplan', True),
            force_floorplan_sync=data.get('force_floorplan_sync', False),
        )

        if data.get('dry_run', False):
            return Response({'status': 'dry_run', 'detail': 'Validation passed; no objects created.'})

        return Response({
            'status': 'stamped',
            'locations_created': len(result.locations),
            'racks_created': len(result.racks),
            'placements_created': len(result.placements),
            'stamp_records': len(result.stamp_records),
            'floorplan_sync': build_floorplan_sync_summary(
                result,
                sync_requested=data.get('sync_floorplan', True),
            ),
        })


VIEWSET_CLASS_MAP['spatialtemplate'] = SpatialTemplateViewSet
globals()['SpatialTemplateViewSet'] = SpatialTemplateViewSet


class RackPopulationTemplateViewSet(VIEWSET_CLASS_MAP['rackpopulationtemplate']):
    """Extends the generated RackPopulationTemplate viewset with a stamp action."""

    @action(detail=True, methods=['post'])
    def stamp(self, request, pk=None):
        template = self.get_object()
        serializer = RackPopulationTemplateStampSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        from dcim.models import Rack
        from netbox_plant_graph.models import DeploymentPlan

        rack = Rack.objects.get(pk=data['rack'])
        plan = DeploymentPlan.objects.get(pk=data['plan']) if data.get('plan') else None

        result = stamp_rack_population(
            template=template,
            rack=rack,
            plan=plan,
            user=request.user,
            dry_run=data.get('dry_run', False),
        )

        if data.get('dry_run', False):
            return Response({'status': 'dry_run', 'detail': 'Validation passed; no objects created.'})

        return Response({
            'status': 'stamped',
            'devices_created': len(result.devices),
            'stamp_records': len(result.stamp_records),
        })


VIEWSET_CLASS_MAP['rackpopulationtemplate'] = RackPopulationTemplateViewSet
globals()['RackPopulationTemplateViewSet'] = RackPopulationTemplateViewSet


# --- Unified stamp preview endpoint ---


class StampPreviewAPIView(APIView):
    """
    Dry-run preview for any template type.

    POST /api/plugins/netbox_plant_graph/stamps/preview/

    Dispatches to the appropriate stamp service with dry_run=True and returns
    the list of objects that would be created.
    """
    queryset = Fabric.objects.all()  # Required for NetBox TokenPermissions

    def post(self, request):
        serializer = StampPreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        template_type = data['template_type']
        template_id = data['template_id']
        params = data.get('parameters', {})

        if template_type == 'assembly':
            return self._preview_assembly(template_id, params)
        elif template_type == 'spatial':
            return self._preview_spatial(template_id, params)
        elif template_type == 'rack_population':
            return self._preview_rack_population(template_id, params)
        else:
            return Response({'error': f'Unknown template_type: {template_type}'}, status=400)

    def _preview_assembly(self, template_id, params):
        from netbox_plant_graph.models import AssemblyTemplate

        try:
            template = AssemblyTemplate.objects.get(pk=template_id)
        except AssemblyTemplate.DoesNotExist:
            raise NotFound(f'AssemblyTemplate {template_id} not found')

        connectors = list(
            template.connectors.order_by('side', 'connector_number').values(
                'side', 'connector_number', 'connector_type', 'position_count', 'label',
            )
        )
        mappings = template.mappings.count()
        has_device_type = template.device_type is not None

        objects_to_create = []
        if has_device_type:
            objects_to_create.append({'type': 'Device', 'source': 'DeviceType auto-stamp'})
        else:
            a_conns = [c for c in connectors if c['side'] == 'A']
            b_conns = [c for c in connectors if c['side'] == 'B']
            objects_to_create.append({'type': 'Device', 'source': 'manual creation'})
            for c in a_conns:
                objects_to_create.append({
                    'type': 'RearPort', 'name': c['label'] or f"A{c['connector_number']}",
                    'positions': c['position_count'],
                })
            for c in b_conns:
                objects_to_create.append({
                    'type': 'FrontPort', 'name': c['label'] or f"B{c['connector_number']}",
                    'positions': c['position_count'],
                })
        objects_to_create.append({'type': 'PortMapping', 'count': mappings})
        objects_to_create.append({'type': 'StampRecord', 'count': 1})

        return Response({
            'template_type': 'assembly',
            'template_id': template_id,
            'template_name': template.name,
            'assembly_type': template.assembly_type,
            'device_type': str(template.device_type) if template.device_type else None,
            'objects_to_create': objects_to_create,
        })

    def _preview_spatial(self, template_id, params):
        from netbox_plant_graph.models import SpatialTemplate

        try:
            template = SpatialTemplate.objects.get(pk=template_id)
        except SpatialTemplate.DoesNotExist:
            raise NotFound(f'SpatialTemplate {template_id} not found')

        # Walk the tree to compute expected object counts
        counts = {'locations': 0, 'racks': 0, 'placements': 0, 'rack_population_cascades': 0}
        self._count_spatial_nodes(
            list(template.nodes.filter(parent__isnull=True).order_by('sort_order', 'pk')),
            counts,
        )

        return Response({
            'template_type': 'spatial',
            'template_id': template_id,
            'template_name': template.name,
            'root_node_type': template.root_node_type,
            'expected_counts': counts,
        })

    def _count_spatial_nodes(self, nodes, counts):
        for node in nodes:
            children = list(node.children.order_by('sort_order', 'pk'))
            for _ in range(node.quantity):
                if node.node_type == 'rack_position':
                    counts['racks'] += 1
                    counts['placements'] += 1
                    if node.rack_population_template:
                        counts['rack_population_cascades'] += 1
                        slot_count = node.rack_population_template.slots.count()
                        counts['placements'] += slot_count  # devices get placements
                else:
                    counts['locations'] += 1
                    counts['placements'] += 1
                    self._count_spatial_nodes(children, counts)

    def _preview_rack_population(self, template_id, params):
        from netbox_plant_graph.models import RackPopulationTemplate

        try:
            template = RackPopulationTemplate.objects.get(pk=template_id)
        except RackPopulationTemplate.DoesNotExist:
            raise NotFound(f'RackPopulationTemplate {template_id} not found')

        slots = list(template.slots.select_related(
            'device_type', 'device_role', 'assembly_template',
        ).order_by('sort_order', 'u_position'))

        devices = []
        assembly_cascades = 0
        for idx, slot in enumerate(slots):
            device_info = {
                'name_pattern': slot.name_pattern,
                'device_type': str(slot.device_type),
                'device_role': str(slot.device_role),
                'u_position': slot.u_position,
                'face': slot.face,
            }
            if slot.assembly_template:
                device_info['assembly_cascade'] = slot.assembly_template.name
                assembly_cascades += 1
            devices.append(device_info)

        return Response({
            'template_type': 'rack_population',
            'template_id': template_id,
            'template_name': template.name,
            'slot_count': len(slots),
            'assembly_cascades': assembly_cascades,
            'devices': devices,
        })
