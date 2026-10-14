"""Adapter for a transparent, normalized Amazon last-mile export.

The public challenge release contains several files and versions.  This
adapter intentionally accepts a small normalized CSV contract rather than
guessing undocumented columns.  A conversion script can map the official
route/stop/package fields into this contract and preserve the raw files.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

from delivery_routing import DeliveryInstance, urgency_from_components


REQUIRED_STOP_COLUMNS = {
    "route_id", "stop_id", "sequence", "tw_start", "tw_end",
    "service_seconds", "urgency",
}


def load_normalized_instances(directory: str | Path) -> list[DeliveryInstance]:
    """Load route-level JSON matrices and stop-level CSV records.

    Expected files:
      * ``stops.csv``: route_id, stop_id, sequence, tw_start, tw_end,
        service_seconds, urgency, optional release_time
      * ``matrices/<route_id>.json``: square travel-time matrix in stop order
      * optional ``historical_routes.csv``: route_id, sequence, stop_id
    """
    root = Path(directory)
    stops_path = root / "stops.csv"
    if not stops_path.exists():
        raise FileNotFoundError(f"Missing normalized stops file: {stops_path}")
    with stops_path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return []
    missing = REQUIRED_STOP_COLUMNS - set(rows[0])
    if missing:
        raise ValueError(f"stops.csv is missing columns: {sorted(missing)}")
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row["route_id"]), []).append(row)
    historical = _load_historical_routes(root / "historical_routes.csv")
    instances = []
    for route_id, route_rows in sorted(grouped.items()):
        route_rows.sort(key=lambda row: int(row["sequence"]))
        stop_to_index = {str(row["stop_id"]): index for index, row in enumerate(route_rows)}
        matrix_path = root / "matrices" / f"{route_id}.json"
        if not matrix_path.exists():
            raise FileNotFoundError(f"Missing travel-time matrix for route {route_id}: {matrix_path}")
        matrix = np.asarray(json.loads(matrix_path.read_text(encoding="utf-8")), dtype=float)
        n = len(route_rows)
        if matrix.shape != (n, n):
            raise ValueError(f"Matrix for route {route_id} has shape {matrix.shape}, expected {(n, n)}")
        distance_path = root / "matrices" / f"{route_id}.distance.json"
        distance_matrix = None
        if distance_path.exists():
            distance_matrix = np.asarray(json.loads(distance_path.read_text(encoding="utf-8")), dtype=float)
            if distance_matrix.shape != (n, n):
                raise ValueError(
                    f"Distance matrix for route {route_id} has shape {distance_matrix.shape}, expected {(n, n)}"
                )
        windows = np.asarray([[float(row["tw_start"]), float(row["tw_end"])] for row in route_rows])
        service = np.asarray([float(row["service_seconds"]) for row in route_rows])
        urgency = np.asarray([float(row["urgency"]) for row in route_rows])
        release_values = [row.get("release_time", "") for row in route_rows]
        release = np.asarray([float(value) if value else 0.0 for value in release_values]) if any(release_values) else None
        observed = historical.get(route_id)
        historical_route = None
        if observed is not None:
            try:
                historical_route = [stop_to_index[str(stop_id)] for stop_id in observed]
            except KeyError as exc:
                raise ValueError(f"Historical route {route_id} references unknown stop {exc.args[0]}") from exc
        metadata = {
            "source": "Amazon normalized export",
            "urgency_definition": "time-window slack proxy; waiting and business priority neutral",
        }
        if "x_m" in route_rows[0] and "y_m" in route_rows[0]:
            metadata["positions"] = [
                (float(row["x_m"]), float(row["y_m"])) for row in route_rows
            ]
        provenance_path = root / "provenance.json"
        if provenance_path.exists():
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            metadata["provenance"] = provenance
            if provenance.get("source"):
                metadata["source"] = provenance["source"]
            if provenance.get("urgency_definition"):
                metadata["urgency_definition"] = provenance["urgency_definition"]
            if provenance.get("distance_definition"):
                metadata["distance_definition"] = provenance["distance_definition"]
            for entry in provenance.get("instances") or []:
                if str(entry.get("route_id")) != str(route_id):
                    continue
                if "model_horizon" in entry:
                    metadata["model_horizon"] = float(entry["model_horizon"])
                break
        instances.append(DeliveryInstance(
            matrix, urgency, windows, service, route_id=route_id,
            historical_route=historical_route, release_times=release,
            distance_matrix=distance_matrix, metadata=metadata,
        ))
    return instances


def _load_historical_routes(path: Path) -> dict[str, list[int]]:
    if not path.exists():
        return {}
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    grouped: dict[str, list[tuple[int, str]]] = {}
    for row in rows:
        grouped.setdefault(str(row["route_id"]), []).append((int(row["sequence"]), str(row["stop_id"])))
    return {route_id: [stop for _, stop in sorted(values)] for route_id, values in grouped.items()}


def convert_slack_to_urgency(slack_seconds, gamma: float = 1.0) -> np.ndarray:
    """Public helper used by the calibration script; smaller slack is urgent."""
    slack = np.asarray(slack_seconds, dtype=float)
    neutral = np.zeros_like(slack)
    return urgency_from_components(neutral, slack, np.ones_like(slack), start_node=0, gamma=gamma)
