from collections import deque


def breadth_first_walk(start, neighbor_fn, *, max_depth: int = 32):
    queue = deque([(start, 0)])
    seen = {start}
    while queue:
        node, depth = queue.popleft()
        yield node, depth
        if depth >= max_depth:
            continue
        for neighbor in neighbor_fn(node):
            if neighbor in seen:
                continue
            seen.add(neighbor)
            queue.append((neighbor, depth + 1))
