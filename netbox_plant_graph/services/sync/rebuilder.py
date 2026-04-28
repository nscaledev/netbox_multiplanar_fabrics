from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from netbox_plant_graph.models import GraphBuildRun
from netbox_plant_graph.services.graph.unresolved_summaries import (
    sync_unresolved_state_for_build,
    unresolved_summary_persistence_enabled,
)

from .extractor import extract_source_bundle
from .graph_builder import build_graph
from .transformer import transform_source_bundle


def _scope_value(scope, name, default=None):
    if scope is None:
        return default
    if isinstance(scope, dict):
        return scope.get(name, default)
    return getattr(scope, name, default)


def _scope_object(scope):
    for field_name in ('fabric', 'site', 'location'):
        scoped_object = _scope_value(scope, field_name)
        if scoped_object is not None:
            return scoped_object, field_name
    if scope is not None and hasattr(scope, '_meta') and hasattr(scope, 'pk'):
        return scope, scope._meta.model_name
    return None, 'global'


def _initial_build_run(*, scope, trigger_mode: str) -> GraphBuildRun:
    scope_object, scope_family = _scope_object(scope)
    scope_type = None
    scope_id = None
    scope_label = ''
    if scope_object is not None:
        scope_type = ContentType.objects.get_for_model(scope_object, for_concrete_model=False)
        scope_id = scope_object.pk
        scope_label = str(scope_object)

    comparable_scope = 'partial_scope'
    if trigger_mode != 'incremental' and scope_family in {'global', 'fabric'}:
        comparable_scope = 'full_fabric'

    return GraphBuildRun.objects.create(
        fabric=_scope_value(scope, 'fabric'),
        scope_type=scope_type,
        scope_id=scope_id,
        scope_label=scope_label,
        trigger_mode=trigger_mode,
        status='running',
        started_at=timezone.now(),
        metadata={
            'scope_family': scope_family,
            'comparable_scope': comparable_scope,
        },
    )


def _finalize_stats(result: dict[str, object]) -> dict[str, object]:
    stats = dict(result)
    stats.pop('fabric_id', None)
    stats.pop('graph_revision', None)
    stats.pop('build_run', None)
    if not stats.get('dry_run'):
        stats.pop('dry_run', None)
    return stats


def rebuild_graph(*, scope=None, dry_run: bool = False, trigger_mode: str = 'manual') -> dict[str, int]:
    if dry_run:
        bundle = extract_source_bundle(scope=scope)
        graph_inputs = transform_source_bundle(bundle)
        return build_graph(graph_inputs, dry_run=True)

    build_run = _initial_build_run(scope=scope, trigger_mode=trigger_mode)
    stage = 'extract'
    started_at = build_run.started_at or timezone.now()

    try:
        bundle = extract_source_bundle(scope=scope)
        stage = 'transform'
        graph_inputs = transform_source_bundle(bundle)
        stage = 'build'
        result = build_graph(graph_inputs, dry_run=False)
        build_run.fabric_id = result.get('fabric_id') or build_run.fabric_id
        build_run.save(update_fields=('fabric', 'last_updated'))
        if unresolved_summary_persistence_enabled():
            if build_run.fabric_id is None:
                summary_sync = {'status': 'skipped_missing_fabric'}
            elif (build_run.metadata or {}).get('comparable_scope') != 'full_fabric':
                summary_sync = {'status': 'skipped_partial_scope'}
            else:
                sync_result = sync_unresolved_state_for_build(fabric=build_run.fabric_id, build_run=build_run)
                summary_sync = {
                    'status': 'completed',
                    'candidate_count': sync_result.candidate_count,
                    'created_count': sync_result.created_count,
                    'updated_count': sync_result.updated_count,
                    'reopened_count': sync_result.reopened_count,
                    'resolved_count': sync_result.resolved_count,
                    'observation_count': sync_result.observation_count,
                    'comparable_scope': sync_result.comparable_scope,
                }
        else:
            summary_sync = {'status': 'disabled'}
    except Exception as exc:
        build_run.status = 'failed'
        build_run.completed_at = timezone.now()
        build_run.metadata = {
            **(build_run.metadata or {}),
            'failed_stage': stage,
            'error_class': exc.__class__.__name__,
            'error': str(exc),
            'rebuild_duration_ms': int((build_run.completed_at - started_at).total_seconds() * 1000),
        }
        build_run.save(update_fields=('status', 'completed_at', 'metadata', 'last_updated'))
        raise

    completed_at = timezone.now()
    fabric_id = result.get('fabric_id')
    build_run.fabric_id = fabric_id or build_run.fabric_id
    build_run.status = 'completed'
    build_run.completed_at = completed_at
    build_run.stats = _finalize_stats(result)
    build_run.metadata = {
        **(build_run.metadata or {}),
        'graph_revision': result.get('graph_revision'),
        'summary_sync': summary_sync,
        'rebuild_duration_ms': int((completed_at - started_at).total_seconds() * 1000),
    }
    build_run.save(update_fields=('fabric', 'status', 'completed_at', 'stats', 'metadata', 'last_updated'))

    result['build_run'] = build_run.pk
    return result
