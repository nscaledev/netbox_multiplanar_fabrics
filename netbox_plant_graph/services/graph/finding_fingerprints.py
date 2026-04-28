import hashlib
import json


SEMANTIC_METADATA_KEYS = {
    'cross_plane_fine_edge': ('left_plane_ids', 'right_plane_ids', 'plane_pair_ids', 'rule_id', 'contamination_domain_key'),
    'incomplete_child_interface_set': ('actual_child_count', 'cable_id', 'expected_child_count', 'profile'),
    'missing_cable_profile': ('profile',),
    'missing_child_interface': ('cable_id', 'position_count', 'profile'),
    'missing_port_mapping': ('cable_id', 'mapping_side', 'position'),
    'multi_plane_attachment': ('plane_numbers',),
    'partial_profile_mapping': (
        'first_unresolved_detail',
        'first_unresolved_position',
        'first_unresolved_reason',
        'matched_positions',
        'profile',
        'unresolved_positions',
    ),
    'plane_underpopulated': (),
    'shared_passive_artifact': ('plane_ids', 'plane_pair_ids', 'rule_id', 'contamination_domain_key'),
    'unresolved_profile_mapping': (
        'first_unresolved_detail',
        'first_unresolved_position',
        'first_unresolved_reason',
        'matched_positions',
        'profile',
        'unresolved_positions',
    ),
}


def _normalize_value(value):
    if isinstance(value, dict):
        return {key: _normalize_value(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    return value


def _semantic_metadata(finding: dict) -> dict:
    metadata = finding.get('metadata') or {}
    keys = SEMANTIC_METADATA_KEYS.get(finding.get('finding_type'))
    if keys is None:
        return _normalize_value(metadata)
    return {
        key: _normalize_value(metadata[key])
        for key in keys
        if key in metadata
    }


def build_audit_finding_fingerprint(finding: dict) -> str:
    obj = finding.get('object') or {}
    payload = {
        'finding_type': finding.get('finding_type'),
        'object': {
            'app_label': obj.get('app_label'),
            'model': obj.get('model'),
            'pk': obj.get('pk'),
            'registry_key': obj.get('registry_key'),
        },
        'semantic_metadata': _semantic_metadata(finding),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()
