from .extractor import extract_source_bundle
from .graph_builder import build_graph
from .transformer import transform_source_bundle


def rebuild_graph(*, scope=None, dry_run: bool = False) -> dict[str, int]:
    bundle = extract_source_bundle(scope=scope)
    graph_inputs = transform_source_bundle(bundle)
    return build_graph(graph_inputs, dry_run=dry_run)
