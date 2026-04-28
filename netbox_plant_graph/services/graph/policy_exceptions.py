from django.utils import timezone

from netbox_plant_graph.models import DisjointnessException, Fabric


def _normalize_fabric(fabric=None):
    if isinstance(fabric, Fabric):
        return fabric
    if fabric is None:
        return Fabric.objects.order_by('pk').first()
    return Fabric.objects.filter(pk=getattr(fabric, 'pk', fabric)).first()


def _reference_identity(reference) -> tuple[str | None, str, int]:
    return (reference.app_label, reference.model, reference.pk)


def _exception_target_identity(exception: DisjointnessException) -> tuple[str | None, str, int] | None:
    if exception.target_type_id is None or exception.target_id is None:
        return None
    return (exception.target_type.app_label, exception.target_type.model, exception.target_id)


def _normalized_plane_pairs(plane_pair_ids) -> set[tuple[int, int]]:
    return {
        tuple(sorted(plane_pair))
        for plane_pair in plane_pair_ids
        if len(plane_pair) == 2
    }


def is_disjointness_exception_active(exception: DisjointnessException, *, now=None) -> bool:
    now = now or timezone.now()
    if exception.status != 'approved' or not exception.active:
        return False
    if exception.expires_at is not None and exception.expires_at <= now:
        return False
    return True


def list_active_disjointness_exceptions(*, fabric=None, now=None) -> tuple[DisjointnessException, ...]:
    selected_fabric = _normalize_fabric(fabric=fabric)
    if selected_fabric is None:
        return ()
    queryset = (
        DisjointnessException.objects.filter(fabric=selected_fabric)
        .select_related('fabric', 'target_type', 'plane_a', 'plane_b', 'approved_by')
        .order_by('-created', '-pk')
    )
    return tuple(
        exception
        for exception in queryset
        if is_disjointness_exception_active(exception, now=now)
    )


def approve_disjointness_exception(*, exception, actor=None) -> DisjointnessException:
    if not isinstance(exception, DisjointnessException):
        exception = DisjointnessException.objects.get(pk=getattr(exception, 'pk', exception))
    now = timezone.now()
    if not exception.policy_mode:
        exception.policy_mode = exception.fabric.disjointness_policy
    exception.status = 'approved'
    exception.active = True
    exception.approved_by = actor
    exception.approved_at = now
    if exception.expires_at is not None and exception.expires_at <= now:
        exception.expires_at = None
    exception.save()
    return exception


def expire_disjointness_exception(*, exception, actor=None) -> DisjointnessException:
    if not isinstance(exception, DisjointnessException):
        exception = DisjointnessException.objects.get(pk=getattr(exception, 'pk', exception))
    now = timezone.now()
    exception.status = 'expired'
    exception.active = False
    if exception.expires_at is None or exception.expires_at > now:
        exception.expires_at = now
    metadata = dict(exception.metadata or {})
    metadata['last_expired_by_id'] = getattr(actor, 'pk', None)
    metadata['last_expired_at'] = now.isoformat()
    exception.metadata = metadata
    exception.save()
    return exception


def reactivate_disjointness_exception(*, exception, actor=None) -> DisjointnessException:
    if not isinstance(exception, DisjointnessException):
        exception = DisjointnessException.objects.get(pk=getattr(exception, 'pk', exception))
    now = timezone.now()
    if not exception.policy_mode:
        exception.policy_mode = exception.fabric.disjointness_policy
    exception.status = 'approved'
    exception.active = True
    exception.approved_by = actor
    exception.approved_at = now
    if exception.expires_at is not None and exception.expires_at <= now:
        exception.expires_at = None
    exception.save()
    return exception


def _match_plane_pair(exception: DisjointnessException, plane_pair_ids) -> bool:
    exception_plane_pair = exception.plane_pair_ids
    if not exception_plane_pair:
        return False
    return exception_plane_pair in _normalized_plane_pairs(plane_pair_ids)


def exception_matches_domain(*, exception: DisjointnessException, domain) -> bool:
    if not _match_plane_pair(exception, domain.plane_pair_ids):
        return False

    target_identity = _exception_target_identity(exception)
    if target_identity is None:
        return False

    artifact_identities = {_reference_identity(artifact) for artifact in domain.artifacts}
    bridge_identities = {_reference_identity(bridge.edge) for bridge in domain.edge_bridges}
    representative_identities = {_reference_identity(reference) for reference in domain.representative_targets}

    if exception.exception_type == 'shared_passive_artifact':
        return target_identity in artifact_identities
    if exception.exception_type == 'cross_plane_fine_edge':
        return target_identity in bridge_identities
    if exception.exception_type == 'contamination_domain':
        return target_identity in artifact_identities | bridge_identities | representative_identities
    return False


def matching_disjointness_exceptions_for_finding(
    *,
    exceptions: tuple[DisjointnessException, ...],
    finding_type: str,
    target_reference,
    plane_pair_ids,
    domain=None,
) -> tuple[DisjointnessException, ...]:
    target_identity = _reference_identity(target_reference)
    matches = []
    for exception in exceptions:
        if not _match_plane_pair(exception, plane_pair_ids):
            continue
        exception_target_identity = _exception_target_identity(exception)
        if exception_target_identity is None:
            continue
        if exception.exception_type == finding_type and exception_target_identity == target_identity:
            matches.append(exception)
            continue
        if domain is not None and exception.exception_type == 'contamination_domain' and exception_matches_domain(
            exception=exception,
            domain=domain,
        ):
            matches.append(exception)
    return tuple(matches)


def build_disjointness_exception_review(*, fabric=None, evaluation=None):
    selected_fabric = _normalize_fabric(fabric=fabric)
    if selected_fabric is None:
        return {
            'active_exceptions': (),
            'covered_exception_ids': set(),
            'domain_matches': {},
            'drifted_exceptions': (),
        }

    active_exceptions = list_active_disjointness_exceptions(fabric=selected_fabric)
    if evaluation is None:
        return {
            'active_exceptions': active_exceptions,
            'covered_exception_ids': set(),
            'domain_matches': {},
            'drifted_exceptions': active_exceptions,
        }

    domain_matches = {}
    covered_exception_ids = set()
    for domain in evaluation.contamination_domains:
        matches = tuple(
            exception
            for exception in active_exceptions
            if exception_matches_domain(exception=exception, domain=domain)
        )
        if matches:
            domain_matches[domain.domain_key] = matches
            covered_exception_ids.update(exception.pk for exception in matches)

    drifted_exceptions = tuple(
        exception
        for exception in active_exceptions
        if exception.pk not in covered_exception_ids
    )
    return {
        'active_exceptions': active_exceptions,
        'covered_exception_ids': covered_exception_ids,
        'domain_matches': domain_matches,
        'drifted_exceptions': drifted_exceptions,
    }
