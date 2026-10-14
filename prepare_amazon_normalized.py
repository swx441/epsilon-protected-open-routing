"""Convert the public Amazon LMRRC JSON export to the project CSV contract.

The conversion is deterministic and keeps the official route/stop identifiers
inside the normalized bundle.  It uses the evaluation split by default, while
the training split can be converted separately for calibration diagnostics.
The resulting urgency is an explicit proxy derived from customer time-window
slack; it is not an observed Amazon urgency field.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
from datetime import datetime, timedelta
from pathlib import Path


def _load(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _parse_datetime(value):
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("T", " ")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _relative_seconds(value, departure: datetime) -> float | None:
    parsed = _parse_datetime(value)
    if parsed is None:
        return None
    # Amazon fields are naive UTC timestamps; retain the documented local
    # numeric ordering and express all windows relative to departure.
    return float((parsed - departure).total_seconds())


def _input_paths(raw_root: Path, split: str) -> dict[str, Path]:
    if split == "evaluation":
        prefix = raw_root / "almrrc2021-data-evaluation"
        return {
            "route": prefix / "model_apply_inputs" / "eval_route_data.json",
            "package": prefix / "model_apply_inputs" / "eval_package_data.json",
            "travel": prefix / "model_apply_inputs" / "eval_travel_times.json",
            "sequence": prefix / "model_score_inputs" / "eval_actual_sequences.json",
        }
    prefix = raw_root / "almrrc2021-data-training"
    return {
        "route": prefix / "model_build_inputs" / "route_data.json",
        "package": prefix / "model_build_inputs" / "package_data.json",
        "travel": prefix / "model_build_inputs" / "travel_times.json",
        "sequence": prefix / "model_build_inputs" / "actual_sequences.json",
    }


def _ordered_stops(route_record: dict, sequence_record: dict) -> list[str]:
    stops = route_record.get("stops", {})
    actual = sequence_record.get("actual", {}) if isinstance(sequence_record, dict) else {}
    # The station must be the fixed open-route start.  Remaining stops follow
    # the observed sequence only for deterministic serialization; solvers may
    # change their order during evaluation.
    station = [sid for sid, row in stops.items() if str(row.get("type", "")).lower() == "station"]
    station_id = station[0] if station else None
    ordered = sorted(
        (sid for sid in stops if sid != station_id),
        key=lambda sid: (float(actual.get(sid, 10**9)), str(sid)),
    )
    return ([station_id] if station_id is not None else []) + ordered


def _stop_attributes(stop_id: str, package_rows: dict, departure: datetime, horizon: float,
                      lower_bound_travel_seconds: float = 0.0):
    packages = package_rows.get(stop_id, {}) if isinstance(package_rows, dict) else {}
    service = 0.0
    ends: list[float] = []
    starts: list[float] = []
    for package in packages.values():
        service += float(package.get("planned_service_time_seconds", 0.0) or 0.0)
        window = package.get("time_window", {}) or {}
        start = _relative_seconds(window.get("start_time_utc"), departure)
        end = _relative_seconds(window.get("end_time_utc"), departure)
        if start is not None and end is not None and math.isfinite(start) and math.isfinite(end):
            starts.append(max(0.0, start))
            ends.append(max(0.0, end))
    if not packages:
        service = 0.0
    if starts and ends:
        # A stop with multiple packages is assigned the conservative envelope;
        # invalid or overnight intersections are represented by the envelope.
        early = min(starts)
        late = max(ends)
        if late < early:
            early, late = 0.0, horizon
        due = late
    else:
        early, late, due = 0.0, horizon, horizon
    # Priority is based on *remaining* due-time slack, not merely on the
    # width of a customer's window.  The lower bound is the station-to-stop
    # travel time supplied by the public matrix.  The 60 s floor is fixed
    # before evaluation to avoid unstable weights for near-zero slack.
    remaining_slack = max(due - max(early, lower_bound_travel_seconds), 0.0)
    urgency = 1.0 / (remaining_slack + 60.0)
    return early, late, service, urgency


def convert(raw_root: Path, output: Path, split: str, max_routes: int, min_stops: int, max_stops: int, selection_seed: int) -> dict:
    paths = _input_paths(raw_root, split)
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing Amazon input files:\n" + "\n".join(missing))
    route_data = _load(paths["route"])
    package_data = _load(paths["package"])
    travel_data = _load(paths["travel"])
    sequence_data = _load(paths["sequence"])
    route_ids = sorted(route_data)
    candidates = []
    for route_id in route_ids:
        stops = route_data[route_id].get("stops", {})
        count = len(stops)
        if min_stops <= count <= max_stops:
            digest = hashlib.sha256(f"{selection_seed}:{route_id}".encode("utf-8")).hexdigest()
            candidates.append((digest, route_id))
    selected = [route_id for _, route_id in sorted(candidates)[:max_routes]]
    if not selected:
        raise ValueError("No routes satisfy the requested stop-count range")

    output.mkdir(parents=True, exist_ok=True)
    matrix_dir = output / "matrices"
    matrix_dir.mkdir(exist_ok=True)
    stops_path = output / "stops.csv"
    history_path = output / "historical_routes.csv"
    manifest_path = output / "conversion_manifest.json"
    stop_rows = []
    history_rows = []
    written = []
    for route_id in selected:
        route = route_data[route_id]
        seq = sequence_data.get(route_id, {}).get("actual", {})
        ordered = _ordered_stops(route, sequence_data.get(route_id, {}))
        if not ordered or route_id not in travel_data:
            continue
        matrix_source = travel_data[route_id]
        if any(node not in matrix_source for node in ordered):
            continue
        if any(destination not in matrix_source[source] for source in ordered for destination in ordered):
            continue
        departure = _parse_datetime(
            f"{route.get('date_YYYY_MM_DD', '1970-01-01')} {route.get('departure_time_utc', '00:00:00')}"
        ) or datetime(1970, 1, 1)
        raw_matrix = [[float(matrix_source[source][destination]) for destination in ordered] for source in ordered]
        horizon = max(max(row) for row in raw_matrix) * max(len(ordered), 1) * 2.0 + 3600.0
        route_urgency = []
        for index, stop_id in enumerate(ordered):
            if index == 0:
                early, late, service, urgency = 0.0, horizon, 0.0, 0.0
            else:
                early, late, service, urgency = _stop_attributes(
                    stop_id, package_data.get(route_id, {}), departure, horizon,
                    lower_bound_travel_seconds=float(raw_matrix[0][index]),
                )
            route_urgency.append(urgency)
            stop_rows.append({
                "route_id": route_id,
                "stop_id": stop_id,
                "sequence": index,
                "tw_start": round(early, 3),
                "tw_end": round(late, 3),
                "service_seconds": round(service, 3),
                "urgency": urgency,
            })
            history_rows.append({"route_id": route_id, "sequence": index, "stop_id": stop_id})
        (matrix_dir / f"{route_id}.json").write_text(json.dumps(raw_matrix), encoding="utf-8")
        written.append(route_id)

    with stops_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["route_id", "stop_id", "sequence", "tw_start", "tw_end", "service_seconds", "urgency"])
        writer.writeheader()
        writer.writerows(row for row in stop_rows if row["route_id"] in written)
    with history_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["route_id", "sequence", "stop_id"])
        writer.writeheader()
        writer.writerows(row for row in history_rows if row["route_id"] in written)
    manifest = {
        "source": "2021 Amazon Last Mile Routing Research Challenge",
        "split": split,
        "selected_route_count": len(written),
        "requested_max_routes": max_routes,
        "selection_seed": selection_seed,
        "stop_range": [min_stops, max_stops],
        "urgency_mapping": "1 / (max(time_window_due - max(window_start, station_to_stop_travel_lower_bound), 0) + 60 seconds), normalized per route by DeliveryInstance",
        "route_ids": written,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--split", choices=["evaluation", "training"], default="evaluation")
    parser.add_argument("--max-routes", type=int, default=100)
    parser.add_argument("--min-stops", type=int, default=20)
    parser.add_argument("--max-stops", type=int, default=80)
    parser.add_argument("--selection-seed", type=int, default=20260906)
    args = parser.parse_args()
    manifest = convert(Path(args.raw_root), Path(args.output), args.split, args.max_routes, args.min_stops, args.max_stops, args.selection_seed)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
