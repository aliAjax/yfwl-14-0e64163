#!/usr/bin/env python3
"""Recover receiver assignments, clock shifts, and a linear slowness model."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path


TOP_KEYS = {"schema_version", "case_id", "sites", "events", "traces", "bounds"}
SITE_KEYS = {"site_code", "receiver_km"}
EVENT_KEYS = {"event_code", "hypocenter_km"}
TRACE_KEYS = {"trace_code", "arrival_s"}
BOUND_KEYS = {
    "min_sites",
    "max_sites",
    "min_events",
    "max_events",
    "max_abs_clock_shift_s",
    "min_surface_km_s",
    "max_surface_km_s",
    "min_depth_gradient",
    "max_depth_gradient",
}
CHECK_KEYS = {"schema_version", "completed"}
ENTRY_KEYS = {"case_id", "fingerprint", "station_map_sha256"}


class SeismicInputError(ValueError):
    pass


def duplicate_guard(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SeismicInputError("duplicate JSON key")
        result[key] = value
    return result


def exact_keys(value, expected, label):
    if not isinstance(value, dict):
        raise SeismicInputError(f"{label} must be an object")
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    if missing:
        raise SeismicInputError(f"{label} missing key {missing[0]}")
    if extra:
        raise SeismicInputError(f"{label} extra key {extra[0]}")


def text(value, label):
    if not isinstance(value, str) or not value or len(value) > 48:
        raise SeismicInputError(f"{label} is not valid text")
    if "/" in value or "\\" in value:
        raise SeismicInputError(f"{label} has a path separator")
    return value


def number(value, label, positive=False):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SeismicInputError(f"{label} is not numeric")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise SeismicInputError(f"{label} is not finite") from exc
    if not math.isfinite(result):
        raise SeismicInputError(f"{label} is not finite")
    if positive and result <= 0:
        raise SeismicInputError(f"{label} must be positive")
    return result


def triple(value, label):
    if not isinstance(value, list) or len(value) != 3:
        raise SeismicInputError(f"{label} must contain three coordinates")
    return [number(item, label) for item in value]


def validate(payload):
    exact_keys(payload, TOP_KEYS, "payload")
    if payload["schema_version"] != 1 or isinstance(payload["schema_version"], bool):
        raise SeismicInputError("schema_version must be integer 1")
    text(payload["case_id"], "case_id")
    bounds = payload["bounds"]
    exact_keys(bounds, BOUND_KEYS, "bounds")
    if bounds["min_sites"] != 6 or bounds["max_sites"] != 6:
        raise SeismicInputError("site bounds must be exactly 6")
    if bounds["min_events"] != 8 or bounds["max_events"] != 8:
        raise SeismicInputError("event bounds must be exactly 8")
    if number(bounds["max_abs_clock_shift_s"], "max_abs_clock_shift_s", True) != 1.2:
        raise SeismicInputError("clock shift bound must be 1.2")
    if number(bounds["min_surface_km_s"], "min_surface_km_s", True) != 5.5:
        raise SeismicInputError("minimum surface velocity must be 5.5")
    if number(bounds["max_surface_km_s"], "max_surface_km_s", True) != 7.5:
        raise SeismicInputError("maximum surface velocity must be 7.5")
    if number(bounds["min_depth_gradient"], "min_depth_gradient") != -0.02:
        raise SeismicInputError("minimum depth gradient must be -0.02")
    if number(bounds["max_depth_gradient"], "max_depth_gradient") != 0.02:
        raise SeismicInputError("maximum depth gradient must be 0.02")
    sites = payload["sites"]
    events = payload["events"]
    traces = payload["traces"]
    if not isinstance(sites, list) or not isinstance(events, list) or not isinstance(traces, list):
        raise SeismicInputError("sites, events and traces must be arrays")
    if len(sites) != 6:
        raise SeismicInputError("exactly six sites are required")
    if len(events) != 8:
        raise SeismicInputError("exactly eight events are required")
    if len(traces) != 6:
        raise SeismicInputError("exactly six traces are required")
    site_codes = set()
    for index, site in enumerate(sites):
        exact_keys(site, SITE_KEYS, f"sites[{index}]")
        code = text(site["site_code"], "site_code")
        if code in site_codes:
            raise SeismicInputError("site_code must be unique")
        site_codes.add(code)
        triple(site["receiver_km"], "receiver_km")
    event_codes = set()
    for index, event in enumerate(events):
        exact_keys(event, EVENT_KEYS, f"events[{index}]")
        code = text(event["event_code"], "event_code")
        if code in event_codes:
            raise SeismicInputError("event_code must be unique")
        event_codes.add(code)
        triple(event["hypocenter_km"], "hypocenter_km")
    trace_codes = set()
    for index, trace in enumerate(traces):
        exact_keys(trace, TRACE_KEYS, f"traces[{index}]")
        code = text(trace["trace_code"], "trace_code")
        if code in trace_codes:
            raise SeismicInputError("trace_code must be unique")
        trace_codes.add(code)
        arrivals = trace["arrival_s"]
        if not isinstance(arrivals, list) or len(arrivals) != 8:
            raise SeismicInputError("each arrival list must contain eight values")
        for value in arrivals:
            number(value, "arrival_s")
    return payload


def read_input(path):
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise SeismicInputError(f"cannot read input: {exc.strerror}") from exc
    if not raw.endswith(b"\n"):
        raise SeismicInputError("input must end with a newline")
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=duplicate_guard)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SeismicInputError("input is not valid UTF-8 JSON") from exc
    return raw, validate(payload)


def center_rows(rows):
    return [
        [value - sum(row) / len(row) for value in row]
        for row in rows
    ]


def center_matrix(rows):
    row_centered = center_rows(rows)
    columns = len(rows[0])
    column_means = [
        sum(row[index] for row in row_centered) / len(rows)
        for index in range(columns)
    ]
    return [
        [row[index] - column_means[index] for index in range(columns)]
        for row in row_centered
    ]


def travel_basis(site, event):
    receiver = site["receiver_km"]
    source = event["hypocenter_km"]
    distance = math.sqrt(
        sum((receiver[index] - source[index]) ** 2 for index in range(3))
    )
    depth_term = distance * (receiver[2] + source[2]) / 100.0
    return distance, depth_term


def hungarian(cost):
    size = len(cost)
    row_potential = [0.0] * (size + 1)
    column_potential = [0.0] * (size + 1)
    matching = [0] * (size + 1)
    previous = [0] * (size + 1)
    for row in range(1, size + 1):
        matching[0] = row
        column0 = 0
        minimum = [math.inf] * (size + 1)
        used = [False] * (size + 1)
        while True:
            used[column0] = True
            row0 = matching[column0]
            delta = math.inf
            column1 = 0
            for column in range(1, size + 1):
                if used[column]:
                    continue
                current = (
                    cost[row0 - 1][column - 1]
                    - row_potential[row0]
                    - column_potential[column]
                )
                if current < minimum[column]:
                    minimum[column] = current
                    previous[column] = column0
                if minimum[column] < delta:
                    delta = minimum[column]
                    column1 = column
            for column in range(size + 1):
                if used[column]:
                    row_potential[matching[column]] += delta
                    column_potential[column] -= delta
                else:
                    minimum[column] -= delta
            column0 = column1
            if matching[column0] == 0:
                break
        while True:
            column1 = previous[column0]
            matching[column0] = matching[column1]
            column0 = column1
            if column0 == 0:
                break
    assignment = [0] * size
    for column in range(1, size + 1):
        if matching[column]:
            assignment[matching[column] - 1] = column - 1
    return assignment


def solve_two_by_two(matrix, vector):
    first = matrix[0][0]
    cross = matrix[0][1]
    second = matrix[1][1]
    determinant = first * second - cross * cross
    if abs(determinant) < 1e-14:
        raise SeismicInputError("velocity system is singular")
    left = (vector[0] * second - vector[1] * cross) / determinant
    right = (first * vector[1] - cross * vector[0]) / determinant
    return left, right


def assignment_cost(payload, u_value, gradient):
    observed = center_rows([trace["arrival_s"] for trace in payload["traces"]])
    predicted_rows = []
    for site in payload["sites"]:
        row = []
        for event in payload["events"]:
            distance, depth_term = travel_basis(site, event)
            row.append(u_value * distance + gradient * depth_term)
        predicted_rows.append(row)
    predicted = center_rows(predicted_rows)
    return [
        [
            sum(
                (observed[trace_index][event_index] - predicted[site_index][event_index]) ** 2
                for event_index in range(8)
            )
            for site_index in range(6)
        ]
        for trace_index in range(6)
    ]


def fit_solution(payload, assignment):
    arrivals = [trace["arrival_s"] for trace in payload["traces"]]
    basis_a = []
    basis_b = []
    for site_index in assignment:
        site = payload["sites"][site_index]
        pairs = [travel_basis(site, event) for event in payload["events"]]
        basis_a.append([pair[0] for pair in pairs])
        basis_b.append([pair[1] for pair in pairs])
    centered_arrivals = center_matrix(arrivals)
    centered_a = center_matrix(basis_a)
    centered_b = center_matrix(basis_b)
    normal = [[0.0, 0.0], [0.0, 0.0]]
    rhs = [0.0, 0.0]
    for trace_index in range(6):
        for event_index in range(8):
            row = [
                centered_a[trace_index][event_index],
                centered_b[trace_index][event_index],
            ]
            value = centered_arrivals[trace_index][event_index]
            rhs[0] += row[0] * value
            rhs[1] += row[1] * value
            normal[0][0] += row[0] * row[0]
            normal[0][1] += row[0] * row[1]
            normal[1][0] += row[0] * row[1]
            normal[1][1] += row[1] * row[1]
    u_value, gradient = solve_two_by_two(normal, rhs)
    if u_value <= 0:
        raise SeismicInputError("surface velocity is invalid")
    surface = 1.0 / u_value
    if not 5.5 <= surface <= 7.5:
        raise SeismicInputError("surface velocity is outside bounds")
    if not -0.02 <= gradient <= 0.02:
        raise SeismicInputError("depth gradient is outside bounds")
    offsets = []
    travel_rows = []
    for trace_index in range(6):
        row_offsets = []
        row_travel = []
        for event_index in range(8):
            travel = (
                u_value * basis_a[trace_index][event_index]
                + gradient * basis_b[trace_index][event_index]
            )
            row_offsets.append(arrivals[trace_index][event_index] - travel)
            row_travel.append(travel)
        offsets.append(row_offsets)
        travel_rows.append(row_travel)
    shifts = [sum(row) / 8.0 for row in offsets]
    mean_shift = sum(shifts) / 6.0
    shifts = [value - mean_shift for value in shifts]
    event_levels = [
        sum(
            arrivals[trace_index][event_index]
            - shifts[trace_index]
            - travel_rows[trace_index][event_index]
            for trace_index in range(6)
        )
        / 6.0
        for event_index in range(8)
    ]
    residuals = []
    for trace_index in range(6):
        for event_index in range(8):
            predicted = (
                shifts[trace_index]
                + event_levels[event_index]
                + travel_rows[trace_index][event_index]
            )
            residuals.append(arrivals[trace_index][event_index] - predicted)
    rms = math.sqrt(sum(value * value for value in residuals) / len(residuals))
    return surface, gradient, shifts, rms, max(abs(value) for value in residuals)


def calibrate(payload):
    u_value = 2.0 / (5.5 + 7.5)
    gradient = 0.0
    assignment = None
    for _ in range(4):
        current = hungarian(assignment_cost(payload, u_value, gradient))
        surface, gradient, shifts, rms, max_abs = fit_solution(payload, current)
        u_value = 1.0 / surface
        if current == assignment:
            break
        assignment = current
    if assignment is None:
        raise SeismicInputError("assignment did not converge")
    station_map = []
    for trace_index, site_index in enumerate(assignment):
        station_map.append(
            {
                "trace_code": payload["traces"][trace_index]["trace_code"],
                "site_code": payload["sites"][site_index]["site_code"],
                "clock_shift_s": round(shifts[trace_index], 6),
            }
        )
    station_map.sort(key=lambda row: row["trace_code"])
    return {
        "schema_version": 1,
        "case_id": payload["case_id"],
        "station_map": station_map,
        "velocity": {
            "surface_km_s": round(surface, 6),
            "depth_gradient": round(gradient, 8),
        },
        "fit": {"rms_s": round(rms, 6), "max_abs_s": round(max_abs, 6)},
        "status": "calibrated",
    }


def canonical_hash(value):
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def write_atomic(path, data):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".seismic-",
        dir=str(target.parent),
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_checkpoint(path):
    target = Path(path)
    if not target.exists():
        return {"schema_version": 1, "completed": []}
    try:
        value = json.loads(
            target.read_text(encoding="utf-8"),
            object_pairs_hook=duplicate_guard,
        )
    except (OSError, ValueError) as exc:
        raise SeismicInputError("checkpoint is not readable") from exc
    exact_keys(value, CHECK_KEYS, "checkpoint")
    if value["schema_version"] != 1 or not isinstance(value["completed"], list):
        raise SeismicInputError("checkpoint schema is invalid")
    for index, entry in enumerate(value["completed"]):
        exact_keys(entry, ENTRY_KEYS, f"checkpoint entry {index}")
    return value


def compact(value):
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def publish(payload, raw, checkpoint_path, report_path):
    report = calibrate(payload)
    fingerprint = hashlib.sha256(raw).hexdigest()
    checkpoint = read_checkpoint(checkpoint_path)
    entry = {
        "case_id": payload["case_id"],
        "fingerprint": fingerprint,
        "station_map_sha256": canonical_hash(report["station_map"]),
    }
    duplicate = any(
        item["case_id"] == entry["case_id"]
        and item["fingerprint"] == entry["fingerprint"]
        for item in checkpoint["completed"]
    )
    if not duplicate:
        checkpoint["completed"].append(entry)
        write_atomic(checkpoint_path, compact(checkpoint))
    write_atomic(report_path, compact(report))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--checkpoint", required=True)
    args = parser.parse_args(argv)
    old = Path(args.checkpoint).read_bytes() if Path(args.checkpoint).exists() else b""
    try:
        raw, payload = read_input(args.input)
        publish(payload, raw, args.checkpoint, args.report)
    except SeismicInputError as exc:
        if Path(args.checkpoint).exists() and Path(args.checkpoint).read_bytes() != old:
            write_atomic(args.checkpoint, old)
        print(f"seismic: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"seismic: {exc.strerror}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
