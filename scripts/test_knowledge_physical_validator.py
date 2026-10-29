"""验收 L2 数值校验。观测只读，模型调用被拦截，写入只在临时目录。"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

TEMP = Path(tempfile.mkdtemp(prefix="knowledge-l2-"))
os.environ["KNOWLEDGE_FILES_DB"] = str(TEMP / "files.sqlite3")
os.environ["KNOWLEDGE_UPLOAD_DIR"] = str(TEMP / "uploads")
os.environ["KNOWLEDGE_CLEANED_DIR"] = str(TEMP / "cleaned")
os.environ["KNOWLEDGE_EXTRACTED_DIR"] = str(TEMP / "extracted")
os.environ["KNOWLEDGE_IMPORT_PREVIEW_DIR"] = str(TEMP / "previews")
os.environ["KNOWLEDGE_GRAPH_IMPORTS_DB"] = str(TEMP / "imports.sqlite3")
os.environ["RESEARCH_RELEASE_GRANTS_PATH"] = str(TEMP / "grants.json")
os.environ["RESEARCH_MODEL_AUDIT_PATH"] = str(TEMP / "audit.jsonl")
os.environ["STARTUP_TOOL_SELFTEST"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import knowledge_research_reasoner as reasoner  # noqa: E402
from app.services.knowledge_physical_validator import validate_record_physics, validate_research_physics  # noqa: E402

CHECKS: list[bool] = []
GRAPH = ROOT / "data" / "processed" / "graph" / "graph.json"
EXCEL = ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"
SQLITE = ROOT / "data" / "runtime" / "monitoring_events.sqlite3"
DEFAULT_IMPORTS = ROOT / "data" / "runtime" / "knowledge_graph_imports.sqlite3"
ANSWER_LABELS = ("设备状态", "故障类型", "故障严重度", "故障位置", "诊断置信度", "频谱描述", "根因分析", "建议措施", "优先级", "影响程度")
NOTE = "L2仅验证已有数据能够检查的物理或数值条件，不构成故障确诊，也不自动证明因果关系。"


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append(bool(ok))
    print(("PASS" if ok else "FAIL"), name, detail)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_check(report: dict, check_id: str) -> dict:
    return next((item for item in report["physical_checks"] if item["check_id"] == check_id), {})


def main() -> None:
    before_graph = file_hash(GRAPH)
    before_excel = file_hash(EXCEL)
    before_sqlite = file_hash(SQLITE) if SQLITE.is_file() else None
    before_default = DEFAULT_IMPORTS.exists()
    calls: list[str] = []
    original = reasoner.complete_research_chat

    def spy(system: str, user: str) -> str:
        calls.append(user)
        raise AssertionError("L2 不应调用 DeepSeek")

    reasoner.complete_research_chat = spy
    client = TestClient(app)
    try:
        motor = client.post(
            "/api/knowledge/research/validate",
            json={
                "entity_a": "棒线主轧机电机",
                "entity_b": "定子",
                "record_id": "REC-MA-20260403-00346",
                "overall_status": "verified",
                "physical_checks": [{"status": "consistent"}],
            },
        ).json()
        speed = find_check(motor, "speed_frequency")
        xy = find_check(motor, "xy_vibration")
        check(
            "真实转速、转频和X/Y关系与官方观测一致",
            motor["record_found"] is True
            and motor["observation_evidence"]["device_id"] == "MM-01"
            and speed["status"] == "consistent"
            and abs(float(speed["inputs"]["转速(rpm)"]) - 1502.9) < 1e-9
            and abs(float(speed["inputs"]["转频(Hz)"]) - 25.05) < 1e-9
            and abs(float(speed["computed"]["rotation_frequency_hz"]) - 1502.9 / 60) < 1e-9
            and speed["computed"]["tolerance_hz"] == 0.005
            and "不是工业报警规则" in speed["limit"]
            and xy["status"] == "consistent"
            and abs(float(xy["inputs"]["X向振动(mm/s)"]) - 3.346) < 1e-9
            and abs(float(xy["inputs"]["Y向振动(mm/s)"]) - 3.017) < 1e-9
            and xy["computed"]["relation"] == "X>Y"
            and "不能确认转子不平衡" in xy["limit"]
            and motor["causal_validation_status"] == "unverified"
            and motor["overall_status"] != "verified"
            and NOTE in motor["validation_note"],
            json.dumps({"speed": speed.get("status"), "diff": speed.get("computed"), "xy": xy.get("computed")}, ensure_ascii=False),
        )
        deviation = find_check(motor, "historical_deviation_y")
        check(
            "Y向3.017的统计偏离不能认定轴承磨损",
            abs(float(deviation["inputs"]["current_value"]) - 3.017) < 1e-9
            and deviation["supports_fault"] is False
            and "不能认定轴承磨损" in deviation["limit"]
            and motor["causal_conclusion"] is False,
            str(deviation.get("inputs")),
        )

        gear = client.post(
            "/api/knowledge/research/validate",
            json={"entity_a": "润滑不足", "entity_b": "轴承温升", "record_id": "REC-GE-20260409-00447"},
        ).json()
        readings = find_check(gear, "temperature_reading")
        threshold = find_check(gear, "kb02_temperature_threshold")
        limit = find_check(gear, "device_temperature_limit")
        trend = find_check(gear, "temperature_trend")
        check(
            "齿轮箱记录读取两侧温度且不认定润滑故障",
            abs(float(readings["inputs"]["驱动端轴承温度(°C)"]) - 77.0) < 1e-9
            and abs(float(readings["inputs"]["非驱动端轴承温度(°C)"]) - 73.2) < 1e-9
            and "不能当成持续升温" in readings["limit"]
            and threshold["status"] == "contradicted"
            and threshold["supports_fault"] is False
            and "不认定、也不排除润滑故障" in threshold["limit"]
            and limit["status"] == "not_applicable"
            and "72" not in json.dumps(limit, ensure_ascii=False)
            and trend["status"] == "insufficient_data"
            and gear["causal_validation_status"] == "unverified"
            and all(item["validation_status"] == "unverified" for item in gear["research_hypotheses"]),
            json.dumps({"threshold": threshold.get("status"), "limit": limit.get("status"), "temps": readings.get("inputs")}, ensure_ascii=False),
        )
        insufficient_ids = set(gear["insufficient_checks"])
        check(
            "缺少2X、相位、谐波和波形时返回数据不足",
            {"bpfo_harmonics", "dominant_1x", "second_harmonic_ratio", "phase_stability", "waveform_statistics"} <= insufficient_ids
            and "没有 2X 幅值" in find_check(gear, "second_harmonic_ratio")["limit"]
            and "没有相位" in find_check(gear, "phase_stability")["limit"]
            and "BPFO幅值不是" in find_check(gear, "bpfo_harmonics")["limit"]
            and "不是 1X 主导峰" in find_check(gear, "dominant_1x")["limit"]
            and find_check(gear, "fixed_characteristic_frequency")["status"] == "not_applicable",
            str(sorted(insufficient_ids)),
        )

        conflict = client.post(
            "/api/knowledge/research/validate",
            json={"entity_a": "棒线主轧机电机", "entity_b": "定子", "record_id": "REC-CC-20260314-01681"},
        ).json()
        conflict_text = json.dumps(conflict["observation_evidence"]["same_time_conflicts"], ensure_ascii=False)
        speed_conflict = find_check(conflict, "speed_frequency")
        check(
            "同一时刻冲突记录不被平均或合并",
            conflict["observation_evidence"]["values_merged"] is False
            and conflict["temporal_validation"]["conflicts_merged"] is False
            and "REC-CC-20260314-03241" in conflict_text
            and abs(float(speed_conflict["inputs"]["转速(rpm)"]) - 981.9) < 1e-9
            and "981.0" not in json.dumps(speed_conflict["inputs"], ensure_ascii=False),
            conflict_text[:400],
        )

        missing = client.post(
            "/api/knowledge/research/validate",
            json={"entity_a": "齿轮裂纹", "entity_b": "轴承磨损", "record_id": "REC-NO-SUCH-00000"},
        ).json()
        check(
            "不存在的记录编号返回未找到",
            missing["record_found"] is False
            and missing["overall_status"] == "record_not_found"
            and missing["observation_evidence"] is None
            and "没有找到记录编号" in missing["record_message"]
            and "1502.9" not in json.dumps(missing, ensure_ascii=False),
            missing["record_message"],
        )

        evidence_only = client.post(
            "/api/knowledge/research/validate",
            json={"entity_a": "齿轮裂纹", "entity_b": "轴承磨损"},
        ).json()
        check(
            "没有记录时只列出可检验条件和缺失项",
            evidence_only["record_id"] is None
            and evidence_only["observation_evidence"] is None
            and "转速(rpm)" in evidence_only["missing_measurements"]
            and "2X幅值" in evidence_only["missing_measurements"]
            and evidence_only["overall_status"] == "no_testable_hypothesis"
            and "没有可供因果验证的候选链" in evidence_only["validation_note"]
            and "1502.9" not in json.dumps(evidence_only, ensure_ascii=False)
            and "25.05" not in json.dumps(evidence_only, ensure_ascii=False),
            evidence_only["overall_status"],
        )
        record_only = validate_record_physics("REC-MA-20260403-00346")
        record_speed = find_check(record_only, "speed_frequency")
        record_xy = find_check(record_only, "xy_vibration")
        record_deviation = find_check(record_only, "historical_deviation_summary")
        repeated = validate_record_physics("REC-MA-20260403-00346")
        check(
            "没有故障假设时仍执行记录级转速和振动校验",
            record_only["validation_level"] == "record"
            and record_only["record_found"] is True
            and record_only["research_hypotheses"] == []
            and record_speed["status"] == "consistent"
            and abs(float(record_speed["inputs"]["转速(rpm)"]) - 1502.9) < 1e-9
            and abs(float(record_speed["inputs"]["转频(Hz)"]) - 25.05) < 1e-9
            and record_xy["status"] == "consistent"
            and record_xy["computed"]["relation"] == "X>Y"
            and record_deviation["status"] == "not_applicable"
            and any(item.get("field") == "Y向振动(mm/s)" for item in record_deviation["inputs"]["metrics"])
            and record_only["causal_conclusion"] is False
            and record_only["written_to_graph"] is False
            and record_only["overall_status"] != "verified"
            and "没有可供验证的故障假设" in record_only["validation_note"]
            and repeated["physical_checks"] == record_only["physical_checks"]
            and repeated["research_hypotheses"] == [],
            record_only["overall_status"],
        )
        gear_record = validate_record_physics("REC-GE-20260409-00447")
        gear_threshold = find_check(gear_record, "kb02_temperature_threshold")
        check(
            "没有适用故障时温度子句不适用且不解释成故障",
            gear_record["research_hypotheses"] == []
            and gear_threshold["status"] == "not_applicable"
            and gear_threshold["supports_fault"] is False
            and gear_record["causal_conclusion"] is False
            and threshold["status"] == "contradicted",
            gear_threshold.get("status"),
        )
        blank = validate_record_physics(None)
        blank_text = json.dumps(blank, ensure_ascii=False)
        check(
            "没有记录编号时不执行记录级测量校验",
            blank["record_id"] is None
            and blank["observation_evidence"] is None
            and find_check(blank, "no_observation")["status"] == "not_applicable"
            and "1502.9" not in blank_text
            and "25.05" not in blank_text
            and "3.017" not in blank_text,
            blank["validation_note"],
        )
        preserved = (
            "research_id",
            "entity_a",
            "entity_b",
            "record_id",
            "record_found",
            "physical_checks",
            "consistent_checks",
            "contradicted_checks",
            "insufficient_checks",
            "not_applicable_checks",
            "missing_measurements",
            "overall_status",
            "causal_validation_status",
            "causal_conclusion",
            "written_to_graph",
            "validation_note",
        )
        check("原有校验返回字段仍在", all(key in motor for key in preserved) and all(key in record_only for key in preserved))
    finally:
        reasoner.complete_research_chat = original

    reasoner.complete_research_chat = spy
    try:
        fixtures = validate_research_physics(
            "齿轮裂纹",
            "轴承磨损",
            evaluations=[
                {"check_id": "fixture-consistent", "source": "独立测试夹具", "applicable": True, "measured": 25.05, "reference": 25.048333333333336, "tolerance": 0.005, "basis": "夹具容差 0.005"},
                {"check_id": "fixture-contradicted", "source": "独立测试夹具", "applicable": True, "measured": 30, "reference": 25, "tolerance": 0.005, "basis": "夹具容差 0.005"},
                {"check_id": "fixture-insufficient", "source": "独立测试夹具", "applicable": True, "measured": None, "reference": 25, "tolerance": 0.005, "basis": "缺少测量值"},
                {"check_id": "fixture-not-applicable", "source": "独立测试夹具", "applicable": False, "measured": 1, "reference": 1, "tolerance": 0.1, "basis": "约束不适用于该对象"},
            ],
        )
        by_id = {item["check_id"]: item["status"] for item in fixtures["physical_checks"]}
        check(
            "夹具覆盖四种校验状态且都由计算得出",
            by_id.get("fixture-consistent") == "consistent"
            and by_id.get("fixture-contradicted") == "contradicted"
            and by_id.get("fixture-insufficient") == "insufficient_data"
            and by_id.get("fixture-not-applicable") == "not_applicable"
            and all(item["supports_causal_hypothesis"] is False for item in fixtures["physical_checks"]),
            str(by_id),
        )
        extracted = TEMP / "extracted"
        extracted.mkdir(parents=True, exist_ok=True)
        candidate = {
            "schema_version": "1.0",
            "file_id": "c" * 32,
            "extraction_status": "extracted",
            "relations": [
                {
                    "source_label": "齿轮裂纹",
                    "target_label": "轴承磨损",
                    "relation_type": "CAUSE_CANDIDATE",
                    "direction": "cause_to_effect",
                    "certainty": "uncertain",
                    "causal_status": "unverified",
                    "in_graph": False,
                    "evidence": [{"quote": "齿轮裂纹可能引起轴承磨损", "section_id": "section-001", "locator": {"type": "line", "index": 1}, "file_id": "c" * 32, "original_filename": "候选.txt"}],
                }
            ],
        }
        (extracted / f"{'c' * 32}.json").write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")
        reasoned = validate_research_physics("齿轮裂纹", "轴承磨损")
        check(
            "未验证假设不会变成已验证因果",
            reasoned["research_hypotheses"]
            and all(item["validation_status"] == "unverified" and item["causal_conclusion"] is False for item in reasoned["research_hypotheses"])
            and reasoned["causal_validation_status"] == "unverified"
            and reasoned["overall_status"] != "verified"
            and reasoned["written_to_graph"] is False,
            reasoned["overall_status"],
        )
    finally:
        reasoner.complete_research_chat = original

    kept = client.post("/api/knowledge/research/evidence", json={"entity_a": "棒线主轧机电机", "entity_b": "定子"}).json()
    report_text = json.dumps(motor, ensure_ascii=False) + json.dumps(gear, ensure_ascii=False) + json.dumps(reasoned, ensure_ascii=False)
    check("原有研究证据接口仍返回真实包含关系", kept["connection_paths"][0]["steps"][0]["relation"] == "HAS_COMPONENT" and kept["causal_conclusion"] is False)
    check("结果没有答案标签", all(label not in report_text for label in ANSWER_LABELS))
    check("没有调用 DeepSeek", calls == [])
    check(
        "正式图谱、Excel、入图库和监测库没有被改写",
        file_hash(GRAPH) == before_graph
        and file_hash(EXCEL) == before_excel
        and (file_hash(SQLITE) if SQLITE.is_file() else None) == before_sqlite
        and DEFAULT_IMPORTS.exists() == before_default
        and len(json.loads(GRAPH.read_text(encoding="utf-8"))["nodes"]) == 252
        and len(json.loads(GRAPH.read_text(encoding="utf-8"))["edges"]) == 464,
    )
    print("L2_MOTOR")
    print(json.dumps({"overall": motor["overall_status"], "speed": speed["status"], "xy": xy["computed"], "tolerance": speed["computed"].get("tolerance_hz")}, ensure_ascii=False))
    print("L2_GEAR")
    print(json.dumps({"overall": gear["overall_status"], "temps": readings["inputs"], "threshold": threshold["status"], "device_limit": limit["status"]}, ensure_ascii=False))
    print(f"{sum(CHECKS)}/{len(CHECKS)} passed")
    if not all(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
