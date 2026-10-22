"""Solver adapters for the JWL-ALNS comparison set.

All adapters solve the same open single-vehicle service-order problem: service
node zero at time zero, visit every remaining service node exactly once, and
finish at the final service node. Road costs are supplied as one common
traffic-adjusted shortest-path matrix by the experiment runner.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version as package_version
from time import perf_counter
from typing import Iterable, Sequence

import numpy as np

# Strong external solvers remain native comparisons.  They are optional at
# development time; a missing package is recorded as unavailable instead of
# silently substituting a different heuristic under the same label.
try:
    from alns import ALNS
    from alns.accept import HillClimbing
    from alns.select import RandomSelect
    from alns.stop import MaxRuntime as ALNSMaxRuntime
    HAS_ALNS = True
except ImportError:
    HAS_ALNS = False
try:
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    HAS_ORTOOLS = True
except ImportError:
    HAS_ORTOOLS = False
try:
    from pyvrp import Model
    from pyvrp.stop import MaxRuntime as PyVRPMaxRuntime
    HAS_PYVRP = True
except ImportError:
    HAS_PYVRP = False


ALGO_PYVRP_HGS = "PyVRP-HGS open-route solver"
ALGO_ALNS = "Native ALNS"
ALGO_ALNS_STATIC = "Randomized Local Search comparator"
ALGO_ORTOOLS_GLS = "OR-Tools GLS open-route solver"
ALGO_PRIORITY_GREEDY = "Priority-distance greedy heuristic"
ALGO_DISTANCE_BASELINE = "Travel-time nearest-neighbour baseline"
ALGO_ORTOOLS_SVPTW = "OR-Tools Open-SVRPTW"
ALGO_PYVRP_SVPTW = "PyVRP-HGS Open-SVRPTW"
ALGO_AMW_DELIVERY = "AMW-MOPP priority-aware open path"
ALGO_DYNAMIC_ALNS = "DEPRECATED dynamic ALNS (not in static protocol)"
ALGO_WEIGHTED_MLP_ILS = "Weighted Minimum-Latency ILS"
ALGO_JWL_ALNS = "JWL-ALNS"
ALGO_HISTORICAL_REPLAY = "Historical driver route replay"


class SolverUnavailable(RuntimeError):
    """Raised when a registered external baseline is not installed."""


def _rng_index(rng, high: int) -> int:
    """Draw one integer across NumPy Generator and legacy RandomState APIs."""
    if hasattr(rng, "integers"):
        return int(rng.integers(high))
    return int(rng.randint(high))


def _solver_version(name: str) -> str:
    package = ""
    if "OR-Tools" in name:
        package = "ortools"
    elif "PyVRP" in name:
        package = "pyvrp"
    elif "ALNS" in name:
        package = "alns"
    if not package:
        return ""
    try:
        return package_version(package)
    except PackageNotFoundError:
        return ""


def _validate_inputs(cost_matrix: np.ndarray, start_node: int) -> np.ndarray:
    costs = np.asarray(cost_matrix, dtype=float)
    if costs.ndim != 2 or costs.shape[0] != costs.shape[1]:
        raise ValueError("cost_matrix must be a square matrix.")
    if not 0 <= start_node < len(costs):
        raise ValueError("start_node is outside the cost matrix.")
    if not np.all(np.isfinite(costs)):
        raise ValueError("All service-node pairs must be connected by finite road cost.")
    if np.any(costs < 0):
        raise ValueError("Road costs must be non-negative.")
    return costs


def validate_open_route(route: Sequence[int], node_count: int, start_node: int = 0) -> list[int]:
    route = [int(node) for node in route]
    expected = set(range(node_count))
    if len(route) != node_count or set(route) != expected:
        raise ValueError("Adapter did not return every service node exactly once.")
    if not route or route[0] != start_node:
        raise ValueError("Adapter route must begin at the registered start node.")
    return route


def _integer_cost(value: float) -> int:
    return max(1, int(round(float(value) * 1000)))


def solve_pyvrp_hgs_open_route(
    cost_matrix: np.ndarray,
    positions: Sequence[Sequence[float]],
    seed: int,
    time_limit_sec: float,
    start_node: int = 0,
) -> list[int]:
    """Use PyVRP's HGS implementation with a zero-cost terminal depot.

    Node zero is the initial serviced node, so it is represented by the start
    depot rather than a second client. A distinct terminal depot lets the
    vehicle end at the final service node rather than returning to the start.
    """
    if not HAS_PYVRP:
        raise SolverUnavailable("PyVRP is not installed; install the registered PyVRP version to run this baseline.")
    costs = _validate_inputs(cost_matrix, start_node)
    n_nodes = len(costs)
    if n_nodes == 1:
        return [start_node]
    if len(positions) != n_nodes:
        raise ValueError("positions and cost_matrix have different node counts.")

    model = Model()
    start_xy = positions[start_node]
    start_depot = model.add_depot(float(start_xy[0]), float(start_xy[1]), name="start")
    end_depot = model.add_depot(float(start_xy[0]), float(start_xy[1]), name="open_end")
    client_nodes = [node for node in range(n_nodes) if node != start_node]
    clients = [
        model.add_client(
            float(positions[node][0]),
            float(positions[node][1]),
            delivery=1,
            name=f"bin_{node + 1}",
        )
        for node in client_nodes
    ]
    locations = [start_depot, end_depot, *clients]
    location_nodes = {id(start_depot): start_node, id(end_depot): start_node}
    location_nodes.update({id(client): node for client, node in zip(clients, client_nodes)})

    for frm in locations:
        for to in locations:
            if frm is to:
                continue
            if to is end_depot:
                distance = 0
            else:
                distance = _integer_cost(costs[location_nodes[id(frm)], location_nodes[id(to)]])
            model.add_edge(frm, to, distance=distance)

    model.add_vehicle_type(
        num_available=1,
        capacity=len(client_nodes),
        start_depot=start_depot,
        end_depot=end_depot,
        name="single_open_route_vehicle",
    )
    result = model.solve(
        PyVRPMaxRuntime(max(float(time_limit_sec), 0.05)),
        seed=int(seed),
        collect_stats=True,
        display=False,
    )
    if not result.is_feasible():
        raise RuntimeError("PyVRP-HGS did not produce a feasible route.")
    routes = list(result.best.routes())
    if len(routes) != 1:
        raise RuntimeError("PyVRP-HGS returned an unexpected number of routes.")
    visit_locations = list(routes[0].visits())
    route = [start_node] + [client_nodes[int(location) - 2] for location in visit_locations]
    return validate_open_route(route, n_nodes, start_node)


@dataclass(frozen=True)
class SolverResult:
    """Uniform result contract shared by every baseline adapter."""

    algorithm: str
    route: list[int] | None
    runtime_sec: float
    status: str
    error_message: str = ""
    solver_version: str = ""

    def __iter__(self):
        # Backward-compatible tuple unpacking for existing experiment runners.
        yield self.algorithm
        yield self.route
        yield self.runtime_sec
        yield self.status

    def __getitem__(self, item):
        values = (self.algorithm, self.route, self.runtime_sec, self.status)
        return values[item]


@dataclass(frozen=True)
class _ALNSRouteState:
    route: tuple[int, ...]
    unassigned: tuple[int, ...]
    costs: np.ndarray

    def objective(self) -> float:
        if len(self.route) < 2:
            return 0.0
        return float(sum(self.costs[a, b] for a, b in zip(self.route, self.route[1:])))


@dataclass(frozen=True)
class _JwlALNSState:
    """Open route scored by the same first key as weighted-latency local search.

    The ``alns`` library minimises ``objective`` and never sees the instance.
    ``context`` is the delivery instance used to evaluate ``J_WL``, tardiness,
    and distance. Travel time is not the acceptance criterion.
    """

    route: tuple[int, ...]
    unassigned: tuple[int, ...]
    context: object

    def objective(self) -> float:
        if self.unassigned or len(self.route) != len(self.context.cost_matrix):
            return float("inf")
        key = _metrics_key(list(self.route), self.context)
        # Scalar encoding of (J_WL, tardiness, distance). The gaps are large, but
        # a small worsening of the primary key can still be offset by a secondary key.
        return key[0] * 1e12 + key[1] * 1e3 + key[2]


def _nearest_neighbour_route(costs: np.ndarray, start_node: int) -> list[int]:
    route = [start_node]
    remaining = set(range(len(costs))) - {start_node}
    while remaining:
        current = route[-1]
        node = min(remaining, key=lambda candidate: (costs[current, candidate], candidate))
        route.append(node)
        remaining.remove(node)
    return route


def _open_route_random_removal(state, rng):
    """Remove about one fifth of the non-depot nodes from an open route."""
    if len(state.route) <= 2:
        return state
    count = min(len(state.route) - 1, max(1, (len(state.route) - 1) // 5))
    positions = rng.choice(np.arange(1, len(state.route)), size=count, replace=False)
    removed_positions = {int(position) for position in positions}
    removed = tuple(node for index, node in enumerate(state.route) if index in removed_positions)
    kept = tuple(node for index, node in enumerate(state.route) if index not in removed_positions)
    return type(state)(kept, state.unassigned + removed, state.costs if hasattr(state, "costs") else state.context)


def _open_route_worst_removal(state, rng):
    """Remove the non-depot nodes with the largest travel-time detour."""
    del rng
    if len(state.route) <= 2:
        return state
    matrix = state.costs if hasattr(state, "costs") else state.context.cost_matrix
    contributions = []
    for index in range(1, len(state.route)):
        previous = state.route[index - 1]
        current = state.route[index]
        next_cost = matrix[current, state.route[index + 1]] if index + 1 < len(state.route) else 0.0
        bypass_cost = matrix[previous, state.route[index + 1]] if index + 1 < len(state.route) else 0.0
        contributions.append((matrix[previous, current] + next_cost - bypass_cost, index))
    count = min(len(contributions), max(1, len(contributions) // 5))
    positions = {index for _, index in sorted(contributions, reverse=True)[:count]}
    removed = tuple(node for index, node in enumerate(state.route) if index in positions)
    kept = tuple(node for index, node in enumerate(state.route) if index not in positions)
    return type(state)(kept, state.unassigned + removed, state.costs if hasattr(state, "costs") else state.context)


def _open_route_greedy_repair(state, rng):
    """Reinsert removed nodes at the cheapest open-route travel-time position."""
    matrix = state.costs if hasattr(state, "costs") else state.context.cost_matrix
    route = list(state.route)
    unassigned = list(state.unassigned)
    while unassigned:
        node = unassigned.pop(_rng_index(rng, len(unassigned)))
        best_position, best_delta = 1, float("inf")
        for position in range(1, len(route) + 1):
            previous = route[position - 1]
            if position == len(route):
                delta = matrix[previous, node]
            else:
                following = route[position]
                delta = matrix[previous, node] + matrix[node, following] - matrix[previous, following]
            if delta < best_delta:
                best_delta = float(delta)
                best_position = position
        route.insert(best_position, node)
    return type(state)(tuple(route), (), state.costs if hasattr(state, "costs") else state.context)


def _alns_random_removal(state: _ALNSRouteState, rng: np.random.Generator) -> _ALNSRouteState:
    return _open_route_random_removal(state, rng)


def _alns_worst_removal(state: _ALNSRouteState, rng: np.random.Generator) -> _ALNSRouteState:
    return _open_route_worst_removal(state, rng)


def _alns_greedy_repair(state: _ALNSRouteState, rng: np.random.Generator) -> _ALNSRouteState:
    return _open_route_greedy_repair(state, rng)


def solve_alns_open_route(
    cost_matrix: np.ndarray,
    seed: int,
    time_limit_sec: float,
    start_node: int = 0,
) -> list[int]:
    """Run a standard destroy/repair ALNS on the open route ordering task."""
    if not HAS_ALNS:
        raise SolverUnavailable("ALNS is not installed; install the registered ALNS version to run this baseline.")
    costs = _validate_inputs(cost_matrix, start_node)
    if len(costs) == 1:
        return [start_node]
    # alns 6.x expects a legacy RandomState (it calls ``randint`` internally).
    rng = np.random.RandomState(int(seed))
    initial = _ALNSRouteState(tuple(_nearest_neighbour_route(costs, start_node)), (), costs)
    solver = ALNS(rng)
    solver.add_destroy_operator(_alns_random_removal, name="random_removal")
    solver.add_destroy_operator(_alns_worst_removal, name="worst_removal")
    solver.add_repair_operator(_alns_greedy_repair, name="greedy_insertion")
    result = solver.iterate(
        initial,
        RandomSelect(num_destroy=2, num_repair=1),
        HillClimbing(),
        ALNSMaxRuntime(max(float(time_limit_sec), 0.05)),
    )
    return validate_open_route(result.best_state.route, len(costs), start_node)


def _open_route_cost(route, instance) -> tuple[float, float, float]:
    """Weighted arrival, tardiness, and distance of the nodes currently placed.

    The route may be incomplete. Across candidate positions for one inserted
    node the placed set is the same, so this order matches the complete-route
    key (J_WL, tardiness, distance). It does not divide by the instance scale.
    """
    current = 0.0
    weighted = 0.0
    tardiness = 0.0
    distance = 0.0
    for index in range(1, len(route)):
        node = int(route[index])
        previous = int(route[index - 1])
        current += float(instance.cost_matrix[previous, node])
        distance += float(instance.distance_matrix[previous, node])
        current = max(current, float(instance.time_windows[node, 0]))
        weighted += float(instance.urgency[node]) * current
        tardiness += max(0.0, current - float(instance.time_windows[node, 1]))
        current += float(instance.service_times[node])
    return (weighted, tardiness, distance)


def _jwl_worst_removal(state: _JwlALNSState, rng) -> _JwlALNSState:
    """Remove the placed nodes whose deletion most reduces weighted arrival."""
    del rng
    if len(state.route) <= 2:
        return state
    route = list(state.route)
    full = _open_route_cost(route, state.context)[0]
    contributions = []
    for index in range(1, len(route)):
        reduced = route[:index] + route[index + 1:]
        contributions.append((full - _open_route_cost(reduced, state.context)[0], index))
    count = min(len(contributions), max(1, len(contributions) // 5))
    positions = {index for _, index in sorted(contributions, reverse=True)[:count]}
    removed = tuple(node for index, node in enumerate(route) if index in positions)
    kept = tuple(node for index, node in enumerate(route) if index not in positions)
    return _JwlALNSState(kept, state.unassigned + removed, state.context)


def _jwl_greedy_repair(state: _JwlALNSState, rng) -> _JwlALNSState:
    """Reinsert each removed node at the minimum weighted-arrival position."""
    route = list(state.route)
    unassigned = list(state.unassigned)
    while unassigned:
        node = unassigned.pop(_rng_index(rng, len(unassigned)))
        best_position, best_key = 1, None
        for position in range(1, len(route) + 1):
            key = _open_route_cost(route[:position] + [node] + route[position:], state.context)
            if best_key is None or key < best_key:
                best_key, best_position = key, position
        route.insert(best_position, node)
    return _JwlALNSState(tuple(route), (), state.context)


class _RecordingHillClimbing:
    """Accept a non-worsening candidate and keep it in a capacity-limited archive.

    The library callback fires only for a new global best. The protected band
    needs complete routes that are worse than the best ``J_WL`` but still inside
    the tolerance, so every accepted candidate is recorded here.
    """

    def __init__(self, archive: list[list[int]], instance, capacity: int = 24):
        self.archive = archive
        self.instance = instance
        self.capacity = capacity

    def __call__(self, rnd, best, current, candidate):
        del rnd
        accepted = candidate.objective() <= current.objective()
        if accepted and not candidate.unassigned:
            self.archive[:] = _archive_add(
                self.archive, list(candidate.route), self.instance, self.capacity
            )
        return accepted


def _protected_band_route(archive: list[list[int]], instance, tolerance: float) -> tuple[list[int], list[int]]:
    """Return the minimum-J_WL route and the lexicographic route inside its band."""
    from delivery_routing import evaluate_route
    primary = min(archive, key=lambda item: _metrics_key(item, instance))
    best = _metrics_key(primary, instance)[0]
    limit = best * (1.0 + float(tolerance)) + 1e-12
    protected = [item for item in archive if _metrics_key(item, instance)[0] <= limit]
    selected = min(
        protected,
        key=lambda item: (
            float(evaluate_route(item, instance)["late_minutes"]),
            float(evaluate_route(item, instance)["total_distance"]),
            float(evaluate_route(item, instance)["total_travel_time"]),
            _metrics_key(item, instance)[0],
        ),
    )
    return primary, selected


def solve_jwl_alns(instance, seed: int = 0, time_limit_sec: float = 5.0,
                   priority_tolerance=None, archive_capacity: int = 24) -> list[int]:
    """Destroy-and-repair ALNS accepted on (J_WL, tardiness, distance).

    Random removal is unchanged. Worst removal and greedy repair use weighted
    service arrival rather than travel time, so the search is aimed at the same
    first key as weighted-latency local search. Acceptance is the encoded key
    returned by the state objective. With ``priority_tolerance``, the same
    accepted archive also returns the route selected inside the (1+epsilon) band.
    """
    if not HAS_ALNS:
        raise SolverUnavailable("ALNS is not installed; install the registered ALNS version to run this baseline.")
    costs = _validate_inputs(instance.cost_matrix, instance.start_node)
    if len(costs) <= 1:
        return [instance.start_node]
    rng = np.random.RandomState(int(seed))
    initial_route = _best_insertion_route(instance)
    archive = _archive_add([], initial_route, instance, capacity=int(archive_capacity))
    initial = _JwlALNSState(tuple(initial_route), (), instance)
    solver = ALNS(rng)
    solver.add_destroy_operator(_open_route_random_removal, name="random_removal")
    solver.add_destroy_operator(_jwl_worst_removal, name="worst_removal")
    solver.add_repair_operator(_jwl_greedy_repair, name="weighted_arrival_insertion")
    solver.iterate(
        initial,
        RandomSelect(num_destroy=2, num_repair=1),
        _RecordingHillClimbing(archive, instance, capacity=int(archive_capacity)),
        ALNSMaxRuntime(max(float(time_limit_sec), 0.05)),
    )
    primary, selected = _protected_band_route(
        archive, instance, 0.0 if priority_tolerance is None else priority_tolerance
    )
    if priority_tolerance is not None:
        from delivery_routing import evaluate_route
        primary_metrics = evaluate_route(primary, instance)
        instance.metadata["jwl_alns_priority_only"] = {
            "route": [int(node) for node in primary],
            "J_WL": float(primary_metrics["J_WL"]),
            "late_minutes": float(primary_metrics["late_minutes"]),
            "total_distance": float(primary_metrics["total_distance"]),
            "total_travel_time": float(primary_metrics["total_travel_time"]),
            "archive_size": len(archive),
            "priority_tolerance": float(priority_tolerance),
            "archive": [list(item) for item in archive],
        }
    return _delivery_validate(selected, instance)


def solve_ortools_gls_open_route(
    cost_matrix: np.ndarray,
    time_limit_sec: float,
    start_node: int = 0,
) -> list[int]:
    """Run OR-Tools guided local search with an explicit zero-cost end node."""
    if not HAS_ORTOOLS:
        raise SolverUnavailable("OR-Tools is not installed; install the registered OR-Tools version to run this baseline.")
    costs = _validate_inputs(cost_matrix, start_node)
    n_nodes = len(costs)
    if n_nodes == 1:
        return [start_node]
    terminal = n_nodes
    manager = pywrapcp.RoutingIndexManager(n_nodes + 1, 1, [start_node], [terminal])
    routing = pywrapcp.RoutingModel(manager)

    def transit_cost(from_index: int, to_index: int) -> int:
        frm = manager.IndexToNode(from_index)
        to = manager.IndexToNode(to_index)
        if to == terminal:
            return 0
        if frm == terminal:
            return 0
        return _integer_cost(costs[frm, to])

    callback_index = routing.RegisterTransitCallback(transit_cost)
    routing.SetArcCostEvaluatorOfAllVehicles(callback_index)
    parameters = pywrapcp.DefaultRoutingSearchParameters()
    parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    parameters.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    parameters.time_limit.FromMilliseconds(max(50, int(round(float(time_limit_sec) * 1000))))
    assignment = routing.SolveWithParameters(parameters)
    if assignment is None:
        raise RuntimeError("OR-Tools GLS did not produce a route.")
    route = []
    index = routing.Start(0)
    while not routing.IsEnd(index):
        node = manager.IndexToNode(index)
        if node != terminal:
            route.append(node)
        index = assignment.Value(routing.NextVar(index))
    return validate_open_route(route, n_nodes, start_node)


def solve_priority_distance_greedy(
    cost_matrix: np.ndarray,
    fill_rates: Iterable[float],
    start_node: int = 0,
) -> list[int]:
    """Transparent application heuristic: prioritize fill level per travel cost."""
    costs = _validate_inputs(cost_matrix, start_node)
    fills = np.asarray(list(fill_rates), dtype=float)
    if len(fills) != len(costs) or not np.all(np.isfinite(fills)):
        raise ValueError("fill_rates must be finite and aligned with cost_matrix.")
    route = [start_node]
    remaining = set(range(len(costs))) - {start_node}
    while remaining:
        current = route[-1]
        node = max(
            remaining,
            key=lambda candidate: ((0.05 + fills[candidate]) / max(costs[current, candidate], 1e-8), -candidate),
        )
        route.append(node)
        remaining.remove(node)
    return validate_open_route(route, len(costs), start_node)


def route_to_full_path(
    route: Sequence[int],
    road_connections: dict[tuple[int, int], Sequence[Sequence[float]]],
    positions: Sequence[Sequence[float]],
) -> list[tuple[float, float]]:
    """Expand service order into the same road-polyline representation as JWL-ALNS."""
    route = list(route)
    if not route:
        return []
    full_path: list[tuple[float, float]] = [tuple(map(float, positions[route[0]]))]
    for start, end in zip(route, route[1:]):
        segment = road_connections.get((start, end))
        if not segment:
            reverse = road_connections.get((end, start))
            segment = list(reversed(reverse)) if reverse else None
        if not segment:
            segment = [positions[start], positions[end]]
        points = [tuple(map(float, point)) for point in segment]
        if full_path[-1] == points[0]:
            full_path.extend(points[1:])
        else:
            full_path.extend(points)
    return full_path


# ---------------------------------------------------------------------------
# Delivery-specific adapters
# ---------------------------------------------------------------------------

def _delivery_objective(route, instance, alpha=0.5, beta=1.0) -> float:
    from delivery_routing import common_objective
    return common_objective(route, instance, alpha=alpha, beta=beta)


def _metrics_key(route, instance) -> tuple[float, float, float]:
    """Primary J_WL order with tardiness and route length as tie breakers."""
    from delivery_routing import evaluate_route
    metrics = evaluate_route(route, instance)
    return (float(metrics["J_WL"]), float(metrics["late_minutes"]), float(metrics["total_distance"]))


def _constrained_metrics_key(route, instance) -> tuple[float, float, float, float, float]:
    """Lexicographic key for the operationally constrained JWL-ALNS output.

    Feasibility is primary; urgency-weighted latency is optimized only after
    the candidate is within the frozen operating envelope.  Violation is a
    continuous normalized quantity, so infeasible candidates can still guide
    repair toward feasibility.
    """
    from delivery_routing import constraint_summary, evaluate_route
    metrics = evaluate_route(route, instance)
    summary = constraint_summary(metrics, instance)
    return (
        float(summary["constraint_violation"]),
        float(metrics["J_WL"]),
        float(metrics["late_minutes"]),
        float(metrics["total_distance"]),
        float(metrics["total_travel_time"]),
    )


def _reference_distance(instance) -> float:
    """Nearest-neighbour open-route distance L(P^NN), cached on the instance."""
    cached = instance.metadata.get("L_ref")
    if cached is not None:
        return max(float(cached), 1e-9)
    from delivery_routing import evaluate_route, operating_limits, reference_route
    limits = operating_limits(instance)
    value = max(float(limits.get("L_ref", evaluate_route(reference_route(instance), instance)["total_distance"])), 1e-9)
    instance.metadata["L_ref"] = value
    return value


def _layout_positions(instance, positions: Sequence[Sequence[float]] | None = None) -> list[tuple[float, float]]:
    """Coordinates for solvers that require a location object, not a distance source."""
    if positions is not None:
        return [(float(row[0]), float(row[1])) for row in positions]
    stored = instance.metadata.get("positions")
    if stored is not None:
        return [(float(row[0]), float(row[1])) for row in stored]
    n = instance.node_count
    return [(float(index), 0.0) for index in range(n)]


def _guided_score(route, instance, weight_late: float, weight_distance: float,
                  priority_weight: float = 1.0) -> float:
    """Search-only score. Reported claims always use the decomposed metrics."""
    from delivery_routing import evaluate_route
    metrics = evaluate_route(route, instance)
    horizon = max(float(metrics["arrival_time"]), 1.0)
    reference = _reference_distance(instance)
    return (priority_weight * float(metrics["J_WL"])
            + weight_late * float(metrics["late_minutes"]) * 60.0 / horizon
            + weight_distance * float(metrics["total_distance"]) / reference)


def _priority_greedy(instance) -> list[int]:
    costs = _validate_inputs(instance.cost_matrix, instance.start_node)
    route = [instance.start_node]
    remaining = set(range(len(costs))) - {instance.start_node}
    while remaining:
        current = route[-1]
        node = max(remaining, key=lambda x: (
            float(instance.urgency[x]) / max(float(costs[current, x]), 1e-6), -x
        ))
        route.append(int(node))
        remaining.remove(node)
    return route


def _nearest_route(instance) -> list[int]:
    return _nearest_neighbour_route(instance.cost_matrix, instance.start_node)


def solve_travel_time_nearest(instance) -> list[int]:
    """Efficiency-only baseline using nearest travel-time successor."""
    return _delivery_validate(_nearest_route(instance), instance)


def _best_insertion_route(instance) -> list[int]:
    route = [instance.start_node]
    for node in sorted((x for x in range(instance.node_count) if x != instance.start_node),
                       key=lambda x: (-instance.urgency[x], x)):
        best_position, best_delta = 1, float("inf")
        for position in range(1, len(route) + 1):
            previous = route[position - 1]
            if position == len(route):
                delta = instance.cost_matrix[previous, node]
            else:
                following = route[position]
                delta = (instance.cost_matrix[previous, node] + instance.cost_matrix[node, following]
                         - instance.cost_matrix[previous, following])
            delta -= 0.05 * float(instance.urgency[node])
            if delta < best_delta:
                best_delta, best_position = float(delta), position
        route.insert(best_position, int(node))
    return route


def _random_move(route: list[int], rng: np.random.Generator) -> list[int]:
    candidate = list(route)
    n = len(candidate)
    if n <= 3:
        return candidate
    move = int(rng.integers(3))
    a, b = sorted(int(x) for x in rng.choice(np.arange(1, n), size=2, replace=False))
    if move == 0:
        node = candidate.pop(a)
        candidate.insert(b, node)
    elif move == 1:
        candidate[a], candidate[b] = candidate[b], candidate[a]
    else:
        candidate[a:b + 1] = reversed(candidate[a:b + 1])
    return candidate


def _partial_weighted_arrival(route: list[int], instance, limit: int) -> float:
    """Weighted arrival of the first ``limit`` nodes, without a full evaluation.

    The depot contribution is zero. Insertions after ``limit`` do not change
    this value, so a partial route can be compared while it is rebuilt.
    """
    current = 0.0
    total = 0.0
    for index in range(1, limit):
        node = route[index]
        current += float(instance.cost_matrix[route[index - 1], node])
        current = max(current, float(instance.time_windows[node, 0]))
        total += float(instance.urgency[node]) * current
        current += float(instance.service_times[node])
    return total


def _best_partial_position(route: list[int], node: int, instance) -> tuple[int, float]:
    """Position minimising the partial weighted-arrival cost of inserting ``node``."""
    best_position, best_cost = 1, float("inf")
    for position in range(1, len(route) + 1):
        trial = list(route)
        trial.insert(position, node)
        cost = _partial_weighted_arrival(trial, instance, position + 1)
        if cost < best_cost - 1e-12 or (
            abs(cost - best_cost) <= 1e-12 and position < best_position
        ):
            best_position, best_cost = position, cost
    return best_position, best_cost


def _segment_move(route: list[int], instance, rng: np.random.Generator) -> list[int]:
    """Remove a contiguous customer segment and reinsert it.

    Removal length is uniform on ``3..min(8, n-1)`` and never includes the
    depot. With equal probability the removed nodes are reinserted in random
    order at their cheapest partial position, or the maximum-regret node is
    reinserted at each step. The returned route is always complete.
    """
    n = len(route)
    if n <= 4:
        return list(route)
    length = int(rng.integers(3, min(8, n - 1) + 1))
    start = int(rng.integers(1, n - length + 1))
    removed = route[start:start + length]
    kept = route[:start] + route[start + length:]
    order = [int(node) for node in removed]
    if rng.random() < 0.5:
        rng.shuffle(order)
        for node in order:
            position, _ = _best_partial_position(kept, node, instance)
            kept.insert(position, node)
        return kept
    pending = order
    while pending:
        scored = []
        for node in pending:
            costs = []
            for position in range(1, len(kept) + 1):
                trial = list(kept)
                trial.insert(position, node)
                costs.append((_partial_weighted_arrival(trial, instance, position + 1), position))
            costs.sort()
            best_cost, best_position = costs[0]
            second_cost = costs[1][0] if len(costs) > 1 else best_cost
            scored.append((second_cost - best_cost, -int(node), best_position, node))
        _, _, position, node = max(scored)
        kept.insert(position, node)
        pending.remove(node)
    return kept


def _dominates(a: tuple[float, float, float], b: tuple[float, float, float]) -> bool:
    return all(x <= y + 1e-12 for x, y in zip(a, b)) and any(x < y - 1e-12 for x, y in zip(a, b))


def _archive_add(archive: list[list[int]], route: list[int], instance, capacity: int = 24,
                 constrained: bool = False) -> list[list[int]]:
    key = _constrained_metrics_key(route, instance) if constrained else _metrics_key(route, instance)
    compare = _constrained_metrics_key if constrained else _metrics_key
    # Archive dominance uses the first three reported operating criteria for
    # the legacy mode and the complete lexicographic constrained key otherwise.
    if constrained:
        kept = [item for item in archive if not _dominates(key[:3], compare(item, instance)[:3])]
        if not any(_dominates(compare(item, instance)[:3], key[:3]) or compare(item, instance) == key for item in kept):
            kept.append(list(route))
    else:
        kept = [item for item in archive if not _dominates(key, compare(item, instance))]
        if not any(_dominates(compare(item, instance), key) or compare(item, instance) == key for item in kept):
            kept.append(list(route))
    # Fixed deterministic truncation makes a deadline incumbent reproducible.
    return sorted(kept, key=lambda item: compare(item, instance))[:capacity]


def _operating_key(route, instance) -> tuple[float, float, float]:
    """Normalised operational burden used by the Pareto balance archive."""
    from delivery_routing import evaluate_route, reference_route
    metrics = evaluate_route(route, instance)
    reference = evaluate_route(reference_route(instance), instance)
    return (
        float(metrics["late_minutes"]) / max(float(reference["late_minutes"]), 1e-9),
        float(metrics["total_distance"]) / max(float(reference["total_distance"]), 1e-9),
        float(metrics["total_travel_time"]) / max(float(reference["total_travel_time"]), 1e-9),
    )


def _balance_archive_add(archive: list[list[int]], route: list[int], instance,
                         capacity: int = 24) -> list[list[int]]:
    """Keep non-dominated operational candidates in the protected band."""
    key = _operating_key(route, instance)
    kept = [item for item in archive if not _dominates(key, _operating_key(item, instance))]
    if not any(
        _dominates(_operating_key(item, instance), key)
        or _operating_key(item, instance) == key
        for item in kept
    ):
        kept.append(list(route))
    return sorted(kept, key=lambda item: (_operating_key(item, instance), _metrics_key(item, instance)[0]))[:capacity]


def _delivery_validate(route, instance) -> list[int]:
    from delivery_routing import validate_route
    return validate_route(route, instance)


def solve_weighted_latency_ils(
    instance,
    seed: int = 0,
    time_limit_sec: float = 5.0,
    alpha: float = 0.8,
    beta: float = 1.0,
) -> list[int]:
    """Iterated local search directly targeting urgency-weighted latency.

    The implementation is intentionally transparent: nearest-neighbour
    initialisation followed by relocate, swap and 2-opt perturbations.  It is
    a dedicated minimum-latency comparator, not a renamed distance heuristic.
    """
    del alpha, beta
    costs = _validate_inputs(instance.cost_matrix, instance.start_node)
    if len(costs) <= 1:
        return [instance.start_node]
    rng = np.random.default_rng(int(seed))
    best = _best_insertion_route(instance)
    best_key = _metrics_key(best, instance)
    current = list(best)
    deadline = perf_counter() + max(float(time_limit_sec), 0.05)
    while perf_counter() < deadline:
        candidate = _random_move(current, rng)
        key = _metrics_key(candidate, instance)
        if key <= _metrics_key(current, instance) or rng.random() < 0.025:
            current = candidate
        if key < best_key:
            best, best_key = candidate, key
    return _delivery_validate(best, instance)


def solve_amw_priority_open_route(
    instance,
    seed: int = 0,
    time_limit_sec: float = 5.0,
    priority_weight: float = 0.65,
    use_adaptive_weights: bool = True,
    use_priority_local_search: bool = True,
    use_candidate_archive: bool = True,
    constrained: bool = False,
    priority_guarded: bool = False,
    priority_tolerance: float = 0.05,
    priority_pareto: bool = False,
    neighborhood: str = "point",
) -> list[int]:
    """External-matrix implementation of the JWL-ALNS decision rule.

    The image-based runner obtains road costs from the extracted network.  For
    an external delivery matrix, this adapter preserves the same algorithmic
    contract: urgency pressure is combined with travel time, candidates are
    retained, and relocate/swap/2-opt moves are accepted only when the fixed
    priority-fair objective improves.  It is deliberately named as an adapter
    so it cannot be mistaken for a separately retrained model.
    """
    costs = _validate_inputs(instance.cost_matrix, instance.start_node)
    if not 0.0 <= float(priority_weight) <= 1.0:
        raise ValueError("priority_weight must be in [0, 1]")
    if sum(bool(value) for value in (constrained, priority_guarded, priority_pareto)) > 1:
        raise ValueError("JWL-ALNS selection modes are mutually exclusive")
    if float(priority_tolerance) < 0.0:
        raise ValueError("priority_tolerance must be non-negative")
    if neighborhood not in ("point", "segment"):
        raise ValueError("neighborhood must be 'point' or 'segment'")
    n = len(costs)
    if n <= 1:
        instance.metadata["amw_search_trace"] = {
            "amw_iterations": 0,
            "amw_best_time_sec": 0.0,
            "amw_improved_last_quartile": False,
        }
        return [instance.start_node]
    rng = np.random.default_rng(int(seed))
    # Earliest urgent first, weighted insertion and short-road starts form the
    # fixed three-way initial candidate pool.
    archive = []
    for initial in (_priority_greedy(instance), _best_insertion_route(instance), _nearest_route(instance)):
        archive = _archive_add(archive, initial, instance, constrained=constrained)
    key_fn = _constrained_metrics_key if constrained else _metrics_key
    current = min(archive, key=lambda item: key_fn(item, instance))
    best = list(current)
    priority_archive = list(archive)
    balance_archive: list[list[int]] = []
    weight_late, weight_distance = 0.40, 0.05
    budget = max(float(time_limit_sec), 0.05)
    started = perf_counter()
    deadline = started + budget
    last_quartile_start = started + 0.75 * budget
    iteration = 0
    improved_last_quartile = False
    incumbent_first_time = 0.0
    first_seen: dict[tuple[int, ...], float] = {}

    def _mark_seen(route: list[int], when: float) -> None:
        key = tuple(int(node) for node in route)
        if key not in first_seen:
            first_seen[key] = float(when)

    def _remember_incumbent(route: list[int], when: float) -> None:
        nonlocal best, incumbent_first_time, improved_last_quartile
        best = list(route)
        incumbent_first_time = float(when)
        _mark_seen(route, when)
        if when >= last_quartile_start:
            improved_last_quartile = True

    for item in archive:
        _mark_seen(item, 0.0)
    _mark_seen(best, 0.0)
    while perf_counter() < deadline:
        if neighborhood == "segment" and rng.random() < 0.7:
            candidate = _segment_move(current, instance, rng)
        else:
            candidate = _random_move(current, rng)
        if use_priority_local_search and n > 4 and rng.random() < 0.55:
            high_position = max(range(1, n), key=lambda pos: instance.urgency[candidate[pos]])
            target = int(rng.integers(1, high_position + 1))
            candidate.insert(target, candidate.pop(high_position))
        if constrained and use_adaptive_weights and iteration % 25 == 0:
            from delivery_routing import evaluate_route
            pressure = evaluate_route(current, instance)
            weight_late = min(4.0, 0.25 + 3.0 * float(pressure["late_rate"]))
            weight_distance = min(0.35, 0.03 + 0.20 * float(pressure["total_distance"]) / _reference_distance(instance))
        # The incumbent walks on the reported objective. The guided score mixes
        # in tardiness and distance at a larger magnitude than J_WL, so accepting
        # on it spends the budget improving a target the paper does not claim.
        # Tardiness and distance stay in the end-of-budget band selection below.
        # The constrained mode keeps its own key, which already leads with
        # feasibility and then J_WL.
        if constrained:
            accepts = key_fn(candidate, instance) <= key_fn(current, instance)
        else:
            accepts = _metrics_key(candidate, instance) <= _metrics_key(current, instance)
        if accepts or rng.random() < 0.015:
            current = candidate
        _mark_seen(candidate, perf_counter() - started)
        if priority_pareto:
            priority_archive = _archive_add(priority_archive, candidate, instance, constrained=False)
            archive = priority_archive
        else:
            archive = (_archive_add(archive, candidate, instance, constrained=constrained)
                       if use_candidate_archive else [current])
        if key_fn(candidate, instance) < key_fn(best, instance):
            _remember_incumbent(candidate, perf_counter() - started)
        if priority_pareto:
            best_priority = min(_metrics_key(item, instance)[0] for item in priority_archive + [best])
            protected_limit = best_priority * (1.0 + float(priority_tolerance)) + 1e-12
            for item in priority_archive:
                if _metrics_key(item, instance)[0] <= protected_limit:
                    balance_archive = _balance_archive_add(balance_archive, item, instance)
        iteration += 1
    if priority_pareto:
        if not balance_archive:
            balance_archive = [best]
        selected = min(
            balance_archive,
            key=lambda item: (_operating_key(item, instance), _metrics_key(item, instance)[0]),
        )
        instance.metadata["amw_search_trace"] = {
            "amw_iterations": int(iteration),
            "amw_best_time_sec": float(first_seen.get(tuple(selected), incumbent_first_time)),
            "amw_improved_last_quartile": bool(improved_last_quartile),
        }
        return _delivery_validate(selected, instance)
    candidates = archive + [best]
    if priority_guarded:
        # Preserve the primary service-order objective first.  Within a
        # pre-declared relative J_WL band around the best archived incumbent,
        # select the route with the smallest operational burden.  This is a
        # bounded trade-off rule, not a hard feasibility claim.
        primary_route = min(candidates, key=lambda item: _metrics_key(item, instance))
        primary_best = _metrics_key(primary_route, instance)[0]
        primary_limit = primary_best * (1.0 + float(priority_tolerance)) + 1e-12
        protected = [
            item for item in candidates
            if _metrics_key(item, instance)[0] <= primary_limit
        ]
        from delivery_routing import evaluate_route
        selected = min(
            protected,
            key=lambda item: (
                float(evaluate_route(item, instance)["late_minutes"]),
                float(evaluate_route(item, instance)["total_distance"]),
                float(evaluate_route(item, instance)["total_travel_time"]),
                _metrics_key(item, instance)[0],
            ),
        )
        selected_jwl = _metrics_key(selected, instance)[0]
        if selected_jwl > primary_limit:
            raise RuntimeError("priority-protected selection left the (1+ε) band")
        primary_metrics = evaluate_route(primary_route, instance)
    else:
        selected = min(candidates, key=lambda item: key_fn(item, instance))
        primary_route = None
        primary_best = None
        selected_jwl = None
        primary_metrics = None
    trace = {
        "amw_iterations": int(iteration),
        "amw_best_time_sec": float(first_seen.get(tuple(selected), incumbent_first_time)),
        "amw_improved_last_quartile": bool(improved_last_quartile),
        "amw_primary_best_jwl": None if primary_best is None else float(primary_best),
        "amw_selected_jwl": None if selected_jwl is None else float(selected_jwl),
        "priority_tolerance": float(priority_tolerance) if priority_guarded else None,
        "neighborhood": neighborhood,
    }
    if primary_metrics is not None:
        trace.update({
            "priority_only_route": [int(node) for node in primary_route],
            "priority_only_J_WL": float(primary_metrics["J_WL"]),
            "priority_only_late_minutes": float(primary_metrics["late_minutes"]),
            "priority_only_distance": float(primary_metrics["total_distance"]),
            "priority_only_travel_time": float(primary_metrics["total_travel_time"]),
            "priority_only_first_time_sec": float(
                first_seen.get(tuple(primary_route), incumbent_first_time)
            ),
        })
    instance.metadata["amw_search_trace"] = trace
    return _delivery_validate(selected, instance)


def solve_static_alns(instance, seed: int = 0, time_limit_sec: float = 5.0) -> list[int]:
    """Pure-Python static destroy/repair ALNS comparator for the protocol."""
    rng = np.random.default_rng(int(seed))
    current = _nearest_route(instance)
    best = list(current)
    deadline = perf_counter() + max(float(time_limit_sec), 0.05)
    while perf_counter() < deadline:
        candidate = _random_move(current, rng)
        if _metrics_key(candidate, instance) <= _metrics_key(current, instance) or rng.random() < 0.03:
            current = candidate
        if _metrics_key(candidate, instance) < _metrics_key(best, instance):
            best = list(candidate)
    return _delivery_validate(best, instance)


def _delivery_time_windows(instance):
    windows = np.asarray(instance.time_windows, dtype=float)
    service = np.asarray(instance.service_times, dtype=float)
    return windows, service


def _pyvrp_soft_windows(instance):
    """Widen hard PyVRP windows so a complete open route is always returned.

    Reported tardiness still comes from the shared soft-window evaluator.
    """
    from delivery_routing import operating_limits
    windows, service = _delivery_time_windows(instance)
    limits = operating_limits(instance)
    # Seconds, matching cost_matrix / service_times.  The NN horizon and the
    # generation horizon are both stored in the same unit.
    ceiling = max(
        float(np.max(windows[:, 1])),
        float(limits["reference_horizon_nn"]),
        float(limits["model_horizon"]),
        1.0,
    ) * 4.0
    widened = windows.copy()
    widened[:, 1] = np.maximum(widened[:, 1], ceiling)
    return widened, service


def _pyvrp_edge_costs(instance, frm_node: int, to_node: int) -> tuple[int, int]:
    distance = _integer_cost(float(instance.distance_matrix[frm_node, to_node]))
    duration = max(0, int(round(float(instance.cost_matrix[frm_node, to_node]))))
    return distance, duration



def solve_ortools_open_svptw(
    instance,
    time_limit_sec: float = 5.0,
    hard_time_windows: bool = False,
) -> list[int]:
    """OR-Tools single-vehicle open route with explicit time-window dimension."""
    if not HAS_ORTOOLS:
        raise SolverUnavailable("OR-Tools is not installed; install the registered OR-Tools version to run this baseline.")
    costs = _validate_inputs(instance.cost_matrix, instance.start_node)
    n = len(costs)
    if n <= 1:
        return [instance.start_node]
    terminal = n
    manager = pywrapcp.RoutingIndexManager(n + 1, 1, [instance.start_node], [terminal])
    routing = pywrapcp.RoutingModel(manager)
    windows, service = _delivery_time_windows(instance)

    def transit_cost(from_index: int, to_index: int) -> int:
        frm, to = manager.IndexToNode(from_index), manager.IndexToNode(to_index)
        if frm == terminal or to == terminal:
            return 0
        return _integer_cost(costs[frm, to])

    cost_callback = routing.RegisterTransitCallback(transit_cost)
    routing.SetArcCostEvaluatorOfAllVehicles(cost_callback)

    def transit_time(from_index: int, to_index: int) -> int:
        frm, to = manager.IndexToNode(from_index), manager.IndexToNode(to_index)
        if frm == terminal or to == terminal:
            return 0
        return max(0, int(round(costs[frm, to] + service[frm])))

    time_callback = routing.RegisterTransitCallback(transit_time)
    # Time windows are soft by default: a late visit is allowed and penalised
    # in the objective below.  Consequently the dimension capacity must bound
    # a complete open route, rather than merely the largest due time.  The
    # former lower-bound-style expression (sum of row minima) can be smaller
    # than any Hamiltonian open route on medium instances, which made OR-Tools
    # return no assignment despite the intended soft-tardiness semantics.
    # One outgoing arc per visited node bounds any open route from above; the
    # terminal arc is zero and only makes this deliberately conservative.
    finite_latest = windows[:, 1][np.isfinite(windows[:, 1])]
    latest_time = float(np.max(finite_latest)) if finite_latest.size else 0.0
    finite_earliest = windows[:, 0][np.isfinite(windows[:, 0])]
    earliest_time = float(np.max(finite_earliest)) if finite_earliest.size else 0.0
    route_time_upper = float(np.sum(np.max(costs, axis=1)) + np.sum(service))
    horizon = max(
        1,
        int(np.ceil(latest_time)),
        int(np.ceil(earliest_time + route_time_upper)),
    )
    routing.AddDimension(time_callback, horizon, horizon, False, "Time")
    time_dimension = routing.GetDimensionOrDie("Time")
    for node in range(n):
        index = manager.NodeToIndex(node)
        early, late = int(round(windows[node, 0])), int(round(windows[node, 1]))
        if hard_time_windows:
            time_dimension.CumulVar(index).SetRange(early, late)
        else:
            time_dimension.CumulVar(index).SetMin(early)
            time_dimension.SetCumulVarSoftUpperBound(index, late, 1000)
    parameters = pywrapcp.DefaultRoutingSearchParameters()
    parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    parameters.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    parameters.time_limit.FromMilliseconds(max(50, int(round(float(time_limit_sec) * 1000))))
    assignment = routing.SolveWithParameters(parameters)
    if assignment is None:
        raise RuntimeError("OR-Tools Open-SVRPTW did not produce a route")
    route = []
    index = routing.Start(0)
    while not routing.IsEnd(index):
        node = manager.IndexToNode(index)
        if node != terminal:
            route.append(node)
        index = assignment.Value(routing.NextVar(index))
    return _delivery_validate(route, instance)


def solve_pyvrp_hgs_open_svptw(
    instance,
    positions: Sequence[Sequence[float]] | None = None,
    seed: int = 0,
    time_limit_sec: float = 5.0,
) -> list[int]:
    """PyVRP HGS as an open-route distance/duration operational baseline.

    Edge lengths come from the instance matrices, not from Euclidean embedding.
    Hard windows are widened so HGS always returns a complete route; lateness
    is scored afterwards by the shared soft-window evaluator.  A zero-cost
    return-to-depot edge, if required by PyVRP 0.6.x, is stripped before
    evaluation and is never counted in L or T_travel.
    """
    if not HAS_PYVRP:
        raise SolverUnavailable("PyVRP is not installed; install the registered PyVRP version to run this baseline.")
    costs = _validate_inputs(instance.cost_matrix, instance.start_node)
    n = len(costs)
    if n <= 1:
        return [instance.start_node]
    coords = _layout_positions(instance, positions)
    if len(coords) != n:
        raise ValueError("positions and cost_matrix have different node counts")
    model = Model()
    start_xy = coords[instance.start_node]
    client_nodes = [node for node in range(n) if node != instance.start_node]
    windows, service = _pyvrp_soft_windows(instance)

    # PyVRP 0.6.x supports one depot and uses positional client indices,
    # whereas newer releases support named locations and explicit end depots.
    # The locked requirements file uses 0.6.3, so keep this branch explicit
    # and auditable rather than silently changing the baseline semantics.
    try:
        start_depot = model.add_depot(float(start_xy[0]), float(start_xy[1]), name="start")
        clients = [model.add_client(
            float(coords[node][0]), float(coords[node][1]), delivery=1,
            tw_early=int(round(windows[node, 0])), tw_late=int(round(windows[node, 1])),
            service_duration=int(round(service[node])), name=f"stop_{node}")
            for node in client_nodes
        ]
        end_depot = model.add_depot(float(start_xy[0]), float(start_xy[1]), name="open_end")
        locations = [start_depot, end_depot, *clients]
        location_nodes = {id(start_depot): instance.start_node, id(end_depot): instance.start_node}
        location_nodes.update({id(client): node for client, node in zip(clients, client_nodes)})
        for frm in locations:
            for to in locations:
                if frm is not to:
                    if to is end_depot:
                        model.add_edge(frm, to, distance=0, duration=0)
                    else:
                        distance, duration = _pyvrp_edge_costs(
                            instance, location_nodes[id(frm)], location_nodes[id(to)],
                        )
                        model.add_edge(frm, to, distance=distance, duration=duration)
        model.add_vehicle_type(num_available=1, capacity=len(client_nodes), start_depot=start_depot,
                               end_depot=end_depot, name="open_svptw_vehicle")
        visit_offset = 2
    except (TypeError, ValueError):
        start_depot = model.add_depot(int(round(start_xy[0])), int(round(start_xy[1])))
        clients = [model.add_client(
            int(round(coords[node][0])), int(round(coords[node][1])), demand=1,
            tw_early=int(round(windows[node, 0])), tw_late=int(round(windows[node, 1])),
            service_duration=int(round(service[node]))
        ) for node in client_nodes]
        locations = [start_depot, *clients]
        location_nodes = {id(start_depot): instance.start_node}
        location_nodes.update({id(client): node for client, node in zip(clients, client_nodes)})
        for frm in locations:
            for to in locations:
                if frm is not to:
                    # A zero-cost edge back to the sole depot emulates open
                    # termination while retaining PyVRP's one-depot contract.
                    if to is start_depot:
                        model.add_edge(frm, to, distance=0, duration=0)
                    else:
                        distance, duration = _pyvrp_edge_costs(
                            instance, location_nodes[id(frm)], location_nodes[id(to)],
                        )
                        model.add_edge(frm, to, distance=distance, duration=duration)
        model.add_vehicle_type(capacity=len(client_nodes), num_available=1)
        visit_offset = 1

    result = model.solve(PyVRPMaxRuntime(max(float(time_limit_sec), 0.05)), seed=int(seed))
    # PyVRP 0.6.x treats time-window violation as hard infeasibility. The
    # paper protocol uses soft windows, so a complete route with time warp is
    # still a valid candidate; lateness is recomputed by DeliveryInstance's
    # common evaluator below. Reject only incomplete solutions.
    if not result.best.is_complete():
        raise RuntimeError("PyVRP-HGS Open-SVRPTW did not produce a complete route")
    routes = list(result.best.routes()) if hasattr(result.best, "routes") else list(result.best.get_routes())
    if len(routes) != 1:
        raise RuntimeError("PyVRP-HGS returned an unexpected number of routes")
    visits = [int(location) for location in routes[0].visits()]
    route = [instance.start_node]
    for location in visits:
        index = int(location) - visit_offset
        if 0 <= index < len(client_nodes):
            node = client_nodes[index]
            if node != instance.start_node and node not in route:
                route.append(node)
    missing = [node for node in range(n) if node not in route]
    if missing:
        raise RuntimeError("PyVRP-HGS returned an incomplete open route")
    return _delivery_validate(route, instance)


def solve_dynamic_alns(instance, seed: int = 0, time_limit_sec: float = 5.0) -> list[int]:
    """Event-driven rolling-horizon ALNS approximation.

    Requests are revealed at their release times.  At each event the route is
    re-evaluated over the currently available unserved requests; unreleased
    requests cannot influence the next service decision.
    """
    if instance.release_times is None:
        # Controlled static instances have no request-release process. Use the
        # same ALNS destroy/repair kernel without inventing release events;
        # dynamic rolling-horizon behaviour is evaluated only when releases
        # are explicitly supplied by the external-data adapter.
        return solve_alns_open_route(
            instance.cost_matrix, seed=seed, time_limit_sec=time_limit_sec,
            start_node=instance.start_node,
        )
    rng = np.random.default_rng(int(seed))
    route = [instance.start_node]
    pending = set(range(instance.node_count)) - {instance.start_node}
    clock = 0.0
    deadline = perf_counter() + max(float(time_limit_sec), 0.05)
    while pending and perf_counter() < deadline:
        available = [node for node in pending if instance.release_times[node] <= clock]
        if not available:
            clock = min(float(instance.release_times[node]) for node in pending)
            available = [node for node in pending if instance.release_times[node] <= clock]
        current = route[-1]
        # Re-plan the currently visible suffix with a short ILS call.  The
        # committed prefix remains unchanged, matching rolling-horizon use.
        visible = [current] + sorted(available)
        sub_costs = instance.cost_matrix[np.ix_(visible, visible)]
        sub_urgency = instance.urgency[visible]
        sub_windows = instance.time_windows[visible]
        sub_service = instance.service_times[visible]
        sub_instance = type(instance)(
            sub_costs, sub_urgency, sub_windows, sub_service,
            start_node=0, route_id=instance.route_id,
            latency_scale=instance.latency_scale,
        )
        suffix = solve_weighted_latency_ils(
            sub_instance, seed=seed + len(route), time_limit_sec=min(0.1, max(0.05, time_limit_sec / 10.0))
        )
        next_node = visible[suffix[1]] if len(suffix) > 1 else max(
            available, key=lambda node: (instance.urgency[node] / max(instance.cost_matrix[current, node], 1e-8), -node)
        )
        route.append(int(next_node))
        pending.remove(next_node)
        clock += float(instance.cost_matrix[current, next_node]) + float(instance.service_times[next_node])
        if len(route) > 3 and rng.random() < 0.25:
            route[-2], route[-1] = route[-1], route[-2]
    if pending:
        route.extend(sorted(pending, key=lambda node: -instance.urgency[node]))
    return _delivery_validate(route, instance)


def historical_driver_route_replay(instance) -> list[int]:
    """Return the observed route, after checking it satisfies the common contract."""
    if instance.historical_route is None:
        raise ValueError("instance has no historical_route")
    return _delivery_validate(instance.historical_route, instance)


def run_delivery_baselines(instance, positions=None, seed: int = 0, time_limit_sec: float = 5.0,
                           priority_tolerance: float = 0.05,
                           amw_solver=None, include_amw: bool = True,
                           include_historical: bool = True, include_optional: bool = False,
                           methods=None):
    """Run proposed comparators and return routes plus runtimes.

    ``amw_solver`` is an optional callable supplied by a road-aware JWL-ALNS
    runner.  It is deliberately not silently replaced by a distance heuristic
    when evaluating external matrix data, because that would mislabel the
    method and overstate external validity.
    """
    outputs = []
    if positions is None:
        # PyVRP requires coordinates for its location objects.  Road length is
        # still taken from the instance matrices; these points are only a
        # layout, never a Euclidean distance source.
        positions = _layout_positions(instance)
    specs = []
    if include_amw:
        if amw_solver is not None:
            specs.append(("AMW-MOPP", amw_solver))
        else:
            specs.append((ALGO_AMW_DELIVERY, lambda: solve_amw_priority_open_route(
                instance, seed=seed, time_limit_sec=time_limit_sec
            )))
    specs.extend([
        (ALGO_ALNS, lambda: solve_alns_open_route(instance.cost_matrix, seed, time_limit_sec,
                                                  start_node=instance.start_node)),
        (ALGO_ALNS_STATIC, lambda: solve_static_alns(instance, seed, time_limit_sec)),
        (ALGO_WEIGHTED_MLP_ILS, lambda: solve_weighted_latency_ils(instance, seed, time_limit_sec)),
        (ALGO_JWL_ALNS, lambda: solve_jwl_alns(instance, seed, time_limit_sec,
                                              priority_tolerance=priority_tolerance)),
        (ALGO_PRIORITY_GREEDY, lambda: _priority_greedy(instance)),
        (ALGO_DISTANCE_BASELINE, lambda: solve_travel_time_nearest(instance)),
    ])
    if include_optional:
        specs.extend([
            (ALGO_ORTOOLS_SVPTW, lambda: solve_ortools_open_svptw(instance, time_limit_sec)),
            (ALGO_PYVRP_SVPTW, lambda: solve_pyvrp_hgs_open_svptw(instance, positions, seed, time_limit_sec)),
        ])
    if include_historical and instance.historical_route is not None:
        specs.append((ALGO_HISTORICAL_REPLAY, lambda: historical_driver_route_replay(instance)))
    if methods is not None:
        methods = set(methods)
        specs = [(name, solve) for name, solve in specs if name in methods]
    for name, solve in specs:
        started = perf_counter()
        try:
            route = solve()
            outputs.append(SolverResult(name, route, perf_counter() - started, "available",
                                        solver_version=_solver_version(name)))
        except SolverUnavailable as exc:
            outputs.append(SolverResult(name, None, perf_counter() - started,
                                        f"unavailable: {exc}", str(exc), _solver_version(name)))
        except Exception as exc:  # preserve failed instances for auditability
            outputs.append(SolverResult(name, None, perf_counter() - started,
                                        "failed", str(exc), _solver_version(name)))
    return outputs


def run_all_baselines(
    cost_matrix: np.ndarray,
    positions: Sequence[Sequence[float]],
    fill_rates: Iterable[float],
    road_connections: dict[tuple[int, int], Sequence[Sequence[float]]],
    seed: int,
    time_limit_sec: float,
    start_node: int = 0,
) -> list[tuple[str, list[int], list[tuple[float, float]], float]]:
    """Run the registered priority-aware baselines on a controlled instance.

    The controlled road experiments do not contain observed release times or
    historical driver routes. They therefore use the same open-SVRPTW,
    static-ALNS and weighted-minimum-latency adapters as the external runner,
    with an explicit wide time-window horizon. Dynamic rolling-horizon effects
    are only claimed for instances that provide release times.
    """
    from delivery_routing import DeliveryInstance

    costs = _validate_inputs(cost_matrix, start_node)
    fills = np.asarray(list(fill_rates), dtype=float)
    if fills.shape != (len(costs),) or not np.all(np.isfinite(fills)):
        raise ValueError("fill_rates must be finite and aligned with cost_matrix.")
    horizon = max(float(costs.max()) * max(len(costs), 1) * 2.0, 1.0)
    windows = np.zeros((len(costs), 2), dtype=float)
    windows[:, 1] = horizon
    instance = DeliveryInstance(costs, fills, windows, np.zeros(len(costs)), start_node=start_node)
    outputs = run_delivery_baselines(
        instance,
        positions=positions,
        seed=seed,
        time_limit_sec=time_limit_sec,
        include_amw=False,
        include_historical=False,
    )
    # No request-release process is present in the controlled instances.  Do
    # not label the fallback ALNS run as dynamic; that label is reserved for
    # instances carrying explicit release_times.
    normalized = []
    for output in outputs:
        name, route, runtime = output[:3]
        if name == ALGO_DYNAMIC_ALNS:
            name = ALGO_ALNS_STATIC
        if route is None:
            continue
        normalized.append((name, route, route_to_full_path(route, road_connections, positions), runtime))
    return normalized
