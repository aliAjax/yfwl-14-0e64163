from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path


ENTRY = os.environ.get("SEISMIC_ENTRY", "/app/seismic.py")
DATA_DIR = Path(os.environ.get("SEISMIC_DATA_DIR", "/app/data"))
MAIN_CASE = DATA_DIR / "seismic_case.json"
GOLDEN = Path(__file__).with_name("golden.json")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def invoke(tmp_path, payload, case_name="payload.json", checkpoint=None):
    case = tmp_path / case_name
    case.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    report = tmp_path / "report.json"
    state = checkpoint if checkpoint is not None else tmp_path / "checkpoint.json"
    process = subprocess.run(
        ["python3", ENTRY, "--input", str(case), "--report", str(report), "--checkpoint", str(state)],
        text=True,
        capture_output=True,
        timeout=8,
    )
    return process, report, state


def main_payload():
    return load_json(MAIN_CASE)


def test_main_survey_gate(tmp_path):
    """题面：「输入固定为六个接收点、八次事件和六条现场记录，三者编号各自唯一。」"""
    process, report, _ = invoke(tmp_path, main_payload())
    data = load_json(report)
    golden = load_json(GOLDEN)
    assert process.returncode == 0
    assert data["station_map"] == golden["station_map"]


def test_success_is_silent(tmp_path):
    """题面：「成功时两个输出流均留空。」"""
    process, _, _ = invoke(tmp_path, main_payload())
    assert process.returncode == 0
    assert process.stdout == ""
    assert process.stderr == ""


def test_report_schema_is_exact(tmp_path):
    """题面：「结果外层：schema_version, case_id, station_map, velocity, fit, status」"""
    process, report, _ = invoke(tmp_path, main_payload())
    data = load_json(report)
    assert process.returncode == 0
    assert set(data) == {"schema_version", "case_id", "station_map", "velocity", "fit", "status"}
    assert all(set(row) == {"trace_code", "site_code", "clock_shift_s"} for row in data["station_map"])
    assert set(data["velocity"]) == {"surface_km_s", "depth_gradient"}
    assert set(data["fit"]) == {"rms_s", "max_abs_s"}


def test_station_map_sorted_and_calibrated(tmp_path):
    """题面：「台站表：trace_code, site_code, clock_shift_s」"""
    process, report, _ = invoke(tmp_path, main_payload())
    data = load_json(report)
    codes = [row["trace_code"] for row in data["station_map"]]
    assert process.returncode == 0
    assert codes == sorted(codes)
    assert data["status"] == "calibrated"


def test_mapping_matches_global_differential_cost(tmp_path):
    """题面：「先用同一现场记录在八次事件间的中心化走时形态，与所有候选接收点的双参数走时形态计算代价，再用匈牙利算法求全局最小台站双射。」"""
    process, report, _ = invoke(tmp_path, main_payload())
    actual = {row["trace_code"]: row["site_code"] for row in load_json(report)["station_map"]}
    assert process.returncode == 0
    assert actual == load_json(GOLDEN)["mapping"]


def test_common_time_shift_does_not_change_mapping(tmp_path):
    """题面：「各记录器时钟还带未知整体偏移。」"""
    payload = main_payload()
    for index, trace in enumerate(payload["traces"]):
        trace["arrival_s"] = [value + 20.0 + index for value in trace["arrival_s"]]
    process, report, _ = invoke(tmp_path, payload)
    actual = {row["trace_code"]: row["site_code"] for row in load_json(report)["station_map"]}
    assert process.returncode == 0
    assert actual == load_json(GOLDEN)["mapping"]


def test_surface_velocity_matches_truth(tmp_path):
    """题面：「速度项：surface_km_s, depth_gradient」"""
    process, report, _ = invoke(tmp_path, main_payload())
    actual = load_json(report)["velocity"]["surface_km_s"]
    assert process.returncode == 0
    assert abs(actual - load_json(GOLDEN)["surface_km_s"]) < 0.02


def test_depth_gradient_matches_truth(tmp_path):
    """题面：「表层速度边界固定为 `[5.5, 7.5]` km/s，深度梯度边界固定为 `[-0.02, 0.02]`。」"""
    process, report, _ = invoke(tmp_path, main_payload())
    velocity = load_json(report)["velocity"]
    assert process.returncode == 0
    assert abs(velocity["depth_gradient"] - load_json(GOLDEN)["depth_gradient"]) < 0.001
    assert 5.5 <= velocity["surface_km_s"] <= 7.5
    assert -0.02 <= velocity["depth_gradient"] <= 0.02


def test_clock_shifts_match_truth(tmp_path):
    """题面：「恢复映射后，闭式求解速度和梯度，再以零均值规范化恢复六个时钟偏移。」"""
    process, report, _ = invoke(tmp_path, main_payload())
    actual = {row["trace_code"]: row["clock_shift_s"] for row in load_json(report)["station_map"]}
    expected = load_json(GOLDEN)["shifts"]
    assert process.returncode == 0
    assert all(abs(actual[code] - expected[code]) < 0.02 for code in expected)


def test_clock_shifts_are_normalized_and_bounded(tmp_path):
    """题面：「六个 `clock_shift_s` 的均值为零。」"""
    process, report, _ = invoke(tmp_path, main_payload())
    values = [row["clock_shift_s"] for row in load_json(report)["station_map"]]
    assert process.returncode == 0
    assert abs(sum(values)) < 0.00001
    assert max(abs(value) for value in values) <= 1.2


def test_fit_is_small(tmp_path):
    """题面：「速度参数在公开边界内唯一」"""
    process, report, _ = invoke(tmp_path, main_payload())
    fit = load_json(report)["fit"]
    assert process.returncode == 0
    assert fit["rms_s"] < 0.003
    assert fit["max_abs_s"] < 0.006


def test_checkpoint_schema_is_exact(tmp_path):
    """题面：「检查点外层仅保存版本号与 `completed`。」"""
    process, _, checkpoint = invoke(tmp_path, main_payload())
    data = load_json(checkpoint)
    assert process.returncode == 0
    assert set(data) == {"schema_version", "completed"}
    assert all(set(entry) == {"case_id", "fingerprint", "station_map_sha256"} for entry in data["completed"])


def test_first_run_appends_one_entry(tmp_path):
    """题面：「新输入追加一条；输入字节一致时检查点字节不变；失败输入保持旧字节。」"""
    process, _, checkpoint = invoke(tmp_path, main_payload())
    assert process.returncode == 0
    assert len(load_json(checkpoint)["completed"]) == 1


def test_repeated_input_is_idempotent(tmp_path):
    """题面：「新输入追加一条；输入字节一致时检查点字节不变；失败输入保持旧字节。」"""
    first, _, checkpoint = invoke(tmp_path, main_payload(), "payload-a.json")
    before = checkpoint.read_bytes()
    second, _, _ = invoke(tmp_path, main_payload(), "payload-b.json", checkpoint)
    assert first.returncode == 0
    assert second.returncode == 0
    assert checkpoint.read_bytes() == before


def test_changed_input_appends_new_entry(tmp_path):
    """题面：「新输入追加一条；输入字节一致时检查点字节不变；失败输入保持旧字节。」"""
    first, _, checkpoint = invoke(tmp_path, main_payload(), "payload-a.json")
    payload = main_payload()
    payload["traces"][0]["arrival_s"][1] += 0.003
    second, _, _ = invoke(tmp_path, payload, "payload-b.json", checkpoint)
    assert first.returncode == 0
    assert second.returncode == 0
    assert len(load_json(checkpoint)["completed"]) == 2


def test_failed_input_preserves_checkpoint(tmp_path):
    """题面：「新输入追加一条；输入字节一致时检查点字节不变；失败输入保持旧字节。」"""
    first, _, checkpoint = invoke(tmp_path, main_payload())
    before = checkpoint.read_bytes()
    payload = main_payload()
    payload["schema_version"] = 2
    second, _, _ = invoke(tmp_path, payload, "payload-b.json", checkpoint)
    assert first.returncode == 0
    assert second.returncode == 2
    assert checkpoint.read_bytes() == before


def test_checkpoint_fingerprint_is_raw_sha256(tmp_path):
    """题面：「`completed` 每项键集合为 `case_id`、`fingerprint`、`station_map_sha256`。」"""
    payload = main_payload()
    raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    process, _, checkpoint = invoke(tmp_path, payload)
    entry = load_json(checkpoint)["completed"][0]
    assert process.returncode == 0
    assert entry["fingerprint"] == hashlib.sha256(raw).hexdigest()


def test_report_and_checkpoint_are_compact(tmp_path):
    """题面：「报告与检查点均为紧凑单行 JSON，并以一个换行结束。」"""
    process, report, checkpoint = invoke(tmp_path, main_payload())
    report_bytes = report.read_bytes()
    checkpoint_bytes = checkpoint.read_bytes()
    assert process.returncode == 0
    assert b'": ' not in report_bytes
    assert b'": ' not in checkpoint_bytes
    assert report_bytes.endswith(b"\n") and not report_bytes.endswith(b"\n\n")
    assert checkpoint_bytes.endswith(b"\n") and not checkpoint_bytes.endswith(b"\n\n")


def test_run_finishes_below_public_limit(tmp_path):
    """题面：「完整复核的公开耗时为六秒，测试看门狗八秒。」"""
    case = tmp_path / "payload.json"
    case.write_text(
        json.dumps(main_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    started = time.monotonic()
    process = subprocess.run(
        ["python3", ENTRY, "--input", str(case), "--report", str(tmp_path / "report.json"), "--checkpoint", str(tmp_path / "checkpoint.json")],
        text=True,
        capture_output=True,
        timeout=8,
    )
    elapsed = time.monotonic() - started
    assert process.returncode == 0
    assert elapsed < 6


def test_wrong_site_count_is_rejected(tmp_path):
    """题面：「输入固定为六个接收点、八次事件和六条现场记录，三者编号各自唯一。」"""
    payload = main_payload()
    payload["sites"] = payload["sites"][:5]
    payload["traces"] = payload["traces"][:5]
    process, report, _ = invoke(tmp_path, payload)
    assert process.returncode == 2
    assert process.stderr.startswith("seismic: ")
    assert not report.exists()


def test_wrong_event_count_is_rejected(tmp_path):
    """题面：「输入固定为六个接收点、八次事件和六条现场记录，三者编号各自唯一。」"""
    payload = main_payload()
    payload["events"] = payload["events"][:7]
    process, report, _ = invoke(tmp_path, payload)
    assert process.returncode == 2
    assert process.stderr.startswith("seismic: ")
    assert not report.exists()


def test_duplicate_trace_code_is_rejected(tmp_path):
    """题面：「输入固定为六个接收点、八次事件和六条现场记录，三者编号各自唯一。」"""
    payload = main_payload()
    payload["traces"][1]["trace_code"] = payload["traces"][0]["trace_code"]
    process, report, _ = invoke(tmp_path, payload)
    assert process.returncode == 2
    assert process.stderr.startswith("seismic: ")
    assert not report.exists()


def test_missing_nested_key_is_rejected(tmp_path):
    """题面：「缺少键即拒绝。」"""
    payload = main_payload()
    del payload["sites"][0]["receiver_km"]
    process, report, _ = invoke(tmp_path, payload)
    assert process.returncode == 2
    assert process.stderr.startswith("seismic: ")
    assert not report.exists()


def test_extra_nested_key_is_rejected(tmp_path):
    """题面：「额外键即拒绝。」"""
    payload = main_payload()
    payload["events"][0]["region_note"] = "ridge"
    process, report, _ = invoke(tmp_path, payload)
    assert process.returncode == 2
    assert process.stderr.startswith("seismic: ")
    assert not report.exists()


def test_duplicate_json_key_is_rejected(tmp_path):
    """题面：「同名键不得在同一层重复。」"""
    text = MAIN_CASE.read_text(encoding="utf-8")
    case = tmp_path / "payload.json"
    case.write_text(text.replace('"schema_version":1,', '"schema_version":1,"schema_version":1,', 1), encoding="utf-8")
    process = subprocess.run(
        ["python3", ENTRY, "--input", str(case), "--report", str(tmp_path / "report.json"), "--checkpoint", str(tmp_path / "checkpoint.json")],
        text=True,
        capture_output=True,
        timeout=8,
    )
    assert process.returncode == 2
    assert process.stderr.startswith("seismic: ")
    assert not (tmp_path / "report.json").exists()


def test_boolean_schema_version_is_rejected(tmp_path):
    """题面：「格式版本只接受整数一，布尔值不属于版本类型。」"""
    payload = main_payload()
    payload["schema_version"] = True
    process, report, _ = invoke(tmp_path, payload)
    assert process.returncode == 2
    assert process.stderr.startswith("seismic: ")
    assert not report.exists()


def test_nonfinite_arrival_is_rejected(tmp_path):
    """题面：「每条 `arrival_s` 恰有八个有限值。」"""
    payload = main_payload()
    payload["traces"][0]["arrival_s"][2] = float("inf")
    process, report, _ = invoke(tmp_path, payload)
    assert process.returncode == 2
    assert process.stderr.startswith("seismic: ")
    assert not report.exists()


def test_arrival_length_mismatch_is_rejected(tmp_path):
    """题面：「每条 `arrival_s` 恰有八个有限值。」"""
    payload = main_payload()
    payload["traces"][0]["arrival_s"].pop()
    process, report, _ = invoke(tmp_path, payload)
    assert process.returncode == 2
    assert process.stderr.startswith("seismic: ")
    assert not report.exists()


def test_coordinate_shape_is_rejected(tmp_path):
    """题面：「每套坐标恰有东、北、深三个有限分量」"""
    payload = main_payload()
    payload["sites"][0]["receiver_km"] = [1.0, 2.0]
    process, report, _ = invoke(tmp_path, payload)
    assert process.returncode == 2
    assert process.stderr.startswith("seismic: ")
    assert not report.exists()


def test_bounds_are_rejected(tmp_path):
    """题面：「表层速度边界固定为 `[5.5, 7.5]` km/s，深度梯度边界固定为 `[-0.02, 0.02]`。」"""
    payload = main_payload()
    payload["bounds"]["max_surface_km_s"] = 8.0
    process, report, _ = invoke(tmp_path, payload)
    assert process.returncode == 2
    assert process.stderr.startswith("seismic: ")
    assert not report.exists()


def test_input_error_protocol_is_closed(tmp_path):
    """题面：「输入错误：标准输出为空；标准错误一行 `seismic: `；状态二；无调用栈。」"""
    payload = main_payload()
    payload["schema_version"] = 2
    process, report, _ = invoke(tmp_path, payload)
    assert process.returncode == 2
    assert process.stdout == ""
    assert process.stderr.startswith("seismic: ")
    assert process.stderr.count("\n") == 1
    assert not report.exists()
