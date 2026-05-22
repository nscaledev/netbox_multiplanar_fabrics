from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from django.db import transaction

from netbox_plant_graph.models import OnboardingDesignItem, OnboardingSourceArtifact

from .workspace import changelog_actor_context, stable_digest


@dataclass(frozen=True)
class NormalizationResult:
    artifact_id: int
    created: int
    updated: int
    skipped: int
    issues: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            'artifact_id': self.artifact_id,
            'created': self.created,
            'updated': self.updated,
            'skipped': self.skipped,
            'issues': list(self.issues),
        }


def normalize_source_artifact(artifact: OnboardingSourceArtifact, *, actor=None) -> NormalizationResult:
    parser_key = artifact.parser_key or 'generic_json'
    payload = artifact.raw_payload or {}
    try:
        staged_items = tuple(_staged_items_for_parser(parser_key, payload, artifact))
    except ValueError as exc:
        result = NormalizationResult(
            artifact_id=artifact.pk,
            created=0,
            updated=0,
            skipped=0,
            issues=({'severity': 'error', 'message': str(exc)},),
        )
        with changelog_actor_context(actor):
            artifact.status = 'failed'
            artifact.parse_result = result.to_dict()
            artifact.save(update_fields=('status', 'parse_result', 'last_updated'))
        return result

    created = 0
    updated = 0
    skipped = 0
    issues: list[dict[str, Any]] = []
    with changelog_actor_context(actor), transaction.atomic():
        for staged in staged_items:
            design_item, was_created = OnboardingDesignItem.objects.update_or_create(
                workspace=artifact.workspace,
                kind=staged['kind'],
                natural_key=staged['natural_key'],
                defaults={
                    'source_artifact': artifact,
                    'desired_state': staged['desired_state'],
                    'provenance': staged['provenance'],
                    'validation_status': staged.get('validation_status', 'valid'),
                    'validation_messages': staged.get('validation_messages', []),
                    'metadata': staged.get('metadata', {}),
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1
            if design_item.validation_status == 'warning':
                issues.extend(design_item.validation_messages or ())
        result = NormalizationResult(
            artifact_id=artifact.pk,
            created=created,
            updated=updated,
            skipped=skipped,
            issues=tuple(issues),
        )
        artifact.status = 'normalized'
        artifact.parse_result = result.to_dict()
        artifact.save(update_fields=('status', 'parse_result', 'last_updated'))
    return result


def _staged_items_for_parser(parser_key: str, payload: Any, artifact: OnboardingSourceArtifact) -> Iterable[dict[str, Any]]:
    if parser_key == 'mpf_blueprint_bundle':
        return _blueprint_bundle_items(payload, artifact)
    if parser_key == 'import_reconcile_json':
        return _import_reconcile_items(payload, artifact)
    if parser_key == 'manual_design_item':
        return _manual_design_items(payload, artifact)
    if parser_key == 'generic_json':
        return _generic_json_items(payload, artifact)
    raise ValueError(f'Unsupported onboarding source parser: {parser_key!r}.')


def _blueprint_bundle_items(payload: Any, artifact: OnboardingSourceArtifact) -> tuple[dict[str, Any], ...]:
    if not isinstance(payload, Mapping):
        raise ValueError('Blueprint bundle payload must be a JSON object.')
    items: list[dict[str, Any]] = []
    architecture = payload.get('architecture') or payload.get('blueprint')
    if isinstance(architecture, Mapping):
        items.append(
            _item(
                artifact,
                kind='fabric_architecture_blueprint',
                natural_key=_natural_key('architecture', architecture),
                desired_state=dict(architecture),
                source_field='architecture',
            )
        )
    stamp_parameters = payload.get('stamp_parameters') or payload.get('parameters')
    if isinstance(stamp_parameters, Mapping):
        items.append(
            _item(
                artifact,
                kind='stamp_parameters',
                natural_key=_natural_key('stamp_parameters', stamp_parameters),
                desired_state=dict(stamp_parameters),
                source_field='stamp_parameters',
            )
        )
    import_payload = payload.get('import_payload')
    if import_payload is not None:
        items.extend(_import_reconcile_items(import_payload, artifact, source_field='import_payload'))
    elif isinstance(payload.get('items'), list):
        items.extend(_import_reconcile_items(payload, artifact, source_field='items'))
    if not items:
        raise ValueError('Blueprint bundle did not contain architecture, stamp_parameters, or import items.')
    return tuple(items)


def _import_reconcile_items(
    payload: Any,
    artifact: OnboardingSourceArtifact,
    *,
    source_field: str = 'items',
) -> tuple[dict[str, Any], ...]:
    raw_items = _payload_items(payload)
    staged: list[dict[str, Any]] = []
    for index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, Mapping):
            staged.append(
                _item(
                    artifact,
                    kind='invalid_import_item',
                    natural_key=f'invalid:{artifact.pk}:{index}',
                    desired_state={'value': raw_item},
                    source_field=source_field,
                    source_row=index,
                    validation_status='conflict',
                    validation_messages=[{'severity': 'error', 'message': 'Import item must be a JSON object.'}],
                )
            )
            continue
        kind = str(raw_item.get('kind') or raw_item.get('object_type') or raw_item.get('model') or 'import_item')
        staged.append(
            _item(
                artifact,
                kind=kind,
                natural_key=_natural_key(kind, raw_item, fallback=f'{artifact.pk}:{index}'),
                desired_state=dict(raw_item),
                source_field=source_field,
                source_row=index,
            )
        )
    return tuple(staged)


def _manual_design_items(payload: Any, artifact: OnboardingSourceArtifact) -> tuple[dict[str, Any], ...]:
    if isinstance(payload, Mapping) and isinstance(payload.get('items'), list):
        return _import_reconcile_items(payload, artifact)
    if not isinstance(payload, Mapping):
        raise ValueError('Manual design item payload must be a JSON object.')
    kind = str(payload.get('kind') or 'manual_note')
    return (
        _item(
            artifact,
            kind=kind,
            natural_key=_natural_key(kind, payload),
            desired_state=dict(payload),
            source_field='manual_entry',
        ),
    )


def _generic_json_items(payload: Any, artifact: OnboardingSourceArtifact) -> tuple[dict[str, Any], ...]:
    if isinstance(payload, Mapping) and isinstance(payload.get('items'), list):
        return _import_reconcile_items(payload, artifact)
    if isinstance(payload, list):
        return _import_reconcile_items(payload, artifact)
    if isinstance(payload, Mapping):
        return _manual_design_items(payload, artifact)
    raise ValueError('Generic JSON onboarding source must be an object or list.')


def _payload_items(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, Mapping) and isinstance(payload.get('items'), list):
        return list(payload.get('items') or [])
    raise ValueError('Import reconciliation payload must be a list or an object with an items list.')


def _item(
    artifact: OnboardingSourceArtifact,
    *,
    kind: str,
    natural_key: str,
    desired_state: Mapping[str, Any],
    source_field: str,
    source_row: int | None = None,
    validation_status: str = 'valid',
    validation_messages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        'kind': kind,
        'natural_key': natural_key,
        'desired_state': dict(desired_state),
        'provenance': {
            'source_artifact_id': artifact.pk,
            'source_system': 'onboarding_workspace',
            'source_document': artifact.source_label or artifact.name,
            'source_field': source_field,
            'source_row': source_row,
            'entry_method': 'document' if artifact.artifact_type != 'manual_entry' else 'manual_ui',
        },
        'validation_status': validation_status,
        'validation_messages': validation_messages or [],
        'metadata': {
            'source_sha256': artifact.content_sha256,
            'parser_key': artifact.parser_key,
        },
    }


def _natural_key(kind: str, payload: Mapping[str, Any], *, fallback: str | None = None) -> str:
    for key in ('natural_key', 'identity', 'slug', 'name', 'idempotency_key', 'external_id'):
        value = payload.get(key)
        if value not in (None, ''):
            return f'{kind}:{value}'
    nested = payload.get('data')
    if isinstance(nested, Mapping):
        for key in ('natural_key', 'identity', 'slug', 'name', 'idempotency_key', 'external_id'):
            value = nested.get(key)
            if value not in (None, ''):
                return f'{kind}:{value}'
    if fallback:
        return f'{kind}:{fallback}'
    return f'{kind}:sha256:{stable_digest(payload)[:24]}'
