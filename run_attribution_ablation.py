"""Attribution ablation: travel-time search against JWL-guided search.

The two JWL cells are read from the saved band results, because changing the
tolerance only reselects inside the recorded archive. The two travel-time cells
are one new search per instance. It differs from JWL-ALNS only in the cost used
by worst removal, repair, and acceptance: travel time instead of the
(J_WL, tardiness, distance) key. The initial route, the equal-probability
operator choice, the non-deteriorating acceptance, the archive, and the
protected-band selection are the same, so the four cells separate the search
objective from the output rule.

Both outputs of one search come from the same archive. ``priority-only`` is the
minimum-J_WL route; ``protected`` is the lexicographic route inside the
(1 + tolerance) band.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from time import perf_counter

import numpy as np

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alns import ALNS
from alns.stop import MaxRuntime

from baseline_adapters import (
    _JwlALNSState,
    _RecordingHillClimbing,
    _archive_add,
    _best_insertion_route,
    _open_route_greedy_repair,
    _open_route_random_removal,
    _open_route_worst_removal,
    _protected_band_route,
)
from controlled_priority_instances import make_instance
from delivery_routing import evaluate_route

SCENARIOS = ("routine", "tight_time_windows", "concentrated_urgency")
FIELDS = [
    "scenario", "seed", "search", "output", "runtime_sec", "archive_size",
    "J_WL", "late_minutes", "total_distance", "total_travel_time", "route",
]


class _TravelTimeState(_JwlALNSState):
    """Same route state as JWL-ALNS, accepted on travel time alone."""

    def objective(self) -> float:
        if self.unassigned or len(self.route) != len(self.context.cost_matrix):
            return float("inf")
        total = 0.0
        for start, end in zip(self.route, self.route[1:]):
            total += float(self.context.cost_matrix[start, end])
        return total


def solve_travel_time_search(instance, seed: int, time_limit_sec: float,
                             tolerance: float, archive_capacity: int = 24):
    """Travel-time destroy-and-repair with the JWL-ALNS archive and output rule."""
    rng = np.random.RandomState(int(seed))
    initial_route = _best_insertion_route(instance)
    archive = _archive_add([], initial_route, instance, capacity=int(archive_capacity))
    solver = ALNS(rng)
    solver.add_destroy_operator(_open_route_random_removal, name="random_removal")
    solver.add_destroy_operator(_open_route_worst_removal, name="worst_removal")
    solver.add_repair_operator(_open_route_greedy_repair, name="travel_time_insertion")
    solver.iterate(
        _TravelTimeState(tuple(initial_route), (), instance),
        __import__("alns.select", fromlist=["RandomSelect"]).RandomSelect(num_destroy=2, num_repair=1),
        _RecordingHillClimbing(archive, instance, capacity=int(archive_capacity)),
        MaxRuntime(max(float(time_limit_sec), 0.05)),
    )
    primary, selected = _protected_band_route(archive, instance, tolerance)
    return primary, selected, len(archive)


def _row(instance_key, search, output, route, runtime, archive_size, instance):
    metrics = evaluate_route(route, instance)
    scenario, seed = instance_key
    return {
        "scenario": scenario,
        "seed": seed,
        "search": search,
        "output": output,
        "runtime_sec": runtime,
        "archive_size": archive_size,
        "J_WL": metrics["J_WL"],
        "late_minutes": metrics["late_minutes"],
        "total_distance": metrics["total_distance"],
        "total_travel_time": metrics["total_travel_time"],
        "route": " ".join(str(node) for node in route),
    }


def _saved_jwl_rows(path: Path, tolerance: float):
    """Read the two JWL cells from the formal band results."""
    label = f"{tolerance:.1f}" if tolerance == 0 else str(tolerance)
    wanted = {
        "JWL-ALNS priority-only": "priority-only",
        "JWL-ALNS": "protected",
    }
    rows = []
    with path.open("r", newline="", encoding="utf-8") as handle:
        for record in csv.DictReader(handle):
            if record["epsilon"] != "0.05" or record["algorithm"] not in wanted:
                continue
            rows.append({
                "scenario": record["scenario"],
                "seed": record["seed"],
                "search": "jwl",
                "output": wanted[record["algorithm"]],
                "runtime_sec": "",
                "archive_size": record["archive_size"],
                "J_WL": record["J_WL"],
                "late_minutes": record["late_minutes"],
                "total_distance": record["total_distance"],
                "total_travel_time": record["total_travel_time"],
                "route": record["route"],
            })
    return rows


def run(output: Path, source: Path, seeds, budget: float, tolerance: float) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if output.exists() and output.stat().st_size > 0:
        with output.open("r", newline="", encoding="utf-8") as handle:
            done = {(row["scenario"], row["seed"]) for row in csv.DictReader(handle)}
    else:
        with output.open("w", newline="", encoding="utf-8") as handle:
            csv.DictWriter(handle, fieldnames=FIELDS).writeheader()
        for row in _saved_jwl_rows(source, tolerance):
            with output.open("a", newline="", encoding="utf-8") as handle:
                csv.DictWriter(handle, fieldnames=FIELDS).writerow(row)

    pending = [
        (scenario, seed)
        for scenario in SCENARIOS
        for seed in seeds
        if (scenario, str(seed)) not in done
    ]
    for index, (scenario, seed) in enumerate(pending, start=1):
        instance = make_instance(node_count=24, scenario=scenario, seed=seed)
        started = perf_counter()
        primary, selected, archive_size = solve_travel_time_search(
            instance, seed, budget, tolerance
        )
        runtime = perf_counter() - started
        with output.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            for output_name, route in (("priority-only", primary), ("protected", selected)):
                writer.writerow(_row((scenario, seed), "travel-time", output_name,
                                     route, f"{runtime:.3f}", archive_size, instance))
        print(f"[{index}/{len(pending)}] {scenario} seed {seed} archive {archive_size} "
              f"{runtime:.1f}s", flush=True)
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--source", required=True,
                        help="saved band results supplying the two JWL cells")
    parser.add_argument("--seeds", default="0-19")
    parser.add_argument("--budget-sec", type=float, default=5.0)
    parser.add_argument("--tolerance", type=float, default=0.05)
    args = parser.parse_args()
    text = args.seeds.strip()
    if "-" in text:
        start, end = (int(part) for part in text.split("-"))
        seeds = list(range(start, end + 1))
    else:
        seeds = [int(part) for part in text.split(",") if part.strip()]
    print(run(Path(args.output), Path(args.source), seeds, args.budget_sec, args.tolerance))


if __name__ == "__main__":
    main()
