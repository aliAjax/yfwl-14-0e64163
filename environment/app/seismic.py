#!/usr/bin/env python3
"""Six-station linear slowness survey entry point."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--checkpoint", required=True)
    args = parser.parse_args()
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    sites = payload.get("sites", [])
    events = payload.get("events", [])
    traces = payload.get("traces", [])
    report = {
        "schema_version": 1,
        "case_id": payload.get("case_id", ""),
        "station_map": [],
        "velocity": {"surface_km_s": 0.0, "depth_gradient": 0.0},
        "fit": {"rms_s": 1.0, "max_abs_s": 1.0},
        "status": "uncalibrated" if len(sites) == len(traces) and len(events) == 8 else "needs_review",
    }
    target = Path(args.report)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    Path(args.checkpoint).parent.mkdir(parents=True, exist_ok=True)
    Path(args.checkpoint).write_text('{"completed":[],"schema_version":1}\n', encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
