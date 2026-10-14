"""Common data model and metrics for priority-aware open delivery routing.

The module deliberately keeps the data contract independent from any solver.
This makes controlled instances and external delivery data comparable without
claiming that a synthetic urgency variable is an observed operational fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np


@dataclass
class DeliveryInstance:
    """One open single-vehicle delivery instance.

    Node zero is the fixed start/depot node.  ``cost_matrix`` is interpreted
    as travel time in seconds by default; callers may use another consistent
    unit, recorded in ``cost_unit``.  The matrix is intentionally explicit so
    an external benchmark can provide its own travel-time matrix.
    """

    cost_matrix: np.ndarray
    urgency: np.ndarray
    time_windows: np.ndarray | None = None
    service_times: np.ndarray | None = None
    distance_matrix: np.ndarray | None = None
    start_node: int = 0
    route_id: str | None = None
    historical_route: list[int] | None = None
    release_times: np.ndarray | None = None
    cost_unit: str = "seconds"
    latency_scale: float | None = None
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.cost_matrix = np.asarray(self.cost_matrix, dtype=float)
        if self.cost_matrix.ndim != 2 or self.cost_matrix.shape[0] != self.cost_matrix.shape[1]:
            raise ValueError("cost_matrix must be a square matrix")
        n = self.cost_matrix.shape[0]
        self.urgency = np.asarray(self.urgency, dtype=float)
        if self.urgency.shape != (n,) or not np.all(np.isfinite(self.urgency)):
            raise ValueError("urgency must be a finite vector aligned with cost_matrix")
        if np.any(self.cost_matrix < 0) or not np.all(np.isfinite(self.cost_matrix)):
            raise ValueError("cost_matrix must contain finite non-negative values")
        if self.distance_matrix is None:
            self.distance_matrix = self.cost_matrix.copy()
        else:
            self.distance_matrix = np.asarray(self.distance_matrix, dtype=float)
            if self.distance_matrix.shape != self.cost_matrix.shape:
                raise ValueError("distance_matrix must have the same shape as cost_matrix")
            if np.any(self.distance_matrix < 0) or not np.all(np.isfinite(self.distance_matrix)):
                raise ValueError("distance_matrix must contain finite non-negative values")
        if not 0 <= int(self.start_node) < n:
            raise ValueError("start_node is outside cost_matrix")
        self.start_node = int(self.start_node)
        self.urgency = normalize_urgency(self.urgency, int(self.start_node))

        if self.time_windows is None:
            self.time_windows = np.zeros((n, 2), dtype=float)
            # Use an explicit finite horizon so all solver adapters can build
            # an integer time dimension even when no business time windows
            # are supplied.
            self.time_windows[:, 1] = max(float(self.cost_matrix.max()) * max(n, 1) * 2.0, 1.0)
        else:
            self.time_windows = np.asarray(self.time_windows, dtype=float)
            if self.time_windows.shape != (n, 2):
                raise ValueError("time_windows must have shape (n, 2)")
            if not np.all(np.isfinite(self.time_windows)):
                raise ValueError("time_windows must be finite")
            if np.any(self.time_windows[:, 0] > self.time_windows[:, 1]):
                raise ValueError("time window lower bound exceeds upper bound")

        if self.service_times is None:
            self.service_times = np.zeros(n, dtype=float)
        else:
            self.service_times = np.asarray(self.service_times, dtype=float)
            if self.service_times.shape != (n,) or not np.all(np.isfinite(self.service_times)):
                raise ValueError("service_times must be a finite vector aligned with cost_matrix")
            if np.any(self.service_times < 0):
                raise ValueError("service_times must be non-negative")

        if self.latency_scale is None:
            # A fixed instance-level scale keeps J_WL comparable across
            # algorithms. Do not normalise by each candidate route's own
            # completion time, which can hide a route-length penalty.
            self.latency_scale = max(
                float(np.max(self.cost_matrix)) * max(n - 1, 1)
                + float(np.sum(self.service_times)),
                1.0,
            )
        else:
            self.latency_scale = float(self.latency_scale)
            if not np.isfinite(self.latency_scale) or self.latency_scale <= 0:
                raise ValueError("latency_scale must be finite and positive")

        if self.release_times is not None:
            self.release_times = np.asarray(self.release_times, dtype=float)
            if self.release_times.shape != (n,) or not np.all(np.isfinite(self.release_times)):
                raise ValueError("release_times must be a finite vector aligned with cost_matrix")
            if np.any(self.release_times < 0):
                raise ValueError("release_times must be non-negative")

        if self.historical_route is not None:
            self.historical_route = [int(x) for x in self.historical_route]

    @property
    def node_count(self) -> int:
        return int(self.cost_matrix.shape[0])


def validate_route(route: Sequence[int], instance: DeliveryInstance) -> list[int]:
    route = [int(node) for node in route]
    expected = set(range(instance.node_count))
    if len(route) != instance.node_count or set(route) != expected:
        raise ValueError("route must visit every node exactly once")
    if route[0] != instance.start_node:
        raise ValueError("route must start at instance.start_node")
    return route


def reference_route(instance: DeliveryInstance) -> list[int]:
    """Return the deterministic travel-time nearest-neighbour reference.

    The reference is used only to freeze operating tolerances before comparing
    algorithms.  It does not use urgency and is therefore method-independent.
    """
    route = [instance.start_node]
    remaining = set(range(instance.node_count)) - {instance.start_node}
    while remaining:
        current = route[-1]
        node = min(remaining, key=lambda candidate: (
            float(instance.cost_matrix[current, candidate]), int(candidate)
        ))
        route.append(int(node))
        remaining.remove(node)
    return route


PROTOCOL_DIAGNOSTIC_FIELDS = (
    "amw_iterations",
    "amw_best_time_sec",
    "amw_improved_last_quartile",
    "L_ref",
    "model_horizon",
    "reference_horizon_nn",
)


def _model_horizon(instance: DeliveryInstance, reference_horizon_nn: float) -> float:
    """Return the instance-generation horizon H, distinct from the NN horizon H_NN."""
    cached = instance.metadata.get("model_horizon")
    if cached is not None:
        return max(float(cached), 1.0)
    windows = np.asarray(instance.time_windows, dtype=float)
    value = max(
        float(np.max(windows[:, 1])) if windows.size else 1.0,
        float(instance.latency_scale or 1.0),
        float(reference_horizon_nn),
        1.0,
    )
    instance.metadata["model_horizon"] = value
    return value


def operating_limits(instance: DeliveryInstance) -> dict[str, float]:
    """Freeze method-independent operational limits for the constrained model.

    Limits are derived from the deterministic efficiency reference, not from
    JWL-ALNS or any result-bearing route.  The 15% distance/travel tolerance
    and the tardiness tolerance (10% of reference tardiness plus 5% of the
    NN horizon H_NN) are protocol constants and must be reported with runs.

    ``model_horizon`` is the instance-generation horizon H.  ``reference_horizon_nn``
    is the NN completion time used inside the tardiness limit.  They are not
    the same quantity and must not be substituted for each other.
    """
    cached = instance.metadata.get("operating_limits")
    if (
        isinstance(cached, dict)
        and "L_ref" in cached
        and "reference_horizon_nn" in cached
        and "model_horizon" in cached
    ):
        return {str(key): float(value) for key, value in cached.items()}
    route = reference_route(instance)
    metrics = evaluate_route(route, instance)
    horizon_nn = max(float(metrics["arrival_time"]), 1.0)
    model_h = _model_horizon(instance, horizon_nn)
    l_ref = max(float(metrics["total_distance"]), 1e-9)
    limits = {
        "reference_distance": float(metrics["total_distance"]),
        "reference_travel_time": float(metrics["total_travel_time"]),
        "reference_late_minutes": float(metrics["late_minutes"]),
        "distance_limit": 1.15 * float(metrics["total_distance"]),
        "travel_time_limit": 1.15 * float(metrics["total_travel_time"]),
        "late_minutes_limit": float(metrics["late_minutes"]) * 1.10 + 0.05 * horizon_nn / 60.0,
        "L_ref": float(l_ref),
        "model_horizon": float(model_h),
        "reference_horizon_nn": float(horizon_nn),
    }
    instance.metadata["operating_limits"] = limits
    instance.metadata["L_ref"] = float(l_ref)
    instance.metadata["reference_horizon_nn"] = float(horizon_nn)
    instance.metadata["model_horizon"] = float(model_h)
    return limits


def protocol_diagnostic_fields(instance: DeliveryInstance, algorithm: str | None = None) -> dict[str, object]:
    """Scale and anytime columns shared by every formal CSV writer."""
    limits = operating_limits(instance)
    row: dict[str, object] = {
        "L_ref": float(limits["L_ref"]),
        "model_horizon": float(limits["model_horizon"]),
        "reference_horizon_nn": float(limits["reference_horizon_nn"]),
        "amw_iterations": "",
        "amw_best_time_sec": "",
        "amw_improved_last_quartile": "",
    }
    is_amw = algorithm is None or "AMW-MOPP" in str(algorithm)
    trace = instance.metadata.get("amw_search_trace")
    if is_amw and isinstance(trace, dict) and trace:
        if "amw_iterations" in trace:
            row["amw_iterations"] = int(trace["amw_iterations"])
        if "amw_best_time_sec" in trace:
            row["amw_best_time_sec"] = float(trace["amw_best_time_sec"])
        if "amw_improved_last_quartile" in trace:
            row["amw_improved_last_quartile"] = bool(trace["amw_improved_last_quartile"])
    return row


def constraint_summary(metrics: dict[str, float | int | bool], instance: DeliveryInstance) -> dict[str, float | bool]:
    """Compute normalized violation and feasibility under frozen limits."""
    limits = operating_limits(instance)
    distance_violation = max(0.0, float(metrics["total_distance"]) - limits["distance_limit"])
    travel_violation = max(0.0, float(metrics["total_travel_time"]) - limits["travel_time_limit"])
    late_violation = max(0.0, float(metrics["late_minutes"]) - limits["late_minutes_limit"])
    normalized = (
        distance_violation / max(limits["distance_limit"], 1e-9)
        + travel_violation / max(limits["travel_time_limit"], 1e-9)
        + late_violation / max(limits["late_minutes_limit"], 1e-9)
    )
    return {
        "distance_limit": float(limits["distance_limit"]),
        "travel_time_limit": float(limits["travel_time_limit"]),
        "late_minutes_limit": float(limits["late_minutes_limit"]),
        "distance_violation": float(distance_violation),
        "travel_time_violation": float(travel_violation),
        "late_minutes_violation": float(late_violation),
        "constraint_violation": float(normalized),
        "operationally_feasible": bool(normalized <= 1e-12),
    }


def normalize_urgency(urgency: Sequence[float], start_node: int = 0) -> np.ndarray:
    """Set depot urgency to zero and scale service-node mean urgency to one.

    The scaling preserves every within-instance priority ordering and makes
    the JWL-ALNS candidate score invariant to a seconds/minutes conversion or an
    externally supplied common multiplier.
    """
    values = np.maximum(np.asarray(urgency, dtype=float).copy(), 0.0)
    if not 0 <= int(start_node) < len(values):
        raise ValueError("start_node is outside urgency")
    values[int(start_node)] = 0.0
    service_values = np.delete(values, int(start_node))
    mean_value = float(np.mean(service_values)) if len(service_values) else 0.0
    if mean_value <= 1e-12 and len(service_values):
        values[:] = 1.0
        values[int(start_node)] = 0.0
        mean_value = 1.0
    return values / max(mean_value, 1e-12)


def _minmax(values: Sequence[float], inverse: bool = False, exclude_index: int | None = None) -> np.ndarray:
    """Return a bounded, deterministic component score for urgency mapping."""
    raw = np.asarray(values, dtype=float)
    if not np.all(np.isfinite(raw)):
        raise ValueError("urgency components must be finite")
    if inverse:
        raw = 1.0 / np.maximum(raw, 1e-6)
    reference = np.delete(raw, int(exclude_index)) if exclude_index is not None else raw
    low, high = float(np.min(reference)), float(np.max(reference))
    if high - low <= 1e-12:
        return np.zeros_like(raw)
    return (raw - low) / (high - low)


def urgency_from_components(
    waiting_time: Sequence[float],
    remaining_slack: Sequence[float],
    service_priority: Sequence[float],
    lambdas: tuple[float, float, float] = (0.20, 0.60, 0.20),
    start_node: int = 0,
    gamma: float = 1.0,
) -> np.ndarray:
    """Compute the pre-registered priority score used by this study.

    ``q_i = lambda_1 * norm(wait_i) + lambda_2 * norm(1/slack_i) +
    lambda_3 * norm(priority_i)``.  Components are normalized within an
    instance then the final values are mean-normalized over service nodes.
    Missing operational fields must be represented by an explicit neutral
    vector, never inferred from a solver output.
    """
    if len(lambdas) != 3 or any(value < 0 for value in lambdas) or sum(lambdas) <= 0:
        raise ValueError("lambdas must be three non-negative values with positive sum")
    if gamma <= 0 or not np.isfinite(gamma):
        raise ValueError("gamma must be finite and positive")
    waiting = _minmax(waiting_time, exclude_index=start_node)
    slack_inverse = _minmax(
        np.power(np.maximum(np.asarray(remaining_slack, dtype=float), 1e-6), -float(gamma)),
        exclude_index=start_node,
    )
    priority = _minmax(service_priority, exclude_index=start_node)
    if not (waiting.shape == slack_inverse.shape == priority.shape):
        raise ValueError("urgency components must have the same shape")
    q = lambdas[0] * waiting + lambdas[1] * slack_inverse + lambdas[2] * priority
    # A neutral component vector still needs a valid equal-priority baseline.
    if np.all(q <= 1e-12):
        q = np.ones_like(q)
    return normalize_urgency(q, start_node)


def _normalised_weighted_latency(
    arrivals: np.ndarray, urgency: np.ndarray, scale: float | None = None
) -> float:
    n = len(arrivals)
    if n <= 1:
        return 0.0
    weights = np.asarray(urgency, dtype=float).copy()
    weights[0] = 0.0
    if float(weights.sum()) <= 1e-12:
        weights[1:] = 1.0
    scale = max(float(scale if scale is not None else np.max(arrivals[1:])), 1.0)
    return float(np.dot(weights, arrivals) / max(float(weights.sum()) * scale, 1e-12))


def evaluate_route(route: Sequence[int], instance: DeliveryInstance) -> dict[str, float | int | bool]:
    """Evaluate an open route with waiting, soft time-window tardiness and J_WL."""
    route = validate_route(route, instance)
    n = instance.node_count
    arrivals = np.zeros(n, dtype=float)
    departures = np.zeros(n, dtype=float)
    current_time = 0.0
    total_distance = 0.0
    total_travel = 0.0
    tardiness = np.zeros(n, dtype=float)

    for index, node in enumerate(route):
        if index:
            previous = route[index - 1]
            travel = float(instance.cost_matrix[previous, node])
            total_distance += float(instance.distance_matrix[previous, node])
            total_travel += travel
            current_time += travel
        early, late = instance.time_windows[node]
        current_time = max(current_time, float(early))
        arrivals[node] = current_time
        tardiness[node] = max(0.0, current_time - float(late))
        current_time += float(instance.service_times[node])
        departures[node] = current_time

    order_position = np.zeros(n, dtype=float)
    for position, node in enumerate(route):
        order_position[node] = position
    priority_latency = _normalised_weighted_latency(
        arrivals, instance.urgency, instance.latency_scale
    )
    rank_latency = _normalised_weighted_latency(
        order_position, instance.urgency, max(float(n - 1), 1.0)
    )
    non_depot = np.arange(1, n)
    top_count = max(1, int(np.ceil(len(non_depot) * 0.25))) if len(non_depot) else 0
    ranked_nodes = sorted(non_depot.tolist(), key=lambda node: (-float(instance.urgency[node]), int(node)))
    top_nodes = np.asarray(ranked_nodes[:top_count], dtype=int) if top_count else []
    # Count the first half of service positions consistently with the paper:
    # positions 1..ceil((n-1)/2), excluding the fixed depot position 0.
    first_half_limit = max(1, int(np.ceil((n - 1) / 2.0)))
    first_half_coverage = float(np.mean(order_position[top_nodes] <= first_half_limit)) if len(top_nodes) else 1.0
    return {
        "J_WL": priority_latency,
        "weighted_rank_latency": rank_latency,
        "total_distance": total_distance,
        "total_travel_time": total_travel,
        "arrival_time": float(current_time),
        "latency_scale": float(instance.latency_scale),
        "late_minutes": float(tardiness.sum() / 60.0),
        "mean_late_minutes": float(tardiness[non_depot].mean() / 60.0) if len(non_depot) else 0.0,
        "late_rate": float(np.mean(tardiness[non_depot] > 0.0)) if len(non_depot) else 0.0,
        "first_half_top_quartile_coverage": first_half_coverage,
        "top_quartile_mean_service_position": float(order_position[top_nodes].mean()) if len(top_nodes) else 0.0,
        "max_tardiness": float(tardiness.max()) if n else 0.0,
        "completed": True,
        "route_length_nodes": n,
    }


def common_objective(
    route: Sequence[int],
    instance: DeliveryInstance,
    alpha: float = 0.5,
    beta: float = 1.0,
) -> float:
    """Fixed cross-solver objective used for the priority-fair comparison.

    ``alpha`` and ``beta`` must be fixed before looking at results.  Distance
    is normalised by a nearest-neighbour reference and tardiness by the route
    horizon, avoiding a hidden unit-dependent trade-off.
    """
    if not 0 <= alpha <= 1 or beta < 0:
        raise ValueError("alpha must be in [0, 1] and beta must be non-negative")
    metrics = evaluate_route(route, instance)
    reference = [instance.start_node]
    remaining = set(range(instance.node_count)) - {instance.start_node}
    while remaining:
        node = min(remaining, key=lambda x: instance.cost_matrix[reference[-1], x])
        reference.append(int(node))
        remaining.remove(node)
    ref_distance = max(float(evaluate_route(reference, instance)["total_distance"]), 1e-9)
    horizon = max(float(metrics["arrival_time"]), 1.0)
    distance_term = float(metrics["total_distance"]) / ref_distance
    late_term = float(metrics["late_minutes"]) * 60.0 / horizon
    return float(alpha * float(metrics["J_WL"]) + (1.0 - alpha) * distance_term + beta * late_term)


def urgency_from_slack(slack: Sequence[float], gamma: float = 1.0, epsilon: float = 1e-3) -> np.ndarray:
    """Monotone urgency mapping used for delivery data and controlled proxies."""
    values = np.asarray(slack, dtype=float)
    if gamma <= 0 or epsilon <= 0 or not np.all(np.isfinite(values)):
        raise ValueError("slack must be finite and gamma/epsilon must be positive")
    return normalize_urgency(np.power(np.maximum(values, 0.0) + epsilon, -float(gamma)))
