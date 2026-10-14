"""Re-select stored archives at epsilon 0.05 and report manuscript aggregates.

The search is not rerun. The tolerance is applied only after the search, so
re-selecting a stored archive at a different epsilon is deterministic.
"""
import csv
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from controlled_priority_instances import make_instance
from baseline_adapters import _protected_band_route

TOL = 0.05


def load_archives(path):
    groups = defaultdict(list)
    order = []
    with open(path, encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            key = row["key"]
            if key not in groups:
                order.append(key)
            groups[key].append((int(row["route_index"]), [int(x) for x in row["route"].split()]))
    return [(key, [route for _, route in sorted(groups[key])]) for key in order]


def gamma_instance(key):
    scenario, seed, gamma = key.split("|")
    return make_instance(24, scenario, int(seed), gamma=float(gamma))


def lambda_instance(key):
    scenario, seed, raw = key.split("|", 2)
    lambdas = tuple(float(x) for x in raw.strip("()").split(","))
    return make_instance(24, scenario, int(seed), lambdas=lambdas)


def scale_instance(key):
    node_count, scenario, seed, _budget = key.split("|")
    return make_instance(int(node_count), scenario, int(seed))


def reselect(path, builder):
    out = []
    for key, archive in load_archives(path):
        instance = builder(key)
        primary, selected = _protected_band_route(archive, instance, TOL)
        out.append((key, primary, selected, instance))
    return out


def mean(values):
    return sum(values) / len(values)


def metrics_of(route, instance):
    from delivery_routing import evaluate_route
    m = evaluate_route(route, instance)
    return m["J_WL"], m["late_minutes"], m["total_distance"], m["total_travel_time"]


def report_gamma(rows):
    groups = defaultdict(list)
    for key, primary, selected, instance in rows:
        gamma = key.split("|")[2]
        groups[gamma].append((primary, selected, instance))
    print("== gamma (15 instances each)")
    for gamma in ("0.5", "1.0", "1.5", "2.0"):
        items = groups[gamma]
        sel = [metrics_of(s, inst) for _, s, inst in items]
        rewritten = sum(p != s for p, s, _ in items)
        print(gamma, len(items), rewritten,
              round(mean([m[0] for m in sel]), 6),
              round(mean([m[1] for m in sel]), 3),
              round(mean([m[2] for m in sel]), 1),
              round(mean([m[3] for m in sel]), 3))


def report_lambda(rows):
    groups = defaultdict(list)
    for key, primary, selected, instance in rows:
        raw = key.split("|", 2)[2]
        groups[raw].append((primary, selected, instance))
    print("== lambda (60 instances each)")
    for raw in groups:
        items = groups[raw]
        pri = [metrics_of(p, inst) for p, _, inst in items]
        sel = [metrics_of(s, inst) for _, s, inst in items]
        rewritten = sum(p != s for p, s, _ in items)
        j_pri, late_pri = mean([m[0] for m in pri]), mean([m[1] for m in pri])
        j_sel, late_sel = mean([m[0] for m in sel]), mean([m[1] for m in sel])
        print(raw, len(items), rewritten,
              round(j_sel, 6), round(late_sel, 3),
              round(mean([m[2] for m in sel]), 1), round(mean([m[3] for m in sel]), 3),
              round(100 * (j_sel / j_pri - 1), 4), round(100 * (late_sel / late_pri - 1), 4))


def report_scale(rows):
    groups = defaultdict(list)
    for key, primary, selected, instance in rows:
        node_count, _, _, budget = key.split("|")
        groups[(int(node_count), float(budget))].append((primary, selected, instance))
    print("== scale (15 instances each cell)")
    for node_count in (24, 48, 80, 120):
        cells = {}
        for budget in (1.0, 5.0, 10.0, 30.0):
            items = groups[(node_count, budget)]
            sel = [metrics_of(s, inst)[0] for _, s, inst in items]
            cells[budget] = sel
        a, b = cells[1.0], cells[30.0]
        improved = sum(x < y - 1e-12 for x, y in zip(b, a))
        print(node_count, [round(mean(cells[t]), 5) for t in (1.0, 5.0, 10.0, 30.0)],
              round(100 * (mean(b) / mean(a) - 1), 4), improved)


BASE = ROOT / "records"
report_gamma(reselect(BASE / "table17_curvature_archive.csv", gamma_instance))
report_lambda(reselect(BASE / "table18_weights_archive.csv", lambda_instance))
report_scale(reselect(BASE / "section66_tableA1_scale_archive.csv", scale_instance))
