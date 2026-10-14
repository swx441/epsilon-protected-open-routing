"""Recompute frozen operating-envelope columns from stored routes.

This script never calls a solver.  After L_ref / H_NN naming is frozen, every
method's envelope cells must be rebuilt from the stored open route and the
reconstructed instance.  Search diagnostics (anytime columns, J_WL, distance)
are left unchanged unless --overwrite-metrics is set.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from amazon_delivery_adapter import load_normalized_instances
from controlled_priority_instances import make_instance
from delivery_routing import constraint_summary, evaluate_route, protocol_diagnostic_fields


ENVELOPE_FIELDS = (
    "distance_limit",
    "travel_time_limit",
    "late_minutes_limit",
    "distance_violation",
    "travel_time_violation",
    "late_minutes_violation",
    "constraint_violation",
    "operationally_feasible",
    "L_ref",
    "model_horizon",
    "reference_horizon_nn",
)


def _parse_route(text: str) -> list[int] | None:
    if not text or not str(text).strip():
        return None
    return [int(token) for token in str(text).split()]


def _gamma(row: dict[str, str]) -> float:
    raw = row.get("gamma", "")
    if raw in ("", None):
        return 1.0
    return float(raw)


def _iet_instance(row: dict[str, str], permute_urgency: bool):
    instance = make_instance(
        int(row["node_count"]),
        str(row["scenario"]),
        int(row["seed"]),
        gamma=_gamma(row),
    )
    if permute_urgency:
        rng = np.random.default_rng(int(row["seed"]) + 104729)
        shuffled = instance.urgency[1:].copy()
        rng.shuffle(shuffled)
        instance.urgency[1:] = shuffled
    return instance


def _amazon_index(input_dir: Path) -> dict[str, object]:
    return {str(instance.route_id): instance for instance in load_normalized_instances(input_dir)}


def replay(protocol: str, rows: list[dict[str, str]], *, amazon_dir: Path | None = None,
           overwrite_metrics: bool = False) -> tuple[list[dict[str, str]], int, int]:
    permute = protocol == "permutation"
    amazon = _amazon_index(amazon_dir) if protocol == "amazon" else None
    updated = 0
    skipped = 0
    warnings = 0
    out = []
    for row in rows:
        route = _parse_route(row.get("route", ""))
        if route is None or str(row.get("route_valid", "")).lower() != "true":
            skipped += 1
            out.append(row)
            continue
        if protocol == "amazon":
            if amazon is None:
                raise ValueError("--amazon-dir is required for protocol amazon")
            instance = amazon.get(str(row.get("route_id", "")))
            if instance is None:
                skipped += 1
                out.append(row)
                continue
        else:
            instance = _iet_instance(row, permute_urgency=permute)
        metrics = evaluate_route(route, instance)
        constraint = constraint_summary(metrics, instance)
        diagnostics = protocol_diagnostic_fields(instance, row.get("algorithm", ""))
        for field in ENVELOPE_FIELDS:
            if field in constraint:
                row[field] = constraint[field]
            elif field in diagnostics:
                row[field] = diagnostics[field]
        stored = row.get("J_WL", "")
        if stored not in ("", None) and overwrite_metrics is False:
            try:
                if abs(float(stored) - float(metrics["J_WL"])) > 1e-8:
                    warnings += 1
            except (TypeError, ValueError):
                pass
        if overwrite_metrics:
            row["J_WL"] = metrics["J_WL"]
            row["J_rank"] = metrics["weighted_rank_latency"]
            row["late_rate"] = metrics["late_rate"]
            row["late_minutes"] = metrics["late_minutes"]
            row["mean_late_minutes"] = metrics["mean_late_minutes"]
            row["total_distance"] = metrics["total_distance"]
            row["total_travel_time"] = metrics["total_travel_time"]
        updated += 1
        out.append(row)
    print(f"replayed={updated} skipped={skipped} jwl_mismatch_warnings={warnings}", flush=True)
    return out, updated, skipped


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", choices=("iet", "exact", "permutation", "amazon"), required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--amazon-dir", default=None,
                        help="normalized Amazon/OSM bundle; required for --protocol amazon")
    parser.add_argument("--overwrite-metrics", action="store_true",
                        help="also replace J_WL / distance / tardiness from the stored route")
    args = parser.parse_args()
    source = Path(args.input)
    with source.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    if not fields:
        raise ValueError(f"empty CSV header: {source}")
    for field in ENVELOPE_FIELDS:
        if field not in fields:
            fields.append(field)
    amazon_dir = Path(args.amazon_dir) if args.amazon_dir else None
    updated_rows, _, _ = replay(
        args.protocol, rows, amazon_dir=amazon_dir, overwrite_metrics=args.overwrite_metrics,
    )
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(updated_rows)


if __name__ == "__main__":
    main()
