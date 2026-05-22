from __future__ import annotations

from typing import Any

from netbox_plant_graph.models import OnboardingSourceArtifact, OnboardingWorkspace

from .workspace import changelog_actor_context, stable_digest


def hash_source_payload(*, raw_payload: Any = None, source_uri: str = '') -> str:
    return stable_digest({'raw_payload': raw_payload or {}, 'source_uri': source_uri or ''})


def attach_source_artifact(
    *,
    workspace: OnboardingWorkspace,
    name: str,
    artifact_type: str = 'api_payload',
    source_uri: str = '',
    raw_payload: Any = None,
    payload_version: str = '',
    source_label: str = '',
    parser_key: str = '',
    actor=None,
    metadata: dict[str, Any] | None = None,
) -> OnboardingSourceArtifact:
    raw_payload = raw_payload or {}
    content_sha256 = hash_source_payload(raw_payload=raw_payload, source_uri=source_uri)
    if not parser_key:
        parser_key = _default_parser_key(artifact_type, raw_payload)
    with changelog_actor_context(actor):
        artifact = OnboardingSourceArtifact.objects.create(
            workspace=workspace,
            artifact_type=artifact_type,
            name=name,
            source_uri=source_uri,
            content_sha256=content_sha256,
            payload_version=payload_version,
            source_label=source_label,
            parser_key=parser_key,
            raw_payload=raw_payload,
            metadata=metadata or {},
        )
        workspace.source_summary = _source_summary(workspace)
        workspace.save(update_fields=('source_summary', 'last_updated'))
    return artifact


def supersede_source_artifact(artifact: OnboardingSourceArtifact, *, actor=None) -> OnboardingSourceArtifact:
    with changelog_actor_context(actor):
        artifact.status = 'superseded'
        artifact.save(update_fields=('status', 'last_updated'))
        artifact.workspace.source_summary = _source_summary(artifact.workspace)
        artifact.workspace.save(update_fields=('source_summary', 'last_updated'))
    return artifact


def _default_parser_key(artifact_type: str, raw_payload: Any) -> str:
    if artifact_type == 'blueprint_bundle':
        return 'mpf_blueprint_bundle'
    if isinstance(raw_payload, dict) and isinstance(raw_payload.get('items'), list):
        return 'import_reconcile_json'
    if isinstance(raw_payload, list):
        return 'import_reconcile_json'
    if artifact_type in {'manual_entry', 'note'}:
        return 'manual_design_item'
    return 'generic_json'


def _source_summary(workspace: OnboardingWorkspace) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for status in workspace.source_artifacts.values_list('status', flat=True):
        counts[status] = counts.get(status, 0) + 1
    return {
        'total': sum(counts.values()),
        'by_status': counts,
    }
