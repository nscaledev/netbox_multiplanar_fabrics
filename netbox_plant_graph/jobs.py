from netbox.jobs import JobRunner

from .services.graph.audits import run_plane_audit
from .services.graph.persistent_audits import run_persistent_plane_audit
from .services.graph.retention import apply_audit_retention
from .services.sync.rebuilder import rebuild_graph


class FullGraphRebuildJob(JobRunner):
    class Meta:
        name = 'Full Graph Rebuild'
        description = 'Rebuild the plant graph from NetBox source objects.'

    def run(self, *args, **kwargs):
        return rebuild_graph(trigger_mode='job')


class IncrementalRefreshJob(JobRunner):
    class Meta:
        name = 'Incremental Refresh'
        description = 'Refresh a scoped portion of the plant graph.'

    def run(self, *args, **kwargs):
        return rebuild_graph(scope=kwargs.get('scope'), trigger_mode='incremental')


class PlaneAuditJob(JobRunner):
    class Meta:
        name = 'Plane Audit'
        description = 'Run plane audit checks.'

    def run(self, *args, **kwargs):
        return run_plane_audit(scope=kwargs.get('scope'))


class PersistentPlaneAuditJob(JobRunner):
    class Meta:
        name = 'Persistent Plane Audit'
        description = 'Run plane audit checks and persist durable finding state.'

    def run(self, *args, **kwargs):
        return run_persistent_plane_audit(fabric=kwargs.get('fabric'), trigger_mode='job')


class AuditRetentionJob(JobRunner):
    class Meta:
        name = 'Audit Retention'
        description = 'Expire suppressions and prune old audit workflow records.'

    def run(self, *args, **kwargs):
        return apply_audit_retention()


class BlastRadiusJob(JobRunner):
    class Meta:
        name = 'Physical Cable Blast Radius'
        description = 'Compute the physical cable blast radius for a selected failure.'

    def run(self, *args, **kwargs):
        from .models import AttachmentUnit, PlantNode
        from .services.graph.blast_radius import compute_blast_radius

        target_type = kwargs.get('target_type')
        target_id = kwargs.get('target_id')
        resolution = kwargs.get('resolution', 'attachment_unit')

        _model_map = {
            'attachmentunit': AttachmentUnit,
            'plantnode': PlantNode,
        }
        model = _model_map.get(str(target_type).lower() if target_type else '')
        if model is None or not target_id:
            raise ValueError(
                f'BlastRadiusJob requires target_type (attachmentunit|plantnode) '
                f'and target_id; got target_type={target_type!r}, target_id={target_id!r}.'
            )
        target = model.objects.get(pk=target_id)
        return compute_blast_radius(target=target, resolution=resolution)
