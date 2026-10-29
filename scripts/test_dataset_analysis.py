"""验证官方记录分析 HTTP 接口。不调用 DeepSeek。"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app

EXCEL = ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"
EXPECTED_HASH = "1e9caeba1fe526f3ad1b5dd6445a9dbcc02fa22ec02273d9863ebc84e3e30f87"
LABELS = {
    "设备状态",
    "故障类型",
    "故障严重度",
    "故障位置",
    "诊断置信度",
    "频谱描述",
    "根因分析",
    "建议措施",
    "优先级",
    "影响程度",
}
RESULTS: list[tuple[str, bool, str]] = []
BODIES: list[object] = []


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((name, passed, detail))
    print(("PASS " if passed else "FAIL ") + name + ("" if not detail else " | " + detail))


def label_keys(value: object, found: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in LABELS:
                found.append(str(key))
            label_keys(item, found)
    elif isinstance(value, list):
        for item in value:
            label_keys(item, found)


def metric(body: dict, field: str) -> dict | None:
    deviation = body.get("statistical_deviation")
    if not isinstance(deviation, dict):
        return None
    items = deviation.get("analyzed_metrics")
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, dict) and item.get("field") == field:
            return item
    return None


def clue(body: dict, code: str) -> dict | None:
    clues = body.get("diagnostic_clues")
    if not isinstance(clues, list):
        return None
    for item in clues:
        if isinstance(item, dict) and item.get("feature_code") == code:
            return item
    return None


def main() -> None:
    before = file_hash(EXCEL)
    check("G 测试前 Excel 哈希", before == EXPECTED_HASH, before)
    model_calls = {"count": 0}

    def blocked(*args, **kwargs):
        model_calls["count"] += 1
        raise RuntimeError("测试不应调用模型")

    main_module.ask_with_tools = blocked
    main_module.ask = blocked
    client = TestClient(app)

    motor = client.get("/api/dataset/analysis/REC-MA-20260403-00346")
    motor_body = motor.json()
    BODIES.append(motor_body)
    observation = motor_body.get("observation") if isinstance(motor_body, dict) else None
    deviation = motor_body.get("statistical_deviation") if isinstance(motor_body, dict) else None
    y_metric = metric(motor_body, "Y向振动(mm/s)") if isinstance(motor_body, dict) else None
    xy = clue(motor_body, "amplitude") if isinstance(motor_body, dict) else None
    y_value = None if not isinstance(observation, dict) else observation.get("values", {}).get("Y向振动(mm/s)")
    y_score = None if not isinstance(y_metric, dict) else y_metric.get("robust_z")
    candidates = motor_body.get("candidate_faults") if isinstance(motor_body, dict) else None
    check(
        "A MM-01 记录分析",
        motor.status_code == 200
        and isinstance(observation, dict)
        and observation.get("device_id") == "MM-01"
        and observation.get("record_id") == "REC-MA-20260403-00346"
        and observation.get("collected_at") == "2026-04-03 11:05:00"
        and isinstance(deviation, dict)
        and deviation.get("detection_status") == "偏离基线"
        and isinstance(y_value, (int, float))
        and abs(float(y_value) - 3.017) < 1e-9
        and isinstance(y_score, (int, float))
        and abs(float(y_score) - 3.923797970479703) < 1e-6
        and isinstance(xy, dict)
        and xy.get("partial_condition_met") is True
        and xy.get("complete_condition_met") is False
        and xy.get("document") == "KB-02_故障模式诊断知识库.md"
        and xy.get("line_number") == 38
        and "转子不平衡" in str(xy.get("related_fault_section"))
        and "不能确认转子不平衡" in str(xy.get("unverified_reason"))
        and motor_body.get("diagnosis_level") == "diagnostic_clue"
        and candidates == []
        and motor_body.get("diagnose_fault_called") is False
        and motor_body.get("confirmed") is False
        and "官方开发集历史记录的辅助分析" in str(motor_body.get("analysis_note"))
        and "不是真实传感器实时监控" in str(motor_body.get("analysis_note")),
        f"status={motor.status_code} device={None if not isinstance(observation, dict) else observation.get('device_id')} level={motor_body.get('diagnosis_level')} y={y_value} z={y_score} candidates={candidates}",
    )

    gearbox = client.get("/api/dataset/analysis/REC-GE-20260409-00447")
    gearbox_body = gearbox.json()
    BODIES.append(gearbox_body)
    gear_observation = gearbox_body.get("observation") if isinstance(gearbox_body, dict) else None
    gear_values = gear_observation.get("values") if isinstance(gear_observation, dict) else {}
    drive = gear_values.get("驱动端轴承温度(°C)") if isinstance(gear_values, dict) else None
    other = gear_values.get("非驱动端轴承温度(°C)") if isinstance(gear_values, dict) else None
    gear_deviation = gearbox_body.get("statistical_deviation") if isinstance(gearbox_body, dict) else None
    anomalous = gear_deviation.get("anomalous_metrics") if isinstance(gear_deviation, dict) else None
    gear_candidates = gearbox_body.get("candidate_faults") if isinstance(gearbox_body, dict) else None
    temperature_clue = clue(gearbox_body, "temperature") if isinstance(gearbox_body, dict) else None
    check(
        "B GB-01 温度偏离不认定润滑故障",
        gearbox.status_code == 200
        and isinstance(gear_observation, dict)
        and gear_observation.get("device_id") == "GB-01"
        and gear_observation.get("record_id") == "REC-GE-20260409-00447"
        and isinstance(drive, (int, float))
        and abs(float(drive) - 77.0) < 1e-9
        and isinstance(other, (int, float))
        and abs(float(other) - 73.2) < 1e-9
        and isinstance(gear_deviation, dict)
        and gear_deviation.get("detection_status") == "偏离基线"
        and anomalous == ["驱动端轴承温度(°C)", "非驱动端轴承温度(°C)"]
        and gear_candidates == []
        and temperature_clue is None
        and gearbox_body.get("diagnosis_level") != "possible_match"
        and gearbox_body.get("confirmed") is False,
        f"status={gearbox.status_code} device={None if not isinstance(gear_observation, dict) else gear_observation.get('device_id')} drive={drive} other={other} level={gearbox_body.get('diagnosis_level')} anomalous={anomalous}",
    )

    missing = client.get("/api/dataset/analysis/REC-NOT-EXIST-123")
    missing_body = missing.json()
    BODIES.append(missing_body)
    check(
        "C 不存在的记录",
        missing.status_code == 404
        and missing_body.get("success") is False
        and "没有找到记录编号" in str(missing_body.get("message"))
        and "REC-NOT-EXIST-123" in str(missing_body.get("message"))
        and "observation" not in missing_body
        and "device_id" not in missing_body
        and "values" not in missing_body,
        f"status={missing.status_code} message={missing_body.get('message')}",
    )

    early = client.get("/api/dataset/analysis/REC-MA-20260314-00001")
    early_body = early.json()
    BODIES.append(early_body)
    early_deviation = early_body.get("statistical_deviation") if isinstance(early_body, dict) else None
    early_observation = early_body.get("observation") if isinstance(early_body, dict) else None
    early_clue = clue(early_body, "amplitude") if isinstance(early_body, dict) else None
    reasons = early_body.get("insufficient_reasons") if isinstance(early_body, dict) else None
    check(
        "D 早期记录基线不足",
        early.status_code == 200
        and isinstance(early_observation, dict)
        and early_observation.get("device_id") == "MM-01"
        and early_observation.get("record_id") == "REC-MA-20260314-00001"
        and isinstance(early_deviation, dict)
        and early_deviation.get("detection_status") == "基线不足"
        and early_deviation.get("history_sample_count") == 0
        and early_deviation.get("anomalous_metrics") == []
        and early_body.get("diagnosis_level") == "diagnostic_clue"
        and isinstance(early_clue, dict)
        and early_clue.get("partial_condition_met") is True
        and early_clue.get("complete_condition_met") is False
        and early_body.get("candidate_faults") == []
        and early_body.get("confirmed") is False
        and isinstance(reasons, list)
        and any("历史基线不足" in str(item) for item in reasons),
        f"status={early.status_code} detection={None if not isinstance(early_deviation, dict) else early_deviation.get('detection_status')} level={early_body.get('diagnosis_level')} history={None if not isinstance(early_deviation, dict) else early_deviation.get('history_sample_count')}",
    )

    illegal = client.get("/api/dataset/analysis/foo..bar")
    illegal_body = illegal.json()
    BODIES.append(illegal_body)
    spaced = client.get("/api/dataset/analysis/bad record")
    spaced_body = spaced.json()
    BODIES.append(spaced_body)
    check(
        "非法记录编号",
        illegal.status_code == 422
        and "不能用于读取服务器文件" in str(illegal_body.get("message"))
        and spaced.status_code == 422
        and "格式不正确" in str(spaced_body.get("message"))
        and not illegal.content.startswith(b"PK"),
        f"path={illegal.status_code} space={spaced.status_code}",
    )

    leaked: list[str] = []
    for body in BODIES:
        label_keys(body, leaked)
    check("E 响应不含答案标签", not leaked, ",".join(leaked))

    summary = client.get("/api/dataset/summary")
    devices = client.get("/api/dataset/devices")
    records = client.get("/api/dataset/records", params={"device_id": "MM-01", "limit": 1})
    observed = client.get("/api/dataset/observations/REC-MA-20260403-00346")
    search = client.get("/api/knowledge/search", params={"q": "轴承", "top_k": 1})
    graph = client.get("/api/knowledge/graph")
    vibration = client.post(
        "/api/vibration/analyze",
        params={"sampling_rate": "1000", "column": "value"},
        content=("value\n" + "\n".join(["0", "1", "0", "-1", "0", "1", "0", "-1"])).encode("utf-8"),
        headers={"content-type": "text/csv"},
    )
    empty_chat = client.post("/api/ai/chat", json={})
    blank_chat = client.post("/api/ai/chat", json={"question": " "})
    summary_body = summary.json()
    devices_body = devices.json()
    records_body = records.json()
    observed_body = observed.json()
    search_body = search.json()
    graph_body = graph.json()
    vibration_body = vibration.json()
    check(
        "F 原有接口未调用模型",
        summary.status_code == 200
        and summary_body.get("device_count") == 46
        and summary_body.get("record_count") == 24000
        and devices.status_code == 200
        and devices_body.get("device_count") == 46
        and records.status_code == 200
        and records_body.get("device_id") == "MM-01"
        and records_body.get("returned_count") == 1
        and observed.status_code == 200
        and observed_body.get("record", {}).get("device_id") == "MM-01"
        and search.status_code == 200
        and search_body.get("count", 0) >= 1
        and graph.status_code == 200
        and len(graph_body.get("nodes", [])) == 252
        and len(graph_body.get("edges", [])) == 464
        and vibration.status_code == 200
        and vibration_body.get("analysis_note") == "这是信号分析结果，不是故障确诊。"
        and empty_chat.status_code == 422
        and empty_chat.json().get("message") == "请输入问题"
        and blank_chat.status_code == 422
        and model_calls["count"] == 0,
        f"summary={summary.status_code} devices={devices.status_code} records={records.status_code} observation={observed.status_code} search={search.status_code} graph={graph.status_code} vibration={vibration.status_code} chat={empty_chat.status_code} model_calls={model_calls['count']}",
    )

    import py_compile

    py_compile.compile(str(ROOT / "backend" / "app" / "main.py"), doraise=True)
    after = file_hash(EXCEL)
    check("G 编译通过且 Excel 哈希不变", after == EXPECTED_HASH and after == before, after)

    failed = [name for name, passed, _detail in RESULTS if not passed]
    print(f"SUMMARY {len(RESULTS) - len(failed)}/{len(RESULTS)}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
