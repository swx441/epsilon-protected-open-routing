"""Reproducible IET ITS protocol for priority-aware open service routing.

The default ``--smoke`` run is intentionally short.  Full confirmatory runs
use the registered five/30-second per-instance budgets and must be completed
before numerical claims are inserted into the manuscript.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from time import perf_counter

import numpy as np

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from baseline_adapters import (ALGO_ALNS_STATIC, ALGO_AMW_DELIVERY, ALGO_JWL_ALNS,
                               ALGO_PRIORITY_GREEDY, ALGO_DISTANCE_BASELINE,
                               ALGO_WEIGHTED_MLP_ILS, run_delivery_baselines,
                               solve_amw_priority_open_route, solve_jwl_alns, SolverResult)
from controlled_priority_instances import make_instance, scenarios
from delivery_routing import (
    PROTOCOL_DIAGNOSTIC_FIELDS,
    constraint_summary,
    evaluate_route,
    protocol_diagnostic_fields,
)
from exact_small_solver import solve_exact_weighted_latency


ALGORITHMS = (ALGO_AMW_DELIVERY, "Native ALNS", ALGO_ALNS_STATIC,
              ALGO_WEIGHTED_MLP_ILS, ALGO_PRIORITY_GREEDY)
AMW_CONSTRAINED = False
AMW_PRIORITY_GUARDED = True
AMW_PRIORITY_TOLERANCE = 0.05

ALL_IET_METHODS = frozenset((
    ALGO_AMW_DELIVERY, "Native ALNS", ALGO_ALNS_STATIC,
    ALGO_WEIGHTED_MLP_ILS, ALGO_JWL_ALNS, ALGO_PRIORITY_GREEDY,
    ALGO_DISTANCE_BASELINE, "OR-Tools Open-SVRPTW",
    "PyVRP-HGS Open-SVRPTW",
))
ALL_EXACT_METHODS = frozenset((
    "Exact weighted-latency oracle", ALGO_AMW_DELIVERY,
    "Native ALNS", ALGO_ALNS_STATIC, ALGO_WEIGHTED_MLP_ILS,
    ALGO_PRIORITY_GREEDY, ALGO_DISTANCE_BASELINE,
))

METHOD_ALIASES = {
    "exact": "Exact weighted-latency oracle",
    "amw": ALGO_AMW_DELIVERY,
    "alns": "Native ALNS",
    "native_alns": "Native ALNS",
    "static_alns": ALGO_ALNS_STATIC,
    "mlp_ils": ALGO_WEIGHTED_MLP_ILS,
    "weighted_mlp_ils": ALGO_WEIGHTED_MLP_ILS,
    "jwl_alns": ALGO_JWL_ALNS,
    "priority_greedy": ALGO_PRIORITY_GREEDY,
    "nearest": ALGO_DISTANCE_BASELINE,
    "ortools": "OR-Tools Open-SVRPTW",
    "pyvrp": "PyVRP-HGS Open-SVRPTW",
    "historical": "Historical driver route replay",
}


def _parse_methods(value: str | None):
    """Translate compact CLI aliases to the exact CSV algorithm labels."""
    if not value or value.strip().lower() in {"all", "*"}:
        return None
    selected = set()
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        selected.add(METHOD_ALIASES.get(token.lower(), token))
    unknown = selected.difference(set(METHOD_ALIASES.values()))
    if unknown:
        raise ValueError(f"Unknown method(s): {', '.join(sorted(unknown))}")
    return selected


def _run_one(instance, seed: int, budget: float, ablation: str | None = None,
             methods=None):
    kwargs = {}
    if ablation == "no_adaptive_weights":
        kwargs["use_adaptive_weights"] = False
    elif ablation == "no_priority_local_search":
        kwargs["use_priority_local_search"] = False
    elif ablation == "single_solution":
        kwargs["use_candidate_archive"] = False
    kwargs["constrained"] = AMW_CONSTRAINED
    kwargs["priority_guarded"] = AMW_PRIORITY_GUARDED
    kwargs["priority_tolerance"] = AMW_PRIORITY_TOLERANCE
    started = perf_counter()
    outputs = []
    if methods is None or ALGO_AMW_DELIVERY in methods:
        try:
            route = solve_amw_priority_open_route(instance, seed, budget, **kwargs)
            outputs.append(SolverResult(ALGO_AMW_DELIVERY, route, perf_counter() - started, "available"))
        except Exception as exc:
            outputs.append(SolverResult(ALGO_AMW_DELIVERY, None, perf_counter() - started,
                                        "failed", str(exc)))
    for solver_result in run_delivery_baselines(instance, seed=seed, time_limit_sec=budget,
                                                include_amw=False, include_historical=False,
                                                include_optional=True, methods=methods):
        outputs.append(solver_result)
        saved = instance.metadata.get("jwl_alns_priority_only")
        if solver_result.algorithm == ALGO_JWL_ALNS and saved:
            outputs.append(SolverResult(
                "JWL-ALNS priority-only", saved["route"], solver_result.runtime_sec, "available"
            ))
    rows = []
    for result in outputs:
        name, route, runtime, status = result
        if route is None:
            rows.append({"algorithm": name, "status": status, "runtime_sec": runtime,
                         "route_valid": False, "solver_version": result.solver_version,
                         "error_message": result.error_message,
                         **protocol_diagnostic_fields(instance, name)})
            continue
        metrics = evaluate_route(route, instance)
        constraint = constraint_summary(metrics, instance)
        rows.append({"algorithm": name, "status": status, "runtime_sec": runtime,
                     "route_valid": True, "J_WL": metrics["J_WL"],
                     "J_rank": metrics["weighted_rank_latency"],
                     "late_rate": metrics["late_rate"], "late_minutes": metrics["late_minutes"],
                     "mean_late_minutes": metrics["mean_late_minutes"],
                     "total_distance": metrics["total_distance"],
                     "total_travel_time": metrics["total_travel_time"],
                     "first_half_top_quartile_coverage": metrics["first_half_top_quartile_coverage"],
                     "top_quartile_mean_service_position": metrics["top_quartile_mean_service_position"],
                     "constraint_violation": constraint["constraint_violation"],
                     "operationally_feasible": constraint["operationally_feasible"],
                     "distance_limit": constraint["distance_limit"],
                     "travel_time_limit": constraint["travel_time_limit"],
                     "late_minutes_limit": constraint["late_minutes_limit"],
                     "distance_violation": constraint["distance_violation"],
                     "travel_time_violation": constraint["travel_time_violation"],
                     "late_minutes_violation": constraint["late_minutes_violation"],
                     **protocol_diagnostic_fields(instance, name),
                     "route": " ".join(map(str, route)),
                     "solver_version": result.solver_version,
                     "error_message": result.error_message})
    return rows


def _existing_keys(path: Path, key_fields: tuple[str, ...]) -> set[tuple[str, ...]]:
    if not path.exists() or path.stat().st_size == 0:
        return set()
    with path.open("r", newline="", encoding="utf-8") as handle:
        # Failed/unavailable rows are retained for audit, but must be retried
        # by --resume once the dependency or transient failure is fixed.
        return {
            tuple(row.get(field, "") for field in key_fields)
            for row in csv.DictReader(handle)
            if row.get("status") == "available"
            and row.get("route_valid", "").lower() == "true"
        }


def _repair_keys(path: Path | None, key_fields: tuple[str, ...], methods=None):
    """Load successful keys from an older CSV as a repair skip-list."""
    if path is None or not path.exists():
        return set()
    keys = _existing_keys(path, key_fields)
    if methods is None:
        return keys
    allowed = set(methods)
    return {key for key in keys if key and key[-1] in allowed}


def _instance_has_all_results(
    completed: set[tuple[str, ...]], prefix: tuple[str, ...], expected_count: int = 8,
    expected_algorithms=None,
) -> bool:
    """Return whether all algorithm rows for one instance are already present.

    Resume must skip the solver call itself, not merely suppress duplicate CSV
    writes after an expensive instance has been recomputed.
    """
    matching = [key for key in completed if key[:len(prefix)] == prefix]
    if expected_algorithms is not None:
        present = {key[len(prefix)] for key in matching if len(key) > len(prefix)}
        return set(expected_algorithms).issubset(present)
    return len(matching) >= expected_count


def _instance_has_methods(completed, prefix, methods):
    """Check exact selected method keys, avoiding count-based false skips."""
    if methods is None:
        return False
    present = {key[len(prefix)] for key in completed
               if key[:len(prefix)] == prefix and len(key) > len(prefix)}
    return set(methods).issubset(present)


def _open_results(path: Path, fields: list[str], resume: bool):
    path.parent.mkdir(parents=True, exist_ok=True)
    append = resume and path.exists() and path.stat().st_size > 0
    if append:
        with path.open("r", newline="", encoding="utf-8") as existing:
            existing_fields = next(csv.reader(existing), [])
        if existing_fields != fields:
            raise ValueError(
                f"Existing CSV schema differs for {path}; use a new output directory "
                "when switching the selection mode or protocol fields."
            )
    handle = path.open("a" if append else "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(handle, fieldnames=fields)
    if not append:
        writer.writeheader(); handle.flush()
    return handle, writer


def _write_metadata(path: Path, *, resume: bool, methods=None, protocol: str = "iet"):
    """Record the immutable runner configuration next to each result CSV."""
    meta_path = path.with_suffix(".meta.json")
    payload = {
        "protocol": protocol,
        "amw_mode": "constrained" if AMW_CONSTRAINED else (
            "priority_guarded" if AMW_PRIORITY_GUARDED else "legacy"
        ),
        "priority_tolerance": AMW_PRIORITY_TOLERANCE,
        "methods": sorted(methods) if methods is not None else "all",
    }
    if resume and meta_path.exists():
        try:
            old = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            old = None
        if old and (old.get("amw_mode"), old.get("priority_tolerance"), old.get("methods")) != (
            payload["amw_mode"], payload["priority_tolerance"], payload["methods"]
        ):
            raise ValueError(f"Metadata mismatch for {path}; use a new output directory")
    if not meta_path.exists() or not resume:
        meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _paused(pause_file: str | None) -> bool:
    return bool(pause_file) and Path(pause_file).exists()


def run_controlled(output: Path, seeds: list[int], budget: float, node_count: int = 24,
                   ablation: str | None = None, gamma: float = 1.0,
                   resume: bool = False, pause_file: str | None = None,
                   methods=None, repair_from: Path | None = None) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    path = output / ("ablation_results.csv" if ablation else "controlled_results.csv")
    _write_metadata(path, resume=resume, methods=methods)
    fields = ["node_count", "scenario", "seed", "budget_sec", "gamma", "ablation", "algorithm", "status",
              "runtime_sec", "solver_version", "error_message", "route_valid", "J_WL", "J_rank", "late_rate", "late_minutes", "mean_late_minutes",
              "total_distance", "total_travel_time", "first_half_top_quartile_coverage",
              "top_quartile_mean_service_position", "constraint_violation", "operationally_feasible",
              "distance_limit", "travel_time_limit", "late_minutes_limit",
              "distance_violation", "travel_time_violation", "late_minutes_violation",
              *PROTOCOL_DIAGNOSTIC_FIELDS, "route"]
    key_fields = ("node_count", "scenario", "seed", "budget_sec", "gamma", "ablation", "algorithm")
    completed = _existing_keys(path, key_fields) if resume else set()
    completed |= _repair_keys(repair_from, key_fields, methods)
    handle, writer = _open_results(path, fields, resume)
    try:
        for scenario in scenarios():
            for seed in seeds:
                if _paused(pause_file):
                    print(f"[paused] checkpoint retained for {path}", flush=True)
                    return path
                instance = make_instance(node_count, scenario, seed, gamma=gamma)
                instance_prefix = tuple(str(value) for value in (
                    node_count, scenario, seed, budget, gamma, ablation or "full"
                ))
                complete = _instance_has_all_results(
                    completed, instance_prefix,
                    expected_algorithms=methods if methods is not None else ALL_IET_METHODS,
                )
                if resume and complete:
                    continue
                for row in _run_one(instance, seed, budget, ablation, methods=methods):
                    record = {"node_count": node_count, "scenario": scenario, "seed": seed,
                              "budget_sec": budget, "gamma": gamma,
                              "ablation": ablation or "full", **row}
                    key = tuple(str(record.get(field, "")) for field in key_fields)
                    if key not in completed:
                        writer.writerow(record); handle.flush(); completed.add(key)
    finally:
        handle.close()
    return path


def run_tradeoff(output: Path, seeds: list[int], budget: float,
                 resume: bool = False, pause_file: str | None = None,
                 methods=None, repair_from: Path | None = None) -> Path:
    """Run the pre-registered urgency exponent sensitivity grid."""
    output.mkdir(parents=True, exist_ok=True)
    path = output / "tradeoff_results.csv"
    _write_metadata(path, resume=resume, methods=methods, protocol="tradeoff")
    fields = ["node_count", "scenario", "seed", "budget_sec", "gamma", "algorithm", "status",
              "runtime_sec", "solver_version", "error_message", "route_valid", "J_WL", "J_rank", "late_rate", "late_minutes", "mean_late_minutes",
              "total_distance", "total_travel_time", "first_half_top_quartile_coverage",
              "top_quartile_mean_service_position", "constraint_violation", "operationally_feasible",
              "distance_limit", "travel_time_limit", "late_minutes_limit",
              "distance_violation", "travel_time_violation", "late_minutes_violation",
              *PROTOCOL_DIAGNOSTIC_FIELDS, "route"]
    key_fields = ("node_count", "scenario", "seed", "budget_sec", "gamma", "algorithm")
    completed = _existing_keys(path, key_fields) if resume else set()
    completed |= _repair_keys(repair_from, key_fields, methods)
    handle, writer = _open_results(path, fields, resume)
    try:
        for gamma in (0.5, 1.0, 1.5, 2.0):
            for scenario in scenarios():
                for seed in seeds:
                    if _paused(pause_file):
                        print(f"[paused] checkpoint retained for {path}", flush=True)
                        return path
                    instance = make_instance(24, scenario, seed, gamma=gamma)
                    instance_prefix = tuple(str(value) for value in (24, scenario, seed, budget, gamma))
                    complete = _instance_has_all_results(
                        completed, instance_prefix,
                        expected_algorithms=methods if methods is not None else ALL_IET_METHODS,
                    )
                    if resume and complete:
                        continue
                    for row in _run_one(instance, seed, budget, methods=methods):
                        record = {"node_count": 24, "scenario": scenario, "seed": seed,
                                  "budget_sec": budget, "gamma": gamma, **row}
                        key = tuple(str(record.get(field, "")) for field in key_fields)
                        if key not in completed:
                            writer.writerow(record); handle.flush(); completed.add(key)
                    _save_archive(output, instance, (scenario, seed, gamma))
    finally:
        handle.close()
    return path


def run_scale(output: Path, seeds: list[int], resume: bool = False,
              pause_file: str | None = None, methods=None,
              repair_from: Path | None = None) -> Path:
    """Run the registered 24/48/80/120-node, 1/5/10/30-second matrix."""
    output.mkdir(parents=True, exist_ok=True)
    path = output / "scale_results.csv"
    _write_metadata(path, resume=resume, methods=methods, protocol="scale")
    fields = ["node_count", "scenario", "seed", "budget_sec", "gamma", "algorithm", "status",
              "runtime_sec", "solver_version", "error_message", "route_valid", "J_WL", "J_rank", "late_rate", "late_minutes", "mean_late_minutes",
              "total_distance", "total_travel_time", "first_half_top_quartile_coverage",
              "top_quartile_mean_service_position", "constraint_violation", "operationally_feasible",
              "distance_limit", "travel_time_limit", "late_minutes_limit",
              "distance_violation", "travel_time_violation", "late_minutes_violation",
              *PROTOCOL_DIAGNOSTIC_FIELDS, "route"]
    key_fields = ("node_count", "scenario", "seed", "budget_sec", "gamma", "algorithm")
    completed = _existing_keys(path, key_fields) if resume else set()
    completed |= _repair_keys(repair_from, key_fields, methods)
    handle, writer = _open_results(path, fields, resume)
    try:
        for node_count in (24, 48, 80, 120):
            for budget in (1.0, 5.0, 10.0, 30.0):
                for scenario in scenarios():
                    for seed in seeds:
                        if _paused(pause_file):
                            print(f"[paused] checkpoint retained for {path}", flush=True)
                            return path
                        instance = make_instance(node_count, scenario, seed)
                        instance_prefix = tuple(str(value) for value in (node_count, scenario, seed, budget, 1.0))
                        complete = _instance_has_all_results(
                            completed, instance_prefix,
                            expected_algorithms=methods if methods is not None else ALL_IET_METHODS,
                        )
                        if resume and complete:
                            continue
                        for row in _run_one(instance, seed, budget, methods=methods):
                            record = {"node_count": node_count, "scenario": scenario, "seed": seed,
                                      "budget_sec": budget, "gamma": 1.0, **row}
                            key = tuple(str(record.get(field, "")) for field in key_fields)
                            if key not in completed:
                                writer.writerow(record); handle.flush(); completed.add(key)
                        _save_archive(output, instance, (node_count, scenario, seed, budget))
    finally:
        handle.close()
    return path


def run_exact(output: Path, seeds: list[int], resume: bool = False,
              pause_file: str | None = None, methods=None,
              repair_from: Path | None = None) -> Path:
    """Compare the heuristic set with an exact primary-objective oracle."""
    output.mkdir(parents=True, exist_ok=True)
    path = output / "exact_small_results.csv"
    _write_metadata(path, resume=resume, methods=methods, protocol="exact")
    fields = ["node_count", "scenario", "seed", "algorithm", "status", "runtime_sec",
              "route_valid", "solver_version", "error_message", "J_WL", "J_rank", "optimality_gap", "late_rate", "late_minutes",
              "total_distance", "total_travel_time", "constraint_violation", "operationally_feasible",
              "distance_limit", "travel_time_limit", "late_minutes_limit", "distance_violation",
              "travel_time_violation", "late_minutes_violation",
              *PROTOCOL_DIAGNOSTIC_FIELDS, "route"]
    key_fields = ("node_count", "scenario", "seed", "algorithm")
    completed = _existing_keys(path, key_fields) if resume else set()
    completed |= _repair_keys(repair_from, key_fields, methods)
    handle, writer = _open_results(path, fields, resume)
    try:
        for node_count in (8, 12, 16):
            for scenario in scenarios():
                for seed in seeds:
                    if _paused(pause_file):
                        print(f"[paused] checkpoint retained for {path}", flush=True)
                        return path
                    instance = make_instance(node_count, scenario, seed)
                    exact_started = perf_counter()
                    instance_prefix = tuple(str(value) for value in (node_count, scenario, seed))
                    complete = _instance_has_all_results(
                        completed, instance_prefix,
                        expected_algorithms=methods if methods is not None else ALL_EXACT_METHODS,
                    )
                    if resume and complete:
                        continue
                    exact_route = solve_exact_weighted_latency(instance)
                    exact_runtime = perf_counter() - exact_started
                    exact_metrics = evaluate_route(exact_route, instance)
                    exact_value = float(exact_metrics["J_WL"])
                    candidates = [("Exact weighted-latency oracle", exact_route, exact_runtime, "available")]
                    baseline_outputs = run_delivery_baselines(
                            instance, seed=seed, time_limit_sec=0.20, include_amw=False,
                            include_historical=False, include_optional=False,
                            methods=methods)
                    if methods is None or ALGO_AMW_DELIVERY in methods:
                        amw_started = perf_counter()
                        try:
                            amw_route = solve_amw_priority_open_route(
                                instance, seed=seed, time_limit_sec=0.20,
                                constrained=AMW_CONSTRAINED,
                                priority_guarded=AMW_PRIORITY_GUARDED,
                                priority_tolerance=AMW_PRIORITY_TOLERANCE,
                            )
                            baseline_outputs.insert(0, SolverResult(
                                ALGO_AMW_DELIVERY, amw_route,
                                perf_counter() - amw_started, "available"))
                        except Exception as exc:
                            baseline_outputs.insert(0, SolverResult(
                                ALGO_AMW_DELIVERY, None,
                                perf_counter() - amw_started, "failed", str(exc)))
                    for name, route, runtime, status in baseline_outputs:
                        candidates.append((name, route, runtime, status))
                        saved = instance.metadata.get("jwl_alns_priority_only")
                        if name == ALGO_JWL_ALNS and saved:
                            candidates.append(("JWL-ALNS priority-only", saved["route"], runtime, "available"))
                    for name, route, runtime, status in candidates:
                        key = (str(node_count), str(scenario), str(seed), str(name))
                        if key in completed:
                            continue
                        if route is None:
                            writer.writerow({"node_count": node_count, "scenario": scenario, "seed": seed,
                                             "algorithm": name, "status": status, "runtime_sec": runtime,
                                             "route_valid": False, "solver_version": "", "error_message": "",
                                             **protocol_diagnostic_fields(instance, name)})
                            handle.flush(); completed.add(key)
                            continue
                        metrics = evaluate_route(route, instance)
                        constraint = constraint_summary(metrics, instance)
                        writer.writerow({"node_count": node_count, "scenario": scenario, "seed": seed,
                                         "algorithm": name, "status": status, "runtime_sec": runtime,
                                         "route_valid": True, "J_WL": metrics["J_WL"],
                                         "J_rank": metrics["weighted_rank_latency"],
                                         "optimality_gap": (float(metrics["J_WL"]) - exact_value) / max(exact_value, 1e-12),
                                         "late_rate": metrics["late_rate"], "late_minutes": metrics["late_minutes"],
                                         "total_distance": metrics["total_distance"],
                                         "total_travel_time": metrics["total_travel_time"],
                                         "constraint_violation": constraint["constraint_violation"],
                                         "operationally_feasible": constraint["operationally_feasible"],
                                         "distance_limit": constraint["distance_limit"],
                                         "travel_time_limit": constraint["travel_time_limit"],
                                         "late_minutes_limit": constraint["late_minutes_limit"],
                                         "distance_violation": constraint["distance_violation"],
                                         "travel_time_violation": constraint["travel_time_violation"],
                                         "late_minutes_violation": constraint["late_minutes_violation"],
                                         **protocol_diagnostic_fields(instance, name),
                                         "route": " ".join(map(str, route)),
                                         "solver_version": "", "error_message": ""})
                        handle.flush(); completed.add(key)
    finally:
        handle.close()
    return path


def run_permutation(output: Path, seeds: list[int], budget: float,
                    resume: bool = False, pause_file: str | None = None,
                    methods=None, repair_from: Path | None = None) -> Path:
    """Negative-control experiment with shuffled urgency labels."""
    output.mkdir(parents=True, exist_ok=True)
    path = output / "urgency_permutation_results.csv"
    _write_metadata(path, resume=resume, methods=methods, protocol="permutation")
    fields = ["node_count", "scenario", "seed", "budget_sec", "algorithm", "status",
              "runtime_sec", "route_valid", "J_WL", "J_rank", "late_rate", "late_minutes",
              "mean_late_minutes", "solver_version", "error_message", "total_distance", "total_travel_time",
              "first_half_top_quartile_coverage", "top_quartile_mean_service_position", "constraint_violation",
              "distance_limit", "travel_time_limit", "late_minutes_limit",
              "operationally_feasible", "distance_violation", "travel_time_violation", "late_minutes_violation",
              *PROTOCOL_DIAGNOSTIC_FIELDS, "route"]
    key_fields = ("node_count", "scenario", "seed", "budget_sec", "algorithm")
    completed = _existing_keys(path, key_fields) if resume else set()
    completed |= _repair_keys(repair_from, key_fields, methods)
    handle, writer = _open_results(path, fields, resume)
    try:
        for scenario in scenarios():
            for seed in seeds:
                if _paused(pause_file):
                    print(f"[paused] checkpoint retained for {path}", flush=True)
                    return path
                instance = make_instance(24, scenario, seed)
                instance_prefix = tuple(str(value) for value in (24, scenario, seed, budget))
                complete = _instance_has_all_results(
                    completed, instance_prefix,
                    expected_algorithms=methods if methods is not None else ALL_IET_METHODS,
                )
                if resume and complete:
                    continue
                rng = np.random.default_rng(seed + 104729)
                shuffled = instance.urgency[1:].copy(); rng.shuffle(shuffled)
                instance.urgency[1:] = shuffled
                for row in _run_one(instance, seed, budget, methods=methods):
                    record = {"node_count": 24, "scenario": scenario, "seed": seed,
                              "budget_sec": budget, **row}
                    key = tuple(str(record.get(field, "")) for field in key_fields)
                    if key not in completed:
                        writer.writerow(record); handle.flush(); completed.add(key)
    finally:
        handle.close()
    return path


def _write_jwl_pair(writer, completed, key_fields, record, instance):
    """Write both outputs of one protected JWL-ALNS search."""
    key = tuple(str(record.get(field, "")) for field in key_fields)
    if key not in completed:
        writer.writerow(record)
        completed.add(key)
    saved = instance.metadata.get("jwl_alns_priority_only")
    if not saved:
        return
    only = dict(record)
    only["algorithm"] = "JWL-ALNS priority-only"
    only["route"] = " ".join(map(str, saved["route"]))
    only["J_WL"] = saved["J_WL"]
    only["late_minutes"] = saved["late_minutes"]
    only["total_distance"] = saved["total_distance"]
    only["total_travel_time"] = saved["total_travel_time"]
    key = tuple(str(only.get(field, "")) for field in key_fields)
    if key not in completed:
        writer.writerow(only)
        completed.add(key)


def _save_archive(output: Path, instance, key: tuple) -> None:
    """Append the accepted archive so a band can be re-selected without searching again."""
    saved = instance.metadata.get("jwl_alns_priority_only")
    if not saved or "archive" not in saved:
        return
    path = output / "archives.csv"
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if new:
            writer.writerow(["key", "archive_size", "route_index", "route"])
        for index, route in enumerate(saved["archive"]):
            writer.writerow(["|".join(map(str, key)), saved["archive_size"], index, " ".join(map(str, route))])


def _selection_metrics(route, instance) -> dict[str, float]:
    from delivery_routing import evaluate_route
    metrics = evaluate_route(route, instance)
    return {"J_WL": metrics["J_WL"], "late_minutes": metrics["late_minutes"],
            "total_distance": metrics["total_distance"], "total_travel_time": metrics["total_travel_time"]}


def run_sensitivity(mode: str, output: Path, seeds: list[int], budget: float,
                    resume: bool = False, pause_file: str | None = None) -> Path:
    """Sensitivity runs for the JWL-ALNS protected rule only.

    Epsilon and archive capacity are read from one 48-route archive per
    instance. Lambda and the random high-urgency placement change the instance,
    so each of those settings is solved separately.
    """
    from baseline_adapters import _archive_add, _protected_band_route
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"{mode}_results.csv"
    values = {
        "epsilon": (0, 0.005, 0.01, 0.02, 0.03, 0.05, 0.055, 0.06, 0.075, 0.1),
        "capacity": (8, 16, 24, 48),
        "lambda": ((0.20, 0.60, 0.20), (0.0, 1.0, 0.0), (1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0), (0.20, 0.20, 0.60)),
        "concentrated_random": ("concentrated_random",),
    }[mode]
    fields = ["node_count", "scenario", "seed", "budget_sec", "parameter", "algorithm", "status",
              "runtime_sec", "route_valid", "J_WL", "late_minutes", "total_distance", "total_travel_time", "route"]
    key_fields = ("node_count", "scenario", "seed", "budget_sec", "parameter", "algorithm")
    completed = _existing_keys(path, key_fields) if resume else set()
    handle, writer = _open_results(path, fields, resume)
    try:
        scenarios_for_mode = ("concentrated_random",) if mode == "concentrated_random" else scenarios()
        for scenario in scenarios_for_mode:
            for seed in seeds:
                if _paused(pause_file):
                    print(f"[paused] checkpoint retained for {path}", flush=True)
                    return path
                pending = [parameter for parameter in values
                           if not (resume and _instance_has_methods(
                               completed, tuple(str(value) for value in (24, scenario, seed, budget, parameter)),
                               {"JWL-ALNS"}))]
                if not pending:
                    continue
                lambdas = (0.20, 0.60, 0.20)
                instance = make_instance(24, scenario, seed, lambdas=lambdas)
                if mode in ("epsilon", "capacity"):
                    started = perf_counter()
                    solve_jwl_alns(instance, seed, budget, priority_tolerance=0.06, archive_capacity=48)
                    runtime = perf_counter() - started
                    full_archive = instance.metadata["jwl_alns_priority_only"]["archive"]
                    for parameter in pending:
                        if mode == "capacity":
                            archive = _archive_add([], full_archive[0], instance, capacity=int(parameter))
                            for route in full_archive[1:]:
                                archive = _archive_add(archive, route, instance, capacity=int(parameter))
                            tolerance = 0.06
                        else:
                            archive = full_archive
                            tolerance = float(parameter)
                        primary, selected = _protected_band_route(archive, instance, tolerance)
                        for algorithm, route in (("JWL-ALNS priority-only", primary), ("JWL-ALNS", selected)):
                            record = {"node_count": 24, "scenario": scenario, "seed": seed, "budget_sec": budget,
                                      "parameter": str(parameter), "algorithm": algorithm, "status": "available",
                                      "runtime_sec": runtime, "route_valid": True,
                                      "route": " ".join(map(str, route)), **_selection_metrics(route, instance)}
                            key = tuple(str(record.get(field, "")) for field in key_fields)
                            if key not in completed:
                                writer.writerow(record)
                                completed.add(key)
                    handle.flush()
                    continue
                for parameter in pending:
                    instance = make_instance(24, scenario, seed, lambdas=parameter if mode == "lambda" else lambdas)
                    started = perf_counter()
                    route = solve_jwl_alns(instance, seed, budget, priority_tolerance=0.06)
                    _save_archive(output, instance, (scenario, seed, parameter))
                    runtime = perf_counter() - started
                    record = {"node_count": 24, "scenario": scenario, "seed": seed, "budget_sec": budget,
                              "parameter": str(parameter), "algorithm": "JWL-ALNS", "status": "available",
                              "runtime_sec": runtime, "route_valid": True, "route": " ".join(map(str, route)),
                              **_selection_metrics(route, instance)}
                    _write_jwl_pair(writer, completed, key_fields, record, instance)
                    handle.flush()
    finally:
        handle.close()
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(ROOT / "results" / "formal"))
    parser.add_argument("--mode", choices=["controlled", "ablation", "tradeoff", "scale", "exact", "permutation", "epsilon", "capacity", "lambda", "concentrated_random", "smoke"], default="smoke")
    parser.add_argument("--seeds", default=None)
    parser.add_argument("--budget-sec", type=float, default=5.0)
    parser.add_argument("--resume", action="store_true",
                        help="append to existing CSV and skip completed algorithm keys")
    parser.add_argument("--pause-file", default=None,
                        help="stop at the next instance boundary when this file exists")
    parser.add_argument("--selection-mode", choices=["legacy", "priority_guarded", "constrained"], default="priority_guarded",
                        help="JWL-ALNS output rule: J_WL-first, protected-band selection, or operational constraints first")
    parser.add_argument("--priority-tolerance", type=float, default=0.05,
                        help="relative J_WL band used only by --selection-mode priority_guarded")
    parser.add_argument("--methods", default=None,
                        help="comma-separated method aliases (amw,alns,static_alns,mlp_ils,priority_greedy,nearest,ortools,pyvrp); default: all")
    parser.add_argument("--repair-from", default=None,
                        help="older CSV whose successful rows are used as a skip-list for selective repair")
    args = parser.parse_args()
    if args.priority_tolerance < 0.0:
        parser.error("--priority-tolerance must be non-negative")
    try:
        methods = _parse_methods(args.methods)
    except ValueError as exc:
        parser.error(str(exc))
    if methods is not None and not methods:
        parser.error("--methods must contain at least one method")
    repair_from = Path(args.repair_from) if args.repair_from else None
    global AMW_CONSTRAINED, AMW_PRIORITY_GUARDED, AMW_PRIORITY_TOLERANCE
    AMW_CONSTRAINED = args.selection_mode == "constrained"
    AMW_PRIORITY_GUARDED = args.selection_mode == "priority_guarded"
    AMW_PRIORITY_TOLERANCE = float(args.priority_tolerance)
    if args.mode == "smoke":
        seeds, budget, node_count = [0, 1], 0.08, 24
    elif args.mode == "tradeoff":
        seeds = [int(x) for x in args.seeds.split(",")] if args.seeds else list(range(5))
        path = run_tradeoff(Path(args.output_dir), seeds, args.budget_sec,
                            resume=args.resume, pause_file=args.pause_file, methods=methods,
                            repair_from=repair_from)
        print(path)
        return
    elif args.mode == "scale":
        seeds = [int(x) for x in args.seeds.split(",")] if args.seeds else list(range(5))
        path = run_scale(Path(args.output_dir), seeds,
                         resume=args.resume, pause_file=args.pause_file, methods=methods,
                         repair_from=repair_from)
        print(path)
        return
    elif args.mode == "exact":
        seeds = [int(x) for x in args.seeds.split(",")] if args.seeds else list(range(30))
        path = run_exact(Path(args.output_dir), seeds,
                         resume=args.resume, pause_file=args.pause_file, methods=methods,
                         repair_from=repair_from)
        print(path)
        return
    elif args.mode == "permutation":
        seeds = [int(x) for x in args.seeds.split(",")] if args.seeds else list(range(20))
        path = run_permutation(Path(args.output_dir), seeds, args.budget_sec,
                               resume=args.resume, pause_file=args.pause_file, methods=methods,
                               repair_from=repair_from)
        print(path)
        return
    elif args.mode in ("epsilon", "capacity", "lambda", "concentrated_random"):
        seeds = [int(x) for x in args.seeds.split(",")] if args.seeds else list(range(20, 40))
        path = run_sensitivity(args.mode, Path(args.output_dir), seeds, args.budget_sec,
                               resume=args.resume, pause_file=args.pause_file)
        print(path)
        return
    else:
        seeds = [int(x) for x in args.seeds.split(",")] if args.seeds else list(range(20 if args.mode == "controlled" else 5))
        budget, node_count = args.budget_sec, 24
    output = Path(args.output_dir)
    if args.mode == "ablation":
        for name in ("full", "no_adaptive_weights", "no_priority_local_search", "single_solution"):
            path = run_controlled(output / name, seeds, budget, node_count,
                                  None if name == "full" else name,
                                  resume=args.resume, pause_file=args.pause_file, methods=methods,
                                  repair_from=repair_from)
            print(path)
    else:
        path = run_controlled(output, seeds, budget, node_count,
                              resume=args.resume, pause_file=args.pause_file, methods=methods,
                              repair_from=repair_from)
        print(path)


if __name__ == "__main__":
    main()
