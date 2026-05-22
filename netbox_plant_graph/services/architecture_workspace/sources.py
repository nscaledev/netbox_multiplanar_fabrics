from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from django.db import transaction

from netbox_plant_graph.models import ArchitectureDesignComponent, ArchitectureSourceArtifact, ArchitectureWorkspace
from netbox_plant_graph.services.architecture_schema import ARCHITECTURE_SCHEMA_CONTRACT_VERSION
from netbox_plant_graph.services.blueprint_registry import architecture_definition_from_payload
from netbox_plant_graph.services.onboarding.workspace import changelog_actor_context, stable_digest

from .workspace import architecture_workspace_summary


@dataclass(frozen=True)
class ArchitectureNormalizationResult:
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


def hash_architecture_source_payload(payload: Any) -> str:
    return stable_digest(payload)


def attach_architecture_source_artifact(
    *,
    workspace: ArchitectureWorkspace,
    name: str,
    artifact_type: str = 'api_payload',
    source_uri: str = '',
    raw_payload: Any | None = None,
    payload_version: str = '',
    source_label: str = '',
    parser_key: str = '',
    metadata: Mapping[str, Any] | None = None,
    actor=None,
) -> ArchitectureSourceArtifact:
    payload = raw_payload if raw_payload is not None else {}
    with changelog_actor_context(actor):
        artifact = ArchitectureSourceArtifact.objects.create(
            workspace=workspace,
            artifact_type=artifact_type or 'api_payload',
            name=name,
            source_uri=source_uri or '',
            content_sha256=hash_architecture_source_payload(payload),
            payload_version=payload_version or '',
            source_label=source_label or '',
            parser_key=parser_key or _default_parser_for_artifact_type(artifact_type),
            raw_payload=payload,
            metadata=dict(metadata or {}),
        )
        workspace.status = 'collecting_sources'
        workspace.source_summary = architecture_workspace_summary(workspace)
        workspace.save(update_fields=('status', 'source_summary', 'last_updated'))
    return artifact


def normalize_architecture_source_artifact(
    artifact: ArchitectureSourceArtifact,
    *,
    actor=None,
) -> ArchitectureNormalizationResult:
    parser_key = artifact.parser_key or _default_parser_for_artifact_type(artifact.artifact_type)
    try:
        components = tuple(_components_for_parser(parser_key, artifact.raw_payload or {}, artifact))
    except ValueError as exc:
        result = ArchitectureNormalizationResult(
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
    issues: list[dict[str, Any]] = []
    with changelog_actor_context(actor), transaction.atomic():
        for component in components:
            obj, was_created = ArchitectureDesignComponent.objects.update_or_create(
                workspace=artifact.workspace,
                kind=component['kind'],
                natural_key=component['natural_key'],
                defaults={
                    'source_artifact': artifact,
                    'desired_state': component['desired_state'],
                    'provenance': component['provenance'],
                    'validation_status': component.get('validation_status', 'valid'),
                    'validation_messages': component.get('validation_messages', []),
                    'metadata': component.get('metadata', {}),
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1
            if obj.validation_status in {'warning', 'conflict'}:
                issues.extend(obj.validation_messages or ())

        result = ArchitectureNormalizationResult(
            artifact_id=artifact.pk,
            created=created,
            updated=updated,
            skipped=0,
            issues=tuple(issues),
        )
        artifact.status = 'normalized'
        artifact.parse_result = result.to_dict()
        artifact.save(update_fields=('status', 'parse_result', 'last_updated'))
        artifact.workspace.status = 'normalizing'
        artifact.workspace.source_summary = architecture_workspace_summary(artifact.workspace)
        artifact.workspace.save(update_fields=('status', 'source_summary', 'last_updated'))
    return result


def _default_parser_for_artifact_type(artifact_type: str) -> str:
    if artifact_type == 'blueprint_bundle':
        return 'mpf_blueprint_bundle'
    if artifact_type == 'schema_json':
        return 'architecture_schema_json'
    if artifact_type == 'stamp_template':
        return 'stamp_template_json'
    if artifact_type == 'manual_entry':
        return 'manual_component'
    return 'generic_json'


def _components_for_parser(
    parser_key: str,
    payload: Any,
    artifact: ArchitectureSourceArtifact,
) -> Iterable[dict[str, Any]]:
    if parser_key == 'mpf_blueprint_bundle':
        return _blueprint_bundle_components(payload, artifact)
    if parser_key == 'architecture_schema_json':
        return _architecture_definition_components(payload, artifact)
    if parser_key == 'stamp_template_json':
        return _stamp_template_components(payload, artifact)
    if parser_key == 'manual_component':
        return _manual_components(payload, artifact)
    if parser_key == 'generic_json':
        if isinstance(payload, Mapping) and _looks_like_blueprint_bundle(payload):
            return _blueprint_bundle_components(payload, artifact)
        if isinstance(payload, Mapping) and _looks_like_architecture_definition(payload):
            return _architecture_definition_components(payload, artifact)
        if isinstance(payload, Mapping) and isinstance(payload.get('items'), list):
            return _manual_components(payload, artifact)
        return _manual_components(payload, artifact)
    raise ValueError(f'Unsupported architecture source parser: {parser_key!r}.')


def _blueprint_bundle_components(payload: Any, artifact: ArchitectureSourceArtifact) -> tuple[dict[str, Any], ...]:
    if not isinstance(payload, Mapping):
        raise ValueError('Blueprint bundle payload must be a JSON object.')
    definition_payload = _definition_payload(payload)
    import_item = _blueprint_import_item(payload)
    components = list(_architecture_definition_components(definition_payload, artifact, import_item=import_item))
    parameter_schema = payload.get('parameter_schema') or definition_payload.get('parameter_schema') or {}
    required_device_types = payload.get('required_device_types') or definition_payload.get('required_device_types') or {}
    if parameter_schema:
        components.append(_component(artifact, 'parameter_schema', 'parameter_schema', parameter_schema, 'parameter_schema'))
    if required_device_types:
        components.append(
            _component(
                artifact,
                'required_device_types',
                'required_device_types',
                required_device_types,
                'required_device_types',
            )
        )
    for template in _stamp_templates(payload):
        components.append(
            _component(
                artifact,
                'stamp_template',
                f"stamp_template:{template.get('slug') or stable_digest(template)[:16]}",
                template,
                'stamp_templates',
            )
        )
    return tuple(components)


def _architecture_definition_components(
    payload: Any,
    artifact: ArchitectureSourceArtifact,
    *,
    import_item: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], ...]:
    if not isinstance(payload, Mapping):
        raise ValueError('Architecture definition payload must be a JSON object.')
    try:
        definition = architecture_definition_from_payload(payload)
    except ValueError:
        definition = None
    slug = str(payload.get('slug') or 'unknown')
    version = str(payload.get('version') or 'unknown')
    natural_key = f'fabric_architecture_blueprint:{slug}:{version}'
    item_payload = dict(import_item or {'kind': 'fabric_architecture_blueprint', 'definition': payload})
    item_payload.setdefault('schema_contract_version', ARCHITECTURE_SCHEMA_CONTRACT_VERSION)
    components = [
        _component(
            artifact,
            'fabric_architecture_blueprint',
            natural_key,
            item_payload,
            'definition',
            validation_status='valid' if definition is not None else 'conflict',
            validation_messages=[] if definition is not None else [{'severity': 'error', 'message': 'Architecture definition is malformed.'}],
        )
    ]
    for role in payload.get('roles') or ():
        if isinstance(role, Mapping):
            components.append(_component(artifact, 'architecture_role', f"role:{role.get('slug')}", role, 'roles'))
    for pattern in payload.get('transfer_patterns') or ():
        if isinstance(pattern, Mapping):
            components.append(_component(artifact, 'transfer_pattern', f"transfer_pattern:{pattern.get('slug')}", pattern, 'transfer_patterns'))
    for rule_set in payload.get('allocation_rule_sets') or ():
        if isinstance(rule_set, Mapping):
            components.append(_component(artifact, 'allocation_rule_set', f"allocation_rule_set:{rule_set.get('slug')}", rule_set, 'allocation_rule_sets'))
    return tuple(components)


def _stamp_template_components(payload: Any, artifact: ArchitectureSourceArtifact) -> tuple[dict[str, Any], ...]:
    if not isinstance(payload, Mapping):
        raise ValueError('Stamp template payload must be a JSON object.')
    return (
        _component(
            artifact,
            'stamp_template',
            f"stamp_template:{payload.get('slug') or stable_digest(payload)[:16]}",
            dict(payload),
            'stamp_template',
        ),
    )


def _manual_components(payload: Any, artifact: ArchitectureSourceArtifact) -> tuple[dict[str, Any], ...]:
    if isinstance(payload, Mapping) and isinstance(payload.get('items'), list):
        items = payload.get('items') or []
    else:
        items = [payload]
    components = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            components.append(
                _component(
                    artifact,
                    'manual_note',
                    f'manual_note:{artifact.pk}:{index}',
                    {'value': item},
                    'manual_entry',
                    validation_status='warning',
                    validation_messages=[{'severity': 'warning', 'message': 'Manual architecture item is not a JSON object.'}],
                )
            )
            continue
        kind = str(item.get('kind') or 'manual_note')
        natural_key = str(item.get('natural_key') or item.get('identity') or item.get('slug') or item.get('name') or f'{artifact.pk}:{index}')
        components.append(_component(artifact, kind, f'{kind}:{natural_key}', dict(item), 'manual_entry', source_row=index))
    return tuple(components)


def _component(
    artifact: ArchitectureSourceArtifact,
    kind: str,
    natural_key: str,
    desired_state: Mapping[str, Any],
    source_field: str,
    *,
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
            'source_system': 'architecture_workspace',
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


def _looks_like_blueprint_bundle(payload: Mapping[str, Any]) -> bool:
    return any(key in payload for key in ('definition', 'architecture', 'blueprint', 'stamp_templates', 'templates'))


def _looks_like_architecture_definition(payload: Mapping[str, Any]) -> bool:
    return {'slug', 'version', 'roles', 'transfer_patterns', 'allocation_rule_sets'}.issubset(payload.keys())


def _definition_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ('definition', 'architecture', 'blueprint'):
        value = payload.get(key)
        if isinstance(value, Mapping):
            if key == 'blueprint' and isinstance(value.get('definition'), Mapping):
                return value['definition']
            return value
    if _looks_like_architecture_definition(payload):
        return payload
    raise ValueError('Blueprint bundle did not include an architecture definition.')


def _blueprint_import_item(payload: Mapping[str, Any]) -> dict[str, Any]:
    definition = _definition_payload(payload)
    item = {
        'kind': 'fabric_architecture_blueprint',
        'definition': dict(definition),
        'parameter_schema': payload.get('parameter_schema') or definition.get('parameter_schema') or {},
        'required_device_types': payload.get('required_device_types') or definition.get('required_device_types') or {},
        'stamp_templates': payload.get('stamp_templates') or payload.get('templates') or {},
        'schema_contract_version': payload.get('schema_contract_version') or ARCHITECTURE_SCHEMA_CONTRACT_VERSION,
        'bundle': {
            'bundle_version': payload.get('bundle_version') or '',
            'bundle_author': payload.get('bundle_author') or '',
            'schema_contract_version': payload.get('schema_contract_version') or ARCHITECTURE_SCHEMA_CONTRACT_VERSION,
        },
    }
    for key in ('name', 'description', 'status', 'lifecycle', 'successor_version', 'metadata'):
        if key in payload:
            item[key] = payload[key]
    return item


def _stamp_templates(payload: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    raw_templates = payload.get('stamp_templates') or payload.get('templates') or {}
    if isinstance(raw_templates, Mapping):
        iterable = raw_templates.items()
    elif isinstance(raw_templates, list):
        iterable = ((None, item) for item in raw_templates)
    else:
        return ()
    templates = []
    for key, value in iterable:
        if not isinstance(value, Mapping):
            continue
        template = dict(value)
        template.setdefault('slug', key or template.get('slug') or stable_digest(template)[:16])
        templates.append(template)
    return tuple(templates)
