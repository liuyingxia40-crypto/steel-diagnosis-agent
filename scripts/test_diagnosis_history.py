"""跨请求故障评估历史。使用临时库、模拟工具和合成校验结果，不调用真实模型。"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
TEMP = Path(tempfile.mkdtemp(prefix="diagnosis-history-"))
os.environ["DIAGNOSIS_HISTORY_DB"] = str(TEMP / "history.sqlite3")
os.environ["SUPPLEMENTARY_MEASUREMENT_DB"] = str(TEMP / "supplements.sqlite3")
os.environ["MONITORING_EVENTS_DB"] = str(TEMP / "events.sqlite3")
os.environ["SUPERVISOR_TRACE_DB"] = str(TEMP / "traces.sqlite3")
os.environ["STARTUP_TOOL_SELFTEST"] = "0"

from fastapi.testclient import TestClient

from app.main import app
from app.services.diagnosis_history import (
    NO_HYPOTHESIS_LINE,
    UNCHANGED_LINE,
    compare_assessments,
    database_path,
    get_assessment,
    list_assessments,
)
from app.services.fault_revision import assess_saved_record

GRAPH = ROOT / "data" / "processed" / "graph" / "graph.json"
EXCEL = ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"
EVENTS = ROOT / "data" / "runtime" / "monitoring_events.sqlite3"
ENV_FILE = ROOT / "backend" / ".env"
DEFAULT_HISTORY = ROOT / "data" / "runtime" / "diagnosis_history.sqlite3"
RESULTS: list[bool] = []
WEAK = "REC-TEMP-WEAK"
SUPPORT = "REC-TEMP-SUPPORT"
EMPTY = "REC-TEMP-EMPTY"
OTHER = "REC-TEMP-OTHER"
BORN = "REC-TEMP-BORN"


def check(name: str, ok: bool, detail: object = "") -> None:
    RESULTS.append(bool(ok))
    print(("PASS" if ok else "FAIL"), name, detail if not ok else "")


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def diagnosis(locators: list[str]) -> dict:
    return {
        "ok": True,
        "status": "possible_match",
        "confirmed": False,
        "matches": [
            {
                "fault": "轴承磨损",
                "confirmed": False,
                "matched_features": [{"document": "KB-02.md", "locator": locator} for locator in locators],
            }
        ],
    }


def relations() -> dict:
    return {
        "ok": True,
        "ambiguous": False,
        "count": 1,
        "node": {"name": "棒线主轧机电机"},
        "relations": [{"source_name": "棒线主轧机电机", "relation": "HAS_COMPONENT", "target_name": "轴承"}],
    }


def tools(record_id: str, device_id: str, locators: list[str]) -> list[dict]:
    return [
        {
            "id": "analysis",
            "name": "analyze_equipment_record",
            "result": {
                "ok": True,
                "record_id": record_id,
                "device_id": device_id,
                "diagnosis_level": "possible_match",
                "confirmed": False,
                "candidate_faults": [],
                "diagnostic_clues": [],
            },
            "arguments": {"record_id": record_id},
        },
        {"id": "relations", "name": "get_equipment_relations", "result": relations(), "arguments": {"name": "棒线主轧机电机"}},
        {"id": "diagnosis", "name": "diagnose_fault", "result": diagnosis(locators), "arguments": {"observed_features": ["测试特征"]}},
    ]


def physics_for(contradicted: bool):
    def physics(_entity_a: str, _entity_b: str, _record_id: str) -> dict:
        status = "contradicted" if contradicted else "consistent"
        return {
            "overall_status": "numeric_contradiction" if contradicted else "conditions_consistent",
            "contradicted_checks": ["speed_frequency"] if contradicted else [],
            "consistent_checks": [] if contradicted else ["speed_frequency"],
            "insufficient_checks": ["phase_stability"],
            "not_applicable_checks": [],
            "missing_measurements": ["相位"],
            "physical_checks": [
                {
                    "check_id": "speed_frequency",
                    "title": "转速与转频一致性",
                    "status": status,
                    "evidence_source": {"kind": "supplementary_measurement", "measurement_ids": ["m-test"]} if contradicted else {},
                }
            ],
            "supports_fault": False,
            "supports_causal_hypothesis": False,
            "written_to_graph": False,
            "causal_conclusion": False,
        }

    return physics


def execute(_name: str, _arguments: str) -> dict:
    raise AssertionError("这组测试的工具结果已经在初始输入里，不应再调用工具。")


def fault_named(result: dict) -> dict:
    return next(item for item in result["hypotheses"] if item.get("faults") == ["轴承磨损"])


def run(record_id: str, device_id: str, locators: list[str], contradicted: bool, measurements: list | None = None) -> dict:
    return assess_saved_record(
        record_id,
        device_id,
        tools(record_id, device_id, locators),
        execute=execute,
        physics=physics_for(contradicted),
        diagnosis_level="possible_match",
        max_rounds=1,
        measurements=measurements
        or [
            {
                "measurement_id": "m-test",
                "content_sha256": "abc123",
                "measured_at": "2026-04-03 11:05:00",
                "sensor_position": "驱动端水平",
                "unit": "mm/s",
                "file_name": "synthetic.csv",
                "data_origin": "synthetic_test_signal",
                "origin_note": "合成测试信号，只用于验证程序，不是工厂测量。",
                "metrics": {"samples": [1, 2, 3]},
            }
        ],
    )


def main() -> None:
    missing = TEMP / "not-created.sqlite3"
    os.environ["DIAGNOSIS_HISTORY_DB"] = str(missing)
    absent = list_assessments(EMPTY)
    os.environ["DIAGNOSIS_HISTORY_DB"] = str(TEMP / "history.sqlite3")
    check("读取历史时不创建数据库", absent == [] and not missing.exists())

    before = {
        "graph": file_hash(GRAPH),
        "excel": file_hash(EXCEL),
        "events": file_hash(EVENTS) if EVENTS.is_file() else "",
        "env": file_hash(ENV_FILE) if ENV_FILE.is_file() else "",
        "history_exists": DEFAULT_HISTORY.exists(),
        "history_size": DEFAULT_HISTORY.stat().st_size if DEFAULT_HISTORY.exists() else 0,
    }

    first = run(WEAK, "MM-01", ["第8行"], False)
    first_fault = fault_named(first)
    first_saved = get_assessment(WEAK, first["assessment_id"])
    check(
        "A 首次评估保存未验证 V1",
        first["ok"] is True
        and first_fault["version"] == 1
        and first_fault["verification_status"] == "未验证"
        and first_fault["version_history"] == []
        and first_saved is not None
        and first_saved["hypotheses"][0]["version"] == 1
        and first["model_called"] is False
        and first["confirmed"] is False
        and first["trace_id"] == "",
        first_fault,
    )

    second = run(WEAK, "MM-01", ["第8行"], True)
    second_fault = fault_named(second)
    first_after = get_assessment(WEAK, first["assessment_id"])
    check(
        "B 新增反对证据后未验证 V1 变为被削弱 V2",
        second_fault["version"] == 2
        and second_fault["verification_status"] == "被削弱"
        and second_fault["version_history"][0]["version"] == 1
        and second_fault["version_history"][0]["verification_status"] == "未验证"
        and any(line == "轴承磨损：未验证 V1 → 被削弱 V2" for line in second["change_lines"])
        and second_fault["opposing_evidence"]
        and second_fault["opposing_evidence"][0]["source"] == "validate_research_physics"
        and "m-test" in second_fault["opposing_evidence"][0]["detail"],
        second_fault,
    )
    check(
        "C 原始 V1 没有被覆盖",
        first_after is not None
        and first_after["hypotheses_detail"][0]["version"] == 1
        and first_after["hypotheses_detail"][0]["verification_status"] == "未验证"
        and first_after["hypotheses_detail"][0]["version_history"] == []
        and first_after["assessment_id"] != second["assessment_id"],
        first_after,
    )

    third = run(WEAK, "MM-01", ["第8行"], True)
    third_fault = fault_named(third)
    check(
        "D 相同证据再次评估不升版",
        third_fault["version"] == 2
        and third_fault["verification_status"] == "被削弱"
        and third["change_lines"] == [UNCHANGED_LINE]
        and len(list_assessments(WEAK)) == 3,
        third_fault,
    )

    support_first = run(SUPPORT, "MM-02", ["第8行"], False)
    support_second = run(SUPPORT, "MM-02", ["第8行", "第9行"], False)
    support_fault = fault_named(support_second)
    support_detail = get_assessment(SUPPORT, support_second["assessment_id"])
    diagnosis_evidence = next(item for item in support_fault["supporting_evidence"] if item["source"] == "diagnose_fault")
    check(
        "E 新增支持证据升版并保留来源",
        fault_named(support_first)["version"] == 1
        and support_fault["version"] == 2
        and support_fault["verification_status"] == "未验证"
        and support_fault["version_history"][0]["verification_status"] == "未验证"
        and "第9行" in diagnosis_evidence["locator"]
        and support_detail is not None
        and support_detail["added_support"]
        and support_detail["added_support"][0]["source"] == "diagnose_fault"
        and "第9行" in support_detail["added_support"][0]["locator"]
        and any("未验证 V1 → 未验证 V2" in line for line in support_second["change_lines"]),
        support_detail["added_support"] if support_detail else support_fault,
    )

    empty = assess_saved_record(
        EMPTY,
        "MM-03",
        [
            {
                "id": "analysis",
                "name": "analyze_equipment_record",
                "result": {
                    "ok": True,
                    "record_id": EMPTY,
                    "device_id": "MM-03",
                    "diagnosis_level": "diagnostic_clue",
                    "confirmed": False,
                    "candidate_faults": [],
                    "diagnostic_clues": [],
                    "missing_data": ["相位"],
                },
                "arguments": {"record_id": EMPTY},
            }
        ],
        execute=execute,
        physics=physics_for(False),
        diagnosis_level="diagnostic_clue",
        max_rounds=1,
        measurements=[],
    )
    empty_saved = get_assessment(EMPTY, empty["assessment_id"])
    check(
        "F 没有故障候选也能保存评估且不生成假设",
        empty["hypotheses"] == []
        and empty["change_lines"] == [NO_HYPOTHESIS_LINE]
        and empty["confirmed"] is False
        and empty_saved is not None
        and empty_saved["no_hypothesis"] is True
        and empty_saved["hypotheses_detail"] == []
        and empty_saved["l2"]["overall_status"] == "conditions_consistent"
        and "相位" in empty_saved["missing_measurements"],
        empty,
    )

    born = run(BORN, "MM-04", ["第8行"], True)
    born_fault = fault_named(born)
    check(
        "首次评估已经是被削弱时保持 V1",
        born_fault["version"] == 1
        and born_fault["verification_status"] == "被削弱"
        and born_fault["version_history"] == []
        and born["change_lines"] == ["首次评估：轴承磨损 被削弱 V1"],
        born_fault,
    )

    listed = list_assessments(WEAK)
    compared = compare_assessments(WEAK, first["assessment_id"], second["assessment_id"])
    detail_text = json.dumps(get_assessment(WEAK, second["assessment_id"]), ensure_ascii=False)
    check(
        "G 历史接口可以查询和对比",
        [item["assessment_no"] for item in listed] == [1, 2, 3]
        and compared["comparable"] is True
        and "轴承磨损：未验证 V1 → 被削弱 V2" in compared["change_lines"]
        and compared["added_oppose"]
        and compared["added_oppose"][0]["source"] == "validate_research_physics"
        and "m-test" in detail_text
        and "abc123" in detail_text
        and "samples" not in detail_text
        and "api_key" not in detail_text,
        compared.get("change_lines"),
    )

    other = run(OTHER, "GE-01", ["第8行"], False)
    weak_ids = {item["assessment_id"] for item in list_assessments(WEAK)}
    other_ids = {item["assessment_id"] for item in list_assessments(OTHER)}
    foreign = get_assessment(WEAK, other["assessment_id"])
    mixed = False
    try:
        run(WEAK, "GE-01", ["第8行"], False)
        mixed = True
    except Exception as exc:
        mixed = "其他设备" not in str(exc)
    check(
        "I 评估记录不混入其他设备或记录",
        other["device_id"] == "GE-01"
        and weak_ids.isdisjoint(other_ids)
        and foreign is None
        and mixed is False
        and all(item["device_id"] == "MM-01" for item in listed)
        and all(item["record_id"] == WEAK for item in listed),
        other.get("error") if isinstance(other, dict) else "",
    )

    again = get_assessment(WEAK, first["assessment_id"])
    check(
        "J 多次请求不覆盖历史也不造成版本冲突",
        again is not None
        and again["hypotheses_detail"][0]["version"] == 1
        and again["hypotheses_detail"][0]["verification_status"] == "未验证"
        and len(list_assessments(WEAK)) == 3
        and len({item["assessment_id"] for item in list_assessments(WEAK)}) == 3,
        again,
    )

    with TestClient(app) as client:
        history = client.get(f"/api/dataset/records/{WEAK}/diagnosis-history")
        one = client.get(f"/api/dataset/records/{WEAK}/diagnosis-history/{second['assessment_id']}")
        compared_http = client.get(
            f"/api/dataset/records/{WEAK}/diagnosis-history/compare",
            params={"before": first["assessment_id"], "after": second["assessment_id"]},
        )
        hidden = client.get(f"/api/dataset/records/{WEAK}/diagnosis-history/{other['assessment_id']}")
    history_body = history.json()
    one_body = one.json()
    compare_body = compared_http.json()
    check(
        "G 历史接口可以通过 HTTP 读取",
        history.status_code == 200
        and len(history_body["assessments"]) == 3
        and one.status_code == 200
        and one_body["assessment_id"] == second["assessment_id"]
        and one_body["hypotheses_detail"][0]["version"] == 2
        and compare_body["comparable"] is True
        and "轴承磨损：未验证 V1 → 被削弱 V2" in compare_body["change_lines"]
        and hidden.status_code == 404
        and "samples" not in json.dumps(one_body, ensure_ascii=False),
        history.status_code,
    )

    after = {
        "graph": file_hash(GRAPH),
        "excel": file_hash(EXCEL),
        "events": file_hash(EVENTS) if EVENTS.is_file() else "",
        "env": file_hash(ENV_FILE) if ENV_FILE.is_file() else "",
        "history_exists": DEFAULT_HISTORY.exists(),
        "history_size": DEFAULT_HISTORY.stat().st_size if DEFAULT_HISTORY.exists() else 0,
    }
    check("K 正式图谱、官方 Excel 和监测事件库保持不变", before == after, {"before": before, "after": after})
    print(f"RESULT passed={sum(RESULTS)} failed={len(RESULTS) - sum(RESULTS)}")
    if not all(RESULTS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
