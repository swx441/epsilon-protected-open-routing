"""Instance-level summaries and paired comparisons for the IET protocol."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np

try:
    from scipy.stats import rankdata, wilcoxon
except ImportError:  # pragma: no cover - reported in output metadata
    rankdata = None
    wilcoxon = None


METRICS = ("J_WL", "J_rank", "late_rate", "late_minutes", "total_distance",
           "total_travel_time", "constraint_violation", "operationally_feasible")


def _float(row, key):
    value = row.get(key, "")
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return 1.0 if value.strip().lower() == "true" else 0.0
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if np.isfinite(value) else None


def _bootstrap_mean(values: np.ndarray, seed: int = 20260908, repeats: int = 2000):
    if len(values) == 0:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    samples = rng.choice(values, size=(repeats, len(values)), replace=True).mean(axis=1)
    return tuple(float(x) for x in np.quantile(samples, [0.025, 0.975]))


def _paired_rank_biserial(delta: np.ndarray) -> float:
    """Paired rank-biserial effect: positive means JWL-ALNS is better.

    All registered metrics are lower-is-better.  For paired differences
    ``JWL-ALNS - baseline``, negative values therefore favour JWL-ALNS.  The statistic
    uses ranks of absolute non-zero differences, consistent with Wilcoxon.
    """
    nonzero = delta[np.abs(delta) > 1e-12]
    if len(nonzero) == 0:
        return 0.0
    if rankdata is None:
        return float("nan")
    ranks = np.asarray(rankdata(np.abs(nonzero), method="average"), dtype=float)
    amw_better = float(ranks[nonzero < 0].sum())
    baseline_better = float(ranks[nonzero > 0].sum())
    return (amw_better - baseline_better) / max(amw_better + baseline_better, 1e-12)


def _holm_adjust(p_values: list[float]) -> list[float]:
    """Holm step-down adjustment while preserving input order."""
    finite = [(idx, value) for idx, value in enumerate(p_values) if np.isfinite(value)]
    adjusted = [float("nan")] * len(p_values)
    running = 0.0
    for rank, (idx, value) in enumerate(sorted(finite, key=lambda item: item[1])):
        corrected = min(1.0, (len(finite) - rank) * float(value))
        running = max(running, corrected)
        adjusted[idx] = running
    return adjusted


def _read(path: Path):
    with path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return [row for row in rows if row.get("status", "").startswith("available") and
            row.get("route_valid", "").lower() == "true"]


def analyze(path: Path, output_dir: Path) -> tuple[Path, Path]:
    rows = _read(path)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "summary.csv"
    pairwise_path = output_dir / "paired_amw_comparisons.csv"
    # External replay rows carry route_id; controlled rows do not.  Include it
    # whenever present so independent Amazon routes cannot overwrite one
    # another during paired grouping.
    has_route_id = any(str(row.get("route_id", "")).strip() for row in rows)
    group_keys = (("route_id",) if has_route_id else ()) + ("node_count", "scenario", "gamma", "ablation", "algorithm")
    groups = {}
    for row in rows:
        key = tuple(row.get(item, "") for item in group_keys)
        groups.setdefault(key, []).append(row)
    with summary_path.open("w", newline="", encoding="utf-8") as handle:
        fields = list(group_keys) + ["metric", "n", "mean", "median", "std", "ci95_low", "ci95_high"]
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
        for key, group in sorted(groups.items()):
            for metric in METRICS:
                values = np.asarray([_float(row, metric) for row in group], dtype=float)
                values = values[np.isfinite(values)]
                ci_low, ci_high = _bootstrap_mean(values)
                writer.writerow(dict(zip(group_keys, key), metric=metric, n=len(values),
                                     mean=float(np.mean(values)) if len(values) else float("nan"),
                                     median=float(np.median(values)) if len(values) else float("nan"),
                                     std=float(np.std(values, ddof=1)) if len(values) > 1 else 0.0,
                                     ci95_low=ci_low, ci95_high=ci_high))

    # Pair by all available experimental identifiers except algorithm/status.
    pair_keys = (("route_id",) if has_route_id else ()) + ("node_count", "scenario", "seed", "budget_sec", "gamma", "ablation")
    by_key = {}
    for row in rows:
        by_key.setdefault(tuple(row.get(item, "") for item in pair_keys), {})[row["algorithm"]] = row
    amw_name = next((name for group in by_key.values() for name in group if name.startswith("AMW-MOPP")), None)
    comparisons = []
    if amw_name:
        baseline_names = sorted({name for group in by_key.values() for name in group if name != amw_name})
        for baseline in baseline_names:
            for metric in METRICS:
                pairs = []
                for group in by_key.values():
                    if amw_name not in group or baseline not in group:
                        continue
                    a, b = _float(group[amw_name], metric), _float(group[baseline], metric)
                    if a is not None and b is not None:
                        pairs.append((a, b))
                if pairs:
                    a = np.asarray([x for x, _ in pairs]); b = np.asarray([y for _, y in pairs])
                    delta = a - b
                    p_value = float("nan")
                    if wilcoxon is not None and len(delta) >= 5 and np.any(np.abs(delta) > 1e-12):
                        p_value = float(wilcoxon(a, b, alternative="two-sided").pvalue)
                    comparisons.append({"baseline": baseline, "metric": metric, "n": len(delta),
                                        "amw_mean": float(np.mean(a)), "baseline_mean": float(np.mean(b)),
                                        "mean_delta_amw_minus_baseline": float(np.mean(delta)),
                                        "amw_better_rate": float(np.mean(delta < 0)), "wilcoxon_p": p_value,
                                        "scipy_available": wilcoxon is not None})
    adjusted_p = _holm_adjust([float(row["wilcoxon_p"]) for row in comparisons])
    for row, corrected in zip(comparisons, adjusted_p):
        row["holm_adjusted_p"] = corrected
        # The effect is computed from paired metric differences in the same
        # loop above and is attached below from the stored aggregate values.
    # Reconstruct paired effects so the CSV reports a directional effect size.
    for row in comparisons:
        pairs = []
        for group in by_key.values():
            if amw_name not in group or row["baseline"] not in group:
                continue
            a, b = _float(group[amw_name], row["metric"]), _float(group[row["baseline"]], row["metric"])
            if a is not None and b is not None:
                pairs.append(a - b)
        row["paired_rank_biserial"] = _paired_rank_biserial(np.asarray(pairs, dtype=float))
    with pairwise_path.open("w", newline="", encoding="utf-8") as handle:
        fields = ["baseline", "metric", "n", "amw_mean", "baseline_mean",
                  "mean_delta_amw_minus_baseline", "amw_better_rate", "wilcoxon_p",
                  "holm_adjusted_p", "paired_rank_biserial", "scipy_available"]
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(comparisons)
    return summary_path, pairwise_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    summary, pairwise = analyze(Path(args.input), Path(args.output_dir))
    print(summary)
    print(pairwise)


if __name__ == "__main__":
    main()
