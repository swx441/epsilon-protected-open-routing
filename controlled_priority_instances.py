"""Connected controlled road graphs for the IET static open-service protocol."""

from __future__ import annotations

import heapq
import numpy as np

from delivery_routing import DeliveryInstance, urgency_from_components


def _grid_matrices(rows: int, cols: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    n = rows * cols
    distance_graph = [[] for _ in range(n)]
    travel_graph = [[] for _ in range(n)]
    for r in range(rows):
        for c in range(cols):
            u = r * cols + c
            for dr, dc in ((1, 0), (0, 1)):
                rr, cc = r + dr, c + dc
                if rr < rows and cc < cols:
                    v = rr * cols + cc
                    distance = float(rng.integers(80, 141))
                    congestion = float(rng.uniform(1.02, 1.25))
                    distance_graph[u].append((v, distance)); distance_graph[v].append((u, distance))
                    travel = distance * congestion
                    travel_graph[u].append((v, travel)); travel_graph[v].append((u, travel))

    def all_pairs(graph: list[list[tuple[int, float]]]) -> np.ndarray:
        result = np.zeros((n, n), dtype=float)
        for source in range(n):
            values = [float("inf")] * n; values[source] = 0.0
            queue = [(0.0, source)]
            while queue:
                distance, node = heapq.heappop(queue)
                if distance != values[node]:
                    continue
                for neighbour, weight in graph[node]:
                    updated = distance + weight
                    if updated < values[neighbour]:
                        values[neighbour] = updated
                        heapq.heappush(queue, (updated, neighbour))
            result[source] = values
        return result

    return all_pairs(distance_graph), all_pairs(travel_graph)


def make_instance(node_count: int = 24, scenario: str = "routine", seed: int = 0,
                  gamma: float = 1.0, lambdas: tuple[float, float, float] = (0.20, 0.60, 0.20)) -> DeliveryInstance:
    if node_count == 8:
        rows, cols = 2, 4
    elif node_count == 12:
        rows, cols = 3, 4
    elif node_count == 16:
        rows, cols = 4, 4
    elif node_count == 24:
        rows, cols = 4, 6
    elif node_count == 48:
        rows, cols = 6, 8
    elif node_count == 80:
        rows, cols = 8, 10
    elif node_count == 120:
        rows, cols = 10, 12
    else:
        raise ValueError("controlled protocol supports 8, 12, 16, 24, 48, 80, or 120 nodes")
    distances, costs = _grid_matrices(rows, cols, seed)
    rng = np.random.default_rng(seed + 7919)
    n = node_count
    service = rng.integers(20, 61, size=n).astype(float)
    service[0] = 0.0
    horizon = float(costs.max() * 1.15 + service.sum())
    if scenario == "routine":
        early = np.zeros(n)
        late = np.full(n, horizon)
        late[1:] = rng.uniform(horizon * 0.48, horizon * 0.90, size=n - 1)
        waiting = rng.uniform(0, 120, size=n)
        slack = np.maximum(late - costs[0], 1.0)
        priority = rng.uniform(0.1, 1.0, size=n)
    elif scenario == "tight_time_windows":
        early = np.zeros(n)
        late = rng.uniform(horizon * 0.28, horizon * 0.58, size=n)
        waiting = rng.uniform(0, 240, size=n)
        slack = np.maximum(late - costs[0], 1.0)
        priority = rng.uniform(0.1, 1.0, size=n)
    elif scenario == "concentrated_urgency":
        early = np.zeros(n)
        late = np.full(n, horizon * 0.85)
        waiting = np.zeros(n)
        slack = np.full(n, horizon * 0.5)
        priority = np.full(n, 0.2)
        hot = np.arange(1, min(n, 7))
        waiting[hot] = rng.uniform(180, 600, size=len(hot))
        slack[hot] = rng.uniform(20, 180, size=len(hot))
        priority[hot] = rng.uniform(0.8, 1.0, size=len(hot))
    elif scenario == "concentrated_random":
        # Same marginal attributes as concentrated_urgency, but the six
        # high-urgency nodes are drawn rather than fixed at indices 1..6.
        early = np.zeros(n)
        late = np.full(n, horizon * 0.85)
        waiting = np.zeros(n)
        slack = np.full(n, horizon * 0.5)
        priority = np.full(n, 0.2)
        hot = np.sort(rng.choice(np.arange(1, n), size=min(n - 1, 6), replace=False))
        waiting[hot] = rng.uniform(180, 600, size=len(hot))
        slack[hot] = rng.uniform(20, 180, size=len(hot))
        priority[hot] = rng.uniform(0.8, 1.0, size=len(hot))
    else:
        raise ValueError(f"unknown scenario: {scenario}")
    urgency = urgency_from_components(waiting, slack, priority, lambdas=lambdas, start_node=0, gamma=gamma)
    windows = np.column_stack([early, late])
    positions = [(float(index % cols), float(index // cols)) for index in range(n)]
    return DeliveryInstance(costs, urgency, windows, service, start_node=0,
                            route_id=f"controlled_{node_count}_{scenario}_{seed}",
                            distance_matrix=distances,
                            metadata={"network": "connected_rectilinear_grid", "scenario": scenario,
                                      "gamma": gamma, "waiting_time": waiting,
                                      "remaining_slack": slack, "service_priority": priority,
                                      "model_horizon": float(horizon),
                                      "grid_shape": (rows, cols),
                                      "positions": positions})


def scenarios() -> tuple[str, ...]:
    return ("routine", "tight_time_windows", "concentrated_urgency")
