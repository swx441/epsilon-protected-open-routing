"""Exact small-instance solver for the primary weighted-latency objective.

The dynamic program is intentionally limited to small instances.  For each
visited subset and final node, an earlier service-start time dominates a later
one with the same subset/final node because all remaining travel and service
times are non-negative.  This gives a reproducible exact reference for the
primary objective without pretending that the large-instance heuristic is an
exact solver.
"""

from __future__ import annotations

from math import inf
from typing import Sequence

import numpy as np


def solve_exact_weighted_latency(instance) -> list[int]:
    """Return an exact route for small instances, minimizing ``J_WL``.

    The start node is fixed.  Soft time-window tardiness is reported by the
    common evaluator but is not part of this primary-objective exact oracle.
    Use only for correctness/gap experiments, normally at most 16 nodes.
    """
    n = instance.node_count
    start = instance.start_node
    if n <= 1:
        return [start]
    if n > 20:
        raise ValueError("exact solver is limited to at most 20 nodes")

    service_nodes = [node for node in range(n) if node != start]
    position = {node: idx for idx, node in enumerate(service_nodes)}
    full_mask = (1 << len(service_nodes)) - 1
    # Multiple labels are required: a path with an earlier final arrival can
    # have a larger accumulated weighted latency than a later path for the
    # same subset/final node.  Keep only non-dominated (arrival, objective)
    # labels so the oracle remains exact while staying practical for n <= 16.
    states: dict[tuple[int, int], list[tuple[float, float, tuple[int, ...]]]] = {}

    def add_label(key, candidate) -> None:
        labels = states.setdefault(key, [])
        arrival, objective, path = candidate
        if any(old[0] <= arrival + 1e-12 and old[1] <= objective + 1e-12
               for old in labels):
            return
        labels[:] = [old for old in labels
                     if not (arrival <= old[0] + 1e-12 and objective <= old[1] + 1e-12)]
        labels.append(candidate)
    for node in service_nodes:
        idx = 1 << position[node]
        arrival = max(float(instance.time_windows[node, 0]),
                      float(instance.cost_matrix[start, node]))
        weighted = float(instance.urgency[node]) * arrival
        add_label((idx, node), (arrival, weighted, (start, node)))

    for mask_size in range(1, len(service_nodes) + 1):
        for mask in range(1, full_mask + 1):
            if bin(mask).count("1") != mask_size:
                continue
            for last in service_nodes:
                key = (mask, last)
                if key not in states:
                    continue
                for current_time, objective, path in list(states[key]):
                    completion = current_time + float(instance.service_times[last])
                    for node in service_nodes:
                        bit = 1 << position[node]
                        if mask & bit:
                            continue
                        arrival = max(float(instance.time_windows[node, 0]),
                                      completion + float(instance.cost_matrix[last, node]))
                        candidate = (arrival,
                                     objective + float(instance.urgency[node]) * arrival,
                                     path + (node,))
                        add_label((mask | bit, node), candidate)

    candidates = [label for node in service_nodes for label in states[(full_mask, node)]]
    _, _, route = min(candidates, key=lambda item: (item[1], item[0], item[2]))
    return list(route)
