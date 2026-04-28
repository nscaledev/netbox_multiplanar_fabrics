from .lane_sets import build_lane_set
from .payloads import LaneAllocationSummaryPayload


def build_lane_allocation_summary(*, target) -> LaneAllocationSummaryPayload:
    lane_set = build_lane_set(target)
    incomplete_attachment_units = sum(
        1 for member in lane_set.attachment_units if member.status not in {'complete', 'empty'}
    )
    return LaneAllocationSummaryPayload(
        scope_kind=lane_set.scope_kind,
        target=lane_set.target,
        expected_lane_total=lane_set.expected_lane_total,
        present_lane_total=lane_set.present_lane_total,
        mapped_lane_total=lane_set.mapped_lane_total,
        missing_lane_total=lane_set.missing_lane_total,
        unmatched_peer_positions=lane_set.unmatched_peer_positions,
        incomplete_attachment_units=incomplete_attachment_units,
        lane_map_consistency=lane_set.lane_map_consistency,
        plane_consistency=lane_set.plane_consistency,
    )
