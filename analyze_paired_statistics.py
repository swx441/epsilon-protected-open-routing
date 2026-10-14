"""Revision statistics that do not rerun a solver.

Two outputs, both computed from an existing formal CSV:

- controlled uncertainty: median, IQR, a 95% bootstrap interval for the mean,
  and paired win/tie/loss against JWL-ALNS, for the four reported metrics;
- Amazon route-level pairs: five seeds are averaged inside each route before
  the bootstrap, so the analysis unit is the route, not the seed.

The 500-run Holm tests are intentionally not recomputed.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np

from analyze_results import _bootstrap_mean, _float, _holm_adjust, _paired_rank_biserial, _read

try:
    from scipy.stats import wilcoxon
except ImportError:  # pragma: no cover
    wilcoxon = None


METRICS = ("J_WL", "late_minutes", "total_distance", "total_travel_time")
AMW = "AMW-MOPP priority-aware open path"


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else ["empty"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _iqr(values: np.ndarray) -> float:
    if len(values) == 0:
        return float("nan")
    q1, q3 = np.quantile(values, [0.25, 0.75])
    return float(q3 - q1)


def _load_controlled(paths: list[Path]) -> list[dict]:
    """Load the manuscript sources, letting a later file replace the same key.

    Table 9 keeps the rerun of the same-objective methods and the earlier
    travel-time solvers. The two files share scenario and seed.
    """
    key_fields = ("node_count", "scenario", "seed", "budget_sec", "gamma", "ablation", "algorithm")
    merged: dict[tuple[str, ...], dict] = {}
    for path in paths:
        for row in _read(path):
            merged[tuple(row.get(field, "") for field in key_fields)] = row
    return list(merged.values())


def controlled_uncertainty(paths: list[Path], output_dir: Path) -> tuple[Path, Path]:
    rows = _load_controlled(paths)
    by_algorithm: dict[str, list[dict]] = {}
    for row in rows:
        by_algorithm.setdefault(row["algorithm"], []).append(row)
    summary = []
    for algorithm in sorted(by_algorithm):
        for metric in METRICS:
            values = np.asarray([_float(row, metric) for row in by_algorithm[algorithm]], dtype=float)
            values = values[np.isfinite(values)]
            low, high = _bootstrap_mean(values)
            summary.append({
                "algorithm": algorithm, "metric": metric, "n": len(values),
                "mean": float(np.mean(values)) if len(values) else float("nan"),
                "ci95_low": low, "ci95_high": high,
                "median": float(np.median(values)) if len(values) else float("nan"),
                "iqr": _iqr(values),
            })

    pair_fields = ("node_count", "scenario", "seed", "budget_sec", "gamma", "ablation")
    grouped: dict[tuple[str, ...], dict[str, dict]] = {}
    for row in rows:
        grouped.setdefault(tuple(row.get(field, "") for field in pair_fields), {})[row["algorithm"]] = row
    comparisons = []
    baselines = sorted({row["algorithm"] for row in rows if row["algorithm"] != AMW})
    for baseline in baselines:
        for metric in METRICS:
            deltas = []
            for group in grouped.values():
                if AMW not in group or baseline not in group:
                    continue
                left, right = _float(group[AMW], metric), _float(group[baseline], metric)
                if left is not None and right is not None:
                    deltas.append(left - right)
            if not deltas:
                continue
            delta = np.asarray(deltas, dtype=float)
            p_value = float("nan")
            if wilcoxon is not None and len(delta) >= 5 and np.any(np.abs(delta) > 1e-12):
                p_value = float(wilcoxon(delta, alternative="two-sided").pvalue)
            low, high = _bootstrap_mean(delta)
            comparisons.append({
                "baseline": baseline, "metric": metric, "n": len(delta),
                "mean_delta": float(np.mean(delta)), "ci95_low": low, "ci95_high": high,
                "win_rate": float(np.mean(delta < -1e-12)),
                "tie_rate": float(np.mean(np.abs(delta) <= 1e-12)),
                "loss_rate": float(np.mean(delta > 1e-12)),
                "wilcoxon_p": p_value,
                "paired_rank_biserial": _paired_rank_biserial(delta),
            })
    adjusted = _holm_adjust([float(row["wilcoxon_p"]) for row in comparisons])
    for row, corrected in zip(comparisons, adjusted):
        row["holm_adjusted_p"] = corrected
    summary_path = output_dir / "controlled_uncertainty.csv"
    pair_path = output_dir / "controlled_paired_wtl.csv"
    _write(summary_path, summary)
    _write(pair_path, comparisons)
    return summary_path, pair_path


def amazon_route_level(path: Path, output_dir: Path) -> Path:
    rows = _read(path)
    # Average the seeds of one route before any comparison. A route with a
    # missing method is dropped from that comparison rather than imputed.
    by_route: dict[tuple[str, str], list[float]] = {}
    for row in rows:
        key = (row.get("route_id", ""), row.get("algorithm", ""))
        value = _float(row, "J_WL")
        distance = _float(row, "total_distance")
        if value is None or distance is None:
            continue
        by_route.setdefault(key, []).append((value, distance))
    route_means = {
        key: (float(np.mean([item[0] for item in values])), float(np.mean([item[1] for item in values])))
        for key, values in by_route.items() if values
    }
    routes = sorted({route for route, _ in route_means})
    baselines = sorted({algorithm for _, algorithm in route_means if algorithm != AMW})
    comparisons = []
    for baseline in baselines:
        for metric_index, metric in ((0, "J_WL"), (1, "total_distance")):
            deltas = []
            for route in routes:
                left = route_means.get((route, AMW))
                right = route_means.get((route, baseline))
                if left is None or right is None:
                    continue
                deltas.append(left[metric_index] - right[metric_index])
            if not deltas:
                continue
            delta = np.asarray(deltas, dtype=float)
            p_value = float("nan")
            if wilcoxon is not None and len(delta) >= 5 and np.any(np.abs(delta) > 1e-12):
                p_value = float(wilcoxon(delta, alternative="two-sided").pvalue)
            low, high = _bootstrap_mean(delta)
            comparisons.append({
                "unit": "route", "baseline": baseline, "metric": metric, "n_routes": len(delta),
                "mean_delta": float(np.mean(delta)), "ci95_low": low, "ci95_high": high,
                "win_rate": float(np.mean(delta < -1e-12)),
                "tie_rate": float(np.mean(np.abs(delta) <= 1e-12)),
                "loss_rate": float(np.mean(delta > 1e-12)),
                "wilcoxon_p_route": p_value,
                "paired_rank_biserial": _paired_rank_biserial(delta),
            })
    adjusted = _holm_adjust([float(row["wilcoxon_p_route"]) for row in comparisons])
    for row, corrected in zip(comparisons, adjusted):
        row["holm_adjusted_p_route"] = corrected
    out = output_dir / "amazon_route_level.csv"
    _write(out, comparisons)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--controlled", required=True, nargs="+")
    parser.add_argument("--amazon", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    output = Path(args.output_dir)
    summary, pairs = controlled_uncertainty([Path(item) for item in args.controlled], output)
    amazon = amazon_route_level(Path(args.amazon), output)
    print(summary)
    print(pairs)
    print(amazon)


if __name__ == "__main__":
    main()
