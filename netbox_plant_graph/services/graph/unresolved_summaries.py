from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from netbox_plant_graph.models import Fabric, GraphBuildRun, UnresolvedStateObservation, UnresolvedStateSummary

from .payloads import UnresolvedCandidatePayload, UnresolvedSyncResultPayload
from .unresolved_candidates import collect_unresolved_candidates
from .unresolved_fingerprints import build_unresolved_summary_fingerprint


def _plugin_config():
    return getattr(settings, 'PLUGINS_CONFIG', {}).get('netbox_plant_graph', {})


def unresolved_summary_persistence_enabled() -> bool:
    return bool(_plugin_config().get('persist_unresolved_summaries', False))


def _content_type_and_id(obj):
    if obj is None:
        return None, None
    return (
        ContentType.objects.get_for_model(obj, for_concrete_model=False),
        obj.pk,
    )


def _plane_for_candidate(fabric, plane_ids):
    if len(plane_ids) != 1:
        return None
    return fabric.planes.filter(plane_number=plane_ids[0]).first()


def _fingerprint_for_candidate(candidate: UnresolvedCandidatePayload) -> str:
    return build_unresolved_summary_fingerprint(
        summary_kind=candidate.summary_kind,
        cause_code=candidate.cause_code,
        fabric_id=candidate.fabric_id,
        plane_numbers=candidate.plane_ids,
        owner_object=candidate.owner_object,
        representative_object=candidate.representative_object,
        selector=candidate.selector,
        anchors=candidate.anchors,
    )


def _is_comparable_full_fabric_build(build_run: GraphBuildRun) -> bool:
    metadata = build_run.metadata or {}
    return metadata.get('comparable_scope') == 'full_fabric'


def sync_unresolved_state(*, fabric, build_run: GraphBuildRun, candidates) -> UnresolvedSyncResultPayload:
    if not isinstance(fabric, Fabric):
        fabric = Fabric.objects.get(pk=getattr(fabric, 'pk', fabric))

    now = timezone.now()
    comparable_scope = _is_comparable_full_fabric_build(build_run)
    existing = {
        summary.fingerprint: summary
        for summary in UnresolvedStateSummary.objects.filter(fabric=fabric)
    }

    seen_fingerprints = set()
    created_count = 0
    updated_count = 0
    reopened_count = 0
    resolved_count = 0
    observation_count = 0

    for candidate in candidates:
        fingerprint = _fingerprint_for_candidate(candidate)
        seen_fingerprints.add(fingerprint)
        summary = existing.get(fingerprint)
        scope_type, scope_id = _content_type_and_id(candidate.scope_object)
        owner_type, owner_id = _content_type_and_id(candidate.owner_object)
        representative_type, representative_id = _content_type_and_id(candidate.representative_object)

        if summary is None:
            candidate_metadata = dict(candidate.metadata or {})
            candidate_metadata.setdefault('reopen_count', 0)
            summary = UnresolvedStateSummary.objects.create(
                fabric=fabric,
                plane=_plane_for_candidate(fabric, candidate.plane_ids),
                scope_type=scope_type,
                scope_id=scope_id,
                scope_label=candidate.scope_label,
                summary_kind=candidate.summary_kind,
                cause_code=candidate.cause_code,
                fingerprint=fingerprint,
                active=True,
                first_seen_build=build_run,
                last_seen_build=build_run,
                first_seen_at=now,
                last_seen_at=now,
                owner_object_type=owner_type,
                owner_object_id=owner_id,
                representative_object_type=representative_type,
                representative_object_id=representative_id,
                plane_ids=list(candidate.plane_ids),
                selector=candidate.selector,
                anchors=candidate.anchors,
                impact=candidate.impact,
                metadata=candidate_metadata,
            )
            existing[fingerprint] = summary
            created_count += 1
        else:
            reopened = not summary.active
            candidate_metadata = dict(candidate.metadata or {})
            candidate_metadata['reopen_count'] = int((summary.metadata or {}).get('reopen_count') or 0) + (1 if reopened else 0)
            summary.plane = _plane_for_candidate(fabric, candidate.plane_ids)
            summary.scope_type = scope_type
            summary.scope_id = scope_id
            summary.scope_label = candidate.scope_label
            summary.summary_kind = candidate.summary_kind
            summary.cause_code = candidate.cause_code
            summary.active = True
            summary.last_seen_build = build_run
            summary.last_seen_at = now
            summary.resolved_at = None
            summary.owner_object_type = owner_type
            summary.owner_object_id = owner_id
            summary.representative_object_type = representative_type
            summary.representative_object_id = representative_id
            summary.plane_ids = list(candidate.plane_ids)
            summary.selector = candidate.selector
            summary.anchors = candidate.anchors
            summary.impact = candidate.impact
            summary.metadata = candidate_metadata
            summary.save()
            if reopened:
                reopened_count += 1
            else:
                updated_count += 1

        _, created = UnresolvedStateObservation.objects.get_or_create(
            summary=summary,
            build=build_run,
            defaults={
                'observed_at': now,
                'impact': candidate.impact,
                'metadata': candidate_metadata,
            },
        )
        if not created:
            observation = UnresolvedStateObservation.objects.get(summary=summary, build=build_run)
            observation.observed_at = now
            observation.impact = candidate.impact
            observation.metadata = candidate_metadata
            observation.save()
        observation_count += 1

    if comparable_scope:
        stale_summaries = UnresolvedStateSummary.objects.filter(fabric=fabric, active=True).exclude(fingerprint__in=seen_fingerprints)
        for summary in stale_summaries:
            summary.active = False
            summary.resolved_at = now
            summary.save(update_fields=('active', 'resolved_at', 'last_updated'))
            resolved_count += 1

    return UnresolvedSyncResultPayload(
        candidate_count=len(tuple(candidates)),
        created_count=created_count,
        updated_count=updated_count,
        reopened_count=reopened_count,
        resolved_count=resolved_count,
        observation_count=observation_count,
        comparable_scope=comparable_scope,
    )


def sync_unresolved_state_for_build(*, fabric, build_run: GraphBuildRun) -> UnresolvedSyncResultPayload:
    candidates = collect_unresolved_candidates(fabric=fabric, build_run=build_run)
    return sync_unresolved_state(fabric=fabric, build_run=build_run, candidates=candidates)
