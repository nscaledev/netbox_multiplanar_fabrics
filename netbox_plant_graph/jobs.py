from netbox.jobs import JobRunner

from .services.graph.audits import run_plane_audit
from .services.sync.rebuilder import rebuild_graph


class FullGraphRebuildJob(JobRunner):
    class Meta:
        name = 'Full Graph Rebuild'
        description = 'Rebuild the plant graph from NetBox source objects.'

    def run(self, *args, **kwargs):
        return rebuild_graph()


class IncrementalRefreshJob(JobRunner):
    class Meta:
        name = 'Incremental Refresh'
        description = 'Refresh a scoped portion of the plant graph.'

    def run(self, *args, **kwargs):
        return rebuild_graph(scope=kwargs.get('scope'))


class PlaneAuditJob(JobRunner):
    class Meta:
        name = 'Plane Audit'
        description = 'Run plane audit checks.'

    def run(self, *args, **kwargs):
        return run_plane_audit(scope=kwargs.get('scope'))


class BlastRadiusJob(JobRunner):
    class Meta:
        name = 'Blast Radius'
        description = 'Compute the blast radius for a selected failure.'

    def run(self, *args, **kwargs):
        return 'Not implemented yet'
