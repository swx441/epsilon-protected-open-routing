"""Run the registered solver set on the normalized Amazon replay bundle.

The Amazon data are an external route-distribution replay set.  This runner
uses the same DeliveryInstance contract and evaluator as the controlled suite;
it does not infer an observed business priority from the public data.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from amazon_delivery_adapter import load_normalized_instances
from baseline_adapters import (
    ALGO_AMW_DELIVERY,
    SolverResult,
    run_delivery_baselines,
    solve_amw_priority_open_route,
)
from delivery_routing import (
    PROTOCOL_DIAGNOSTIC_FIELDS,
    constraint_summary,
    evaluate_route,
    protocol_diagnostic_fields,
)


METHOD_ALIASES = {
    "amw": ALGO_AMW_DELIVERY,
    "alns": "Native ALNS",
    "native_alns": "Native ALNS",
    "static_alns": "Randomized Local Search comparator",
    "mlp_ils": "Weighted Minimum-Latency ILS",
    "weighted_mlp_ils": "Weighted Minimum-Latency ILS",
    "jwl_alns": "JWL-ALNS",
    "priority_greedy": "Priority-distance greedy heuristic",
    "nearest": "Travel-time nearest-neighbour baseline",
    "ortools": "OR-Tools Open-SVRPTW",
    "pyvrp": "PyVRP-HGS Open-SVRPTW",
    "historical": "Historical driver route replay",
}
ALL_AMAZON_METHODS = frozenset(METHOD_ALIASES.values())


def _parse_methods(value: str | None):
    if not value or value.strip().lower() in {"all", "*"}:
        return None
    selected = {METHOD_ALIASES.get(token.strip().lower(), token.strip())
                for token in value.split(",") if token.strip()}
    unknown = selected.difference(set(METHOD_ALIASES.values()))
    if unknown:
        raise ValueError(f"Unknown method(s): {', '.join(sorted(unknown))}")
    return selected


def _route_seed_has_methods(completed, prefix, methods):
    if methods is None:
        return False
    present = {key[2] for key in completed if key[:2] == prefix}
    return set(methods).issubset(present)


def _repair_keys(path: Path | None, methods=None):
    """Load successful route/seed/method keys from an older CSV."""
    if path is None or not path.exists():
        return set()
    with path.open("r", newline="", encoding="utf-8") as handle:
        rows = csv.DictReader(handle)
        keys = {
            (row.get("route_id", ""), row.get("seed", ""), row.get("algorithm", ""))
            for row in rows
            if row.get("status") == "available"
            and row.get("route_valid", "").lower() == "true"
        }
    if methods is None:
        return keys
    return {key for key in keys if key[2] in set(methods)}


FIELDS = [
    "route_id", "node_count", "seed", "budget_sec", "algorithm", "status",
    "runtime_sec", "solver_version", "error_message", "route_valid", "J_WL",
    "J_rank", "late_rate", "late_minutes", "mean_late_minutes",
    "total_distance", "total_travel_time", "first_half_top_quartile_coverage",
    "top_quartile_mean_service_position", "constraint_violation", "operationally_feasible",
    "distance_limit", "travel_time_limit", "late_minutes_limit",
    "distance_violation", "travel_time_violation", "late_minutes_violation",
    *PROTOCOL_DIAGNOSTIC_FIELDS, "route",
]


def _run_one(
    instance,
    seed: int,
    budget: float,
    constrained: bool = False,
    priority_guarded: bool = False,
    priority_tolerance: float = 0.05,
    methods=None,
):
    started = perf_counter()
    outputs = []
    if methods is None or ALGO_AMW_DELIVERY in methods:
        try:
            route = solve_amw_priority_open_route(
                instance, seed, budget, constrained=constrained,
                priority_guarded=priority_guarded,
                priority_tolerance=priority_tolerance,
            )
            outputs = [SolverResult(ALGO_AMW_DELIVERY, route, perf_counter() - started, "available")]
        except Exception as exc:
            outputs = [SolverResult(ALGO_AMW_DELIVERY, None, perf_counter() - started, "failed", str(exc))]

    outputs.extend(run_delivery_baselines(
        instance,
        seed=seed,
        time_limit_sec=budget,
        include_amw=False,
        include_historical=True,
        include_optional=True,
        methods=methods,
        priority_tolerance=priority_tolerance,
    ))
    saved = instance.metadata.get("jwl_alns_priority_only")
    jwl = next((item for item in outputs if item.algorithm == "JWL-ALNS"), None)
    if saved and jwl is not None and jwl.status == "available":
        outputs.append(SolverResult(
            "JWL-ALNS priority-only", saved["route"], jwl.runtime_sec, "available"
        ))
    rows = []
    for result in outputs:
        name, route, runtime, status = result
        row = {
            "route_id": instance.route_id,
            "node_count": instance.node_count,
            "seed": seed,
            "budget_sec": budget,
            "algorithm": name,
            "status": status,
            "runtime_sec": runtime,
            "solver_version": result.solver_version,
            "error_message": result.error_message,
            "route_valid": False,
        }
        if route is not None:
            metrics = evaluate_route(route, instance)
            constraint = constraint_summary(metrics, instance)
            row.update({
                "route_valid": True,
                "J_WL": metrics["J_WL"],
                "J_rank": metrics["weighted_rank_latency"],
                "late_rate": metrics["late_rate"],
                "late_minutes": metrics["late_minutes"],
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
            })
        else:
            row.update(protocol_diagnostic_fields(instance, name))
        rows.append(row)
    return rows


def _write_archive(output: Path, instance) -> None:
    """Append the accepted nondominated archive of one JWL-ALNS search.

    Recording only. The route written to the results CSV is still the one
    selected inside the (1+epsilon) band, and nothing here reselects it.
    """
    saved = instance.metadata.get("jwl_alns_priority_only")
    if not saved or "archive" not in saved:
        return
    path = output.parent / "archives.csv"
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if new:
            writer.writerow(["route_id", "seed", "archive_size", "route_index", "route"])
        seed = instance.metadata.get("_archive_seed", "")
        for index, route in enumerate(saved["archive"]):
            writer.writerow([instance.route_id, seed, saved["archive_size"], index,
                             " ".join(map(str, route))])


def run(input_dir: Path, output: Path, seeds: list[int], budget: float,
        resume: bool = False, pause_file: str | None = None,
        constrained: bool = False, priority_guarded: bool = False,
        priority_tolerance: float = 0.05, methods=None,
        repair_from: Path | None = None, save_archive: bool = False) -> Path:
    instances = load_normalized_instances(input_dir)
    if not instances:
        raise ValueError("The normalized Amazon bundle contains no routes")
    output.parent.mkdir(parents=True, exist_ok=True)
    meta_path = output.with_suffix(".meta.json")
    metadata = {
        "protocol": "amazon",
        "amw_mode": "constrained" if constrained else ("priority_guarded" if priority_guarded else "legacy"),
        "priority_tolerance": priority_tolerance,
        "methods": sorted(methods) if methods is not None else "all",
    }
    if resume and meta_path.exists():
        old = json.loads(meta_path.read_text(encoding="utf-8"))
        if (old.get("amw_mode"), old.get("priority_tolerance"), old.get("methods")) != (
            metadata["amw_mode"], metadata["priority_tolerance"], metadata["methods"]
        ):
            raise ValueError(f"Metadata mismatch for {output}; use a new output file")
    if not meta_path.exists() or not resume:
        meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    append = resume and output.exists() and output.stat().st_size > 0
    completed = set()
    if append:
        with output.open("r", newline="", encoding="utf-8") as existing:
            existing_fields = next(csv.reader(existing), [])
            if existing_fields != FIELDS:
                raise ValueError(
                    f"Existing CSV schema differs for {output}; use a new output file "
                    "when switching the selection mode or protocol fields."
                )
        with output.open("r", newline="", encoding="utf-8") as existing:
            completed = {
                (row.get("route_id", ""), row.get("seed", ""), row.get("algorithm", ""))
                for row in csv.DictReader(existing)
                if row.get("status") == "available"
                and row.get("route_valid", "").lower() == "true"
            }
    completed |= _repair_keys(repair_from, methods)
    with output.open("a" if append else "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if not append:
            writer.writeheader(); handle.flush()
        for index, instance in enumerate(instances, start=1):
            if pause_file and Path(pause_file).exists():
                print(f"[paused] checkpoint retained for {output}", flush=True)
                return output
            for seed in seeds:
                # Skip the costly solver call when this route/seed already
                # has a complete solver set in the checkpoint CSV.  The old
                # implementation only skipped duplicate *writes* after all
                # solvers had been re-run, making resume impractical.
                route_seed_prefix = (str(instance.route_id), str(seed))
                complete = _route_seed_has_methods(
                    completed, route_seed_prefix,
                    methods if methods is not None else ALL_AMAZON_METHODS,
                )
                if resume and complete:
                    continue
                instance.metadata["_archive_seed"] = seed
                for row in _run_one(
                    instance, seed, budget, constrained=constrained,
                    priority_guarded=priority_guarded,
                    priority_tolerance=priority_tolerance,
                    methods=methods,
                ):
                    key = (str(row.get("route_id", "")), str(row.get("seed", "")),
                           str(row.get("algorithm", "")))
                    if key not in completed:
                        writer.writerow(row); handle.flush(); completed.add(key)
                if save_archive:
                    _write_archive(output, instance)
            print(f"[{index}/{len(instances)}] {instance.route_id}", flush=True)
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--seeds", default="0,1,2,3,4")
    parser.add_argument("--budget-sec", type=float, default=30.0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--pause-file", default=None)
    parser.add_argument("--selection-mode", choices=["legacy", "priority_guarded", "constrained"], default="priority_guarded")
    parser.add_argument("--priority-tolerance", type=float, default=0.05,
                        help="relative J_WL band used only by --selection-mode priority_guarded")
    parser.add_argument("--methods", default=None,
                        help="comma-separated method aliases; default: all")
    parser.add_argument("--repair-from", default=None,
                        help="older Amazon CSV whose successful rows are used as a skip-list")
    parser.add_argument("--save-archive", action="store_true",
                        help="also write the accepted nondominated archive; does not change selection")
    args = parser.parse_args()
    if args.priority_tolerance < 0.0:
        parser.error("--priority-tolerance must be non-negative")
    try:
        methods = _parse_methods(args.methods)
    except ValueError as exc:
        parser.error(str(exc))
    seeds = [int(value) for value in args.seeds.split(",") if value.strip()]
    repair_from = Path(args.repair_from) if args.repair_from else None
    print(run(Path(args.input_dir), Path(args.output), seeds, args.budget_sec,
              resume=args.resume, pause_file=args.pause_file,
              constrained=args.selection_mode == "constrained",
              priority_guarded=args.selection_mode == "priority_guarded",
              priority_tolerance=args.priority_tolerance, methods=methods,
              repair_from=repair_from, save_archive=args.save_archive))


if __name__ == "__main__":
    main()
