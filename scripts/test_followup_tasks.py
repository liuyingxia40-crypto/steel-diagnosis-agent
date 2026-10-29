"""补测任务使用临时库和合成振动。MM-01 的官方记录只读。"""

from __future__ import annotations

import hashlib
import math
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
TEMP = Path(tempfile.mkdtemp(prefix="followup-tasks-"))
os.environ["FOLLOWUP_TASK_DB"] = str(TEMP / "followup.sqlite3")
os.environ["SUPPLEMENTARY_MEASUREMENT_DB"] = str(TEMP / "supplements.sqlite3")
os.environ["DIAGNOSIS_HISTORY_DB"] = str(TEMP / "history.sqlite3")
os.environ["DIAGNOSTIC_EVIDENCE_DB"] = str(TEMP / "evidence.sqlite3")
os.environ["MONITORING_EVENTS_DB"] = str(TEMP / "events.sqlite3")
os.environ["SUPERVISOR_TRACE_DB"] = str(TEMP / "traces.sqlite3")
os.environ["STARTUP_TOOL_SELFTEST"] = "0"

from fastapi.testclient import TestClient

from app.main import app
from app.services.diagnosis_history import get_assessment, list_assessments, save_assessment
from app.services.followup_tasks import database_path, generate_followup_tasks, list_followup_tasks, sync_followup_tasks
from app.services.knowledge_physical_validator import validate_record_physics
from app.services.official_dataset import get_observation
from app.services.supplementary_measurement import attach_vibration_csv, list_for_record

RECORD = "REC-MA-20260403-00346"
GRAPH = ROOT / "data" / "processed" / "graph" / "graph.json"
EXCEL = ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"
EVENTS = ROOT / "data" / "runtime" / "monitoring_events.sqlite3"
ENV_FILE = ROOT / "backend" / ".env"
DEFAULT_DB = ROOT / "data" / "runtime" / "followup_tasks.sqlite3"
RESULTS: list[bool] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    RESULTS.append(bool(ok))
    print(("PASS" if ok else "FAIL"), name, "" if ok else detail)


def file_hash(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tone(frequency: float, rate: int, count: int, amplitude: float) -> list[float]:
    return [amplitude * math.sin(2 * math.pi * frequency * index / rate) for index in range(count)]


def csv_bytes(samples: list[float]) -> bytes:
    return ("value\n" + "\n".join(str(item) for item in samples) + "\n").encode("utf-8")


def task_by(tasks: list[dict], item_id: str) -> dict:
    return next(item for item in tasks if item["item_id"] == item_id)


def check_by(report: dict, check_id: str) -> dict:
    return next(item for item in report["physical_checks"] if item["check_id"] == check_id)


def main() -> None:
    missing = TEMP / "absent.sqlite3"
    os.environ["FOLLOWUP_TASK_DB"] = str(missing)
    absent = list_followup_tasks(RECORD)
    os.environ["FOLLOWUP_TASK_DB"] = str(TEMP / "followup.sqlite3")
    check("读取补测任务时不创建数据库", absent == [] and not missing.exists())

    before = {
        "graph": file_hash(GRAPH),
        "excel": file_hash(EXCEL),
        "events": file_hash(EVENTS) if EVENTS.is_file() else "",
        "env": file_hash(ENV_FILE) if ENV_FILE.is_file() else "",
        "default_exists": DEFAULT_DB.exists(),
        "default_size": DEFAULT_DB.stat().st_size if DEFAULT_DB.exists() else 0,
    }
    from app.services import knowledge_research_reasoner as reasoner

    calls: list[str] = []
    original = reasoner.complete_research_chat

    def blocked(*_args, **_kwargs):
        calls.append("deepseek")
        raise AssertionError("补测任务不应调用 DeepSeek")

    reasoner.complete_research_chat = blocked
    first = generate_followup_tasks(RECORD)
    second = generate_followup_tasks(RECORD)
    reasoner.complete_research_chat = original
    titles = {item["title"] for item in first["tasks"]}
    blocked_titles = {"峭度与峰值因子", "包络谱谐波形态", "啮合频率边带", "金属摩擦异音"}
    axial = task_by(first["tasks"], "axial_eccentricity")
    phase = task_by(first["tasks"], "phase_stability")
    check(
        "1 MM-01 按实际缺口生成补测建议",
        first["hypothesis_created"] is False
        and first["confirmed"] is False
        and first["written_to_graph"] is False
        and calls == []
        and titles == {"轴向偏心测量", "1X是否为频谱主导峰", "2X与1X比值", "相位"}
        and axial["priority"] == "直接缺口"
        and "第38行" in axial["knowledge_ref"]
        and "不是工业风险等级" in axial["priority_basis"]
        and phase["upload_supported"] is True
        and phase.get("upload_mode") == "synchronous"
        and all(item["status"] == "待补测" and item["gap_class"] == "missing_measurement" for item in first["tasks"])
        and {item["check_id"] for item in first["already_calculated"]} >= {"speed_frequency", "xy_vibration"},
        titles,
    )
    skipped = {item["title"] for item in first["not_applicable"]}
    check(
        "2 没有依据的项目不会被推荐",
        blocked_titles.isdisjoint(titles) and blocked_titles <= skipped and "温度数值子句" in "".join(item["title"] for item in first["not_applicable"]),
        skipped,
    )
    check(
        "3 相同项目不会重复生成",
        second["created_count"] == 0
        and [item["task_id"] for item in second["tasks"]] == [item["task_id"] for item in first["tasks"]]
        and all(len(item["events"]) == 1 for item in second["tasks"]),
        second["created_count"],
    )
    check("9 没有故障假设时仍有依据建议且不生成假设", list_assessments(RECORD) == [] and first["hypothesis_created"] is False and first["tasks"])

    observation = get_observation(RECORD)["record"]
    device_id = observation["device_id"]
    measured_at = observation["collected_at"]
    before_status = [(item["task_id"], item["status"], len(item["events"])) for item in list_followup_tasks(RECORD)]
    rejected = attach_vibration_csv(
        RECORD,
        "XX-99",
        measured_at,
        "驱动端水平",
        256,
        "mm/s",
        "reject.csv",
        csv_bytes(tone(25, 256, 256, 1)),
        data_origin="synthetic_test_signal",
    )
    after_reject = [(item["task_id"], item["status"], len(item["events"])) for item in list_followup_tasks(RECORD)]
    check("6 数据不匹配时任务状态不变", rejected["ok"] is False and rejected["stored"] is False and after_reject == before_status and list_for_record(RECORD) == [], rejected.get("error"))

    saved = save_assessment(
        RECORD,
        device_id,
        diagnosis_level="diagnostic_clue",
        stop_reason="现有数据无法验证关键条件",
        prior_hypotheses=[],
        hypotheses=[
            {
                "hypothesis_id": "H1",
                "faults": ["转子不平衡"],
                "version": 1,
                "verification_status": "未验证",
                "version_history": [],
                "supporting_evidence": [],
                "opposing_evidence": [],
                "missing_measurements": ["相位"],
                "revision_reason": "测试用的已有未验证假设。",
            }
        ],
        physics={},
        measurements=[],
        missing_measurements=["相位"],
        public_answer="未验证假设，不是确诊。",
    )
    aligned = [left + right for left, right in zip(tone(25, 256, 256, 1), tone(50, 256, 256, 0.3))]
    stored = attach_vibration_csv(
        RECORD,
        device_id,
        measured_at,
        "驱动端水平",
        256,
        "mm/s",
        "synthetic-aligned.csv",
        csv_bytes(aligned),
        data_origin="synthetic_test_signal",
    )
    synced = sync_followup_tasks(RECORD)
    report = validate_record_physics(RECORD)
    tasks = {item["item_id"]: item for item in synced["tasks"]}
    dominant_check = check_by(report, "supplement_dominant_1x")
    ratio_check = check_by(report, "supplement_second_harmonic_ratio")
    phase_check = check_by(report, "supplement_phase")
    official_dominant = check_by(report, "dominant_1x")
    measurement_id = stored["measurement"]["measurement_id"]
    check(
        "4 合法 CSV 只更新实际可计算的项目",
        stored["stored"] is True
        and tasks["dominant_1x"]["status"] == "已获得可用证据"
        and tasks["second_harmonic_ratio"]["status"] == "已获得可用证据"
        and measurement_id in tasks["dominant_1x"]["measurement_ids"]
        and measurement_id in tasks["second_harmonic_ratio"]["measurement_ids"]
        and tasks["phase_stability"]["status"] == "待补测"
        and tasks["axial_eccentricity"]["status"] == "待补测"
        and tasks["phase_stability"]["measurement_ids"] == []
        and tasks["axial_eccentricity"]["measurement_ids"] == []
        and tasks["dominant_1x"]["supports_fault"] is False,
        {key: value["status"] for key, value in tasks.items()},
    )
    check(
        "5 单通道 CSV 不能完成相位补测",
        tasks["phase_stability"]["status"] == "待补测"
        and "单通道不能生成通道间相位" in tasks["phase_stability"]["condition_text"]
        and "缺少键相参考" in tasks["phase_stability"]["condition_text"]
        and tasks["phase_stability"]["measurement_ids"] == []
        and phase_check["status"] == "insufficient_data"
        and tasks["phase_stability"]["supplement_check_status"] == "insufficient_data",
        tasks["phase_stability"]["condition_text"],
    )
    check(
        "7 任务状态与 L2 补充检查一致且不改写官方缺失",
        tasks["dominant_1x"]["supplement_check_status"] == dominant_check["status"]
        and dominant_check["status"] == "consistent"
        and tasks["second_harmonic_ratio"]["supplement_check_status"] == ratio_check["status"]
        and ratio_check["status"] == "consistent"
        and tasks["dominant_1x"]["official_check_status"] == "insufficient_data"
        and official_dominant["status"] == "insufficient_data"
        and "官方检查 dominant_1x 是数据不足" in tasks["dominant_1x"]["condition_text"]
        and "已获得可用证据" in "".join(item["to_status"] for item in tasks["dominant_1x"]["events"])
        and any(item["to_status"] == "已上传待校验" for item in tasks["dominant_1x"]["events"]),
        tasks["dominant_1x"]["condition_text"],
    )
    kept = get_assessment(RECORD, saved["assessment_id"])
    check(
        "8 已有假设和版本历史不被覆盖",
        kept is not None
        and kept["hypotheses_detail"][0]["version"] == 1
        and kept["hypotheses_detail"][0]["verification_status"] == "未验证"
        and kept["hypotheses_detail"][0]["faults"] == ["转子不平衡"]
        and len(list_assessments(RECORD)) == 1
        and list_assessments(RECORD)[0]["assessment_id"] == saved["assessment_id"],
        kept,
    )

    with TestClient(app) as client:
        listed = client.get(f"/api/dataset/records/{RECORD}/followup-tasks")
        created = client.post(f"/api/dataset/records/{RECORD}/followup-tasks")
    listed_body = listed.json()
    created_body = created.json()
    check(
        "接口可以读取和再次生成且不复制任务",
        listed.status_code == 200
        and len(listed_body["tasks"]) == 4
        and created.status_code == 200
        and created_body["created_count"] == 0
        and created_body["hypothesis_created"] is False
        and "samples" not in str(created_body)
        and "api_key" not in str(created_body),
        created.status_code,
    )
    after = {
        "graph": file_hash(GRAPH),
        "excel": file_hash(EXCEL),
        "events": file_hash(EVENTS) if EVENTS.is_file() else "",
        "env": file_hash(ENV_FILE) if ENV_FILE.is_file() else "",
        "default_exists": DEFAULT_DB.exists(),
        "default_size": DEFAULT_DB.stat().st_size if DEFAULT_DB.exists() else 0,
    }
    check("11 正式图谱、官方 Excel 和监测事件库不变", before == after, {"before": before, "after": after})
    print(f"RESULT passed={sum(RESULTS)} failed={len(RESULTS) - sum(RESULTS)}")
    if database_path().is_file():
        pass
    if not all(RESULTS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
