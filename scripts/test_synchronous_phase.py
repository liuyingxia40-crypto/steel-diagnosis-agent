"""同步多通道相位差。信号全部标记为合成测试，不是工厂测量。"""

from __future__ import annotations

import hashlib
import math
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
TEMP = Path(tempfile.mkdtemp(prefix="sync-phase-"))
os.environ["SUPPLEMENTARY_MEASUREMENT_DB"] = str(TEMP / "supplements.sqlite3")
os.environ["FOLLOWUP_TASK_DB"] = str(TEMP / "followup.sqlite3")
os.environ["DIAGNOSIS_HISTORY_DB"] = str(TEMP / "history.sqlite3")
os.environ["MONITORING_EVENTS_DB"] = str(TEMP / "events.sqlite3")
os.environ["SUPERVISOR_TRACE_DB"] = str(TEMP / "traces.sqlite3")
os.environ["STARTUP_TOOL_SELFTEST"] = "0"

from app.services.diagnosis_history import list_assessments, save_assessment
from app.services.fault_revision import assess_saved_record
from app.services.followup_tasks import generate_followup_tasks, sync_followup_tasks
from app.services.knowledge_physical_validator import validate_record_physics
from app.services.official_dataset import get_observation
from app.services.supplementary_measurement import (
    attach_synchronous_csv,
    attach_vibration_csv,
    list_for_record,
    list_synchronous_for_record,
)
from app.services.vibration_phase import interchannel_phase

RECORD = "REC-MA-20260403-00346"
GRAPH = ROOT / "data" / "processed" / "graph" / "graph.json"
EXCEL = ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"
EVENTS = ROOT / "data" / "runtime" / "monitoring_events.sqlite3"
ENV_FILE = ROOT / "backend" / ".env"
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


def sync_csv(rate: int, count: int, phase_deg: float, frequency: float = 25.0, amplitude: float = 1.0) -> bytes:
    phase = math.radians(phase_deg)
    lines = ["time_s,x,y"]
    for index in range(count):
        time_s = index / rate
        left = amplitude * math.sin(2 * math.pi * frequency * time_s)
        right = amplitude * math.sin(2 * math.pi * frequency * time_s + phase)
        lines.append(f"{time_s},{left},{right}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def channels() -> list[dict]:
    return [
        {"column": "x", "name": "X", "direction": "X", "sensor_position": "驱动端水平"},
        {"column": "y", "name": "Y", "direction": "Y", "sensor_position": "驱动端垂直"},
    ]


def find_check(report: dict, check_id: str) -> dict:
    return next((item for item in report["physical_checks"] if item["check_id"] == check_id), {})


def task_by(tasks: list[dict], item_id: str) -> dict:
    return next(item for item in tasks if item["item_id"] == item_id)


def main() -> None:
    before = {
        "graph": file_hash(GRAPH),
        "excel": file_hash(EXCEL),
        "events": file_hash(EVENTS) if EVENTS.is_file() else "",
        "env": file_hash(ENV_FILE) if ENV_FILE.is_file() else "",
    }
    observation = get_observation(RECORD)["record"]
    device_id = observation["device_id"]
    measured_at = observation["collected_at"]
    content = sync_csv(256, 256, 90)
    direct = interchannel_phase(
        [math.sin(2 * math.pi * 25 * index / 256) for index in range(256)],
        [math.sin(2 * math.pi * 25 * index / 256 + math.pi / 2) for index in range(256)],
        256,
        25.05,
        "X",
        "Y",
    )
    check(
        "1 已知相位差的合成信号能恢复合理结果",
        direct["phase_reliable"] is True
        and abs(float(direct["phase_difference_deg"]) - 90) < 5
        and direct["absolute_phase_deg"] is None
        and direct["keyphasor_present"] is False
        and direct["clause_satisfied"] is False
        and "缺少键相参考" in direct["reason"],
        direct,
    )

    mismatched_rate = attach_synchronous_csv(
        RECORD,
        device_id,
        measured_at,
        128,
        "mm/s",
        "bad-rate.csv",
        content,
        channels(),
        sync_method="shared_time_column",
        time_base="relative_seconds",
        data_origin="synthetic_test_signal",
    )
    uneven = ["time_s,x,y"]
    for index in range(256):
        uneven.append(f"{index / 256 + (0.001 if index % 17 == 0 else 0)},{index},{index}")
    mismatched_time = attach_synchronous_csv(
        RECORD,
        device_id,
        measured_at,
        256,
        "mm/s",
        "bad-time.csv",
        ("\n".join(uneven) + "\n").encode("utf-8"),
        channels(),
        sync_method="shared_time_column",
        time_base="relative_seconds",
        data_origin="synthetic_test_signal",
    )
    check(
        "2 不同采样率或时间基准不能直接计算相位差",
        mismatched_rate["ok"] is False
        and "采样率" in mismatched_rate["error"]
        and mismatched_time["ok"] is False
        and ("时间" in mismatched_time["error"] or "间隔" in mismatched_time["error"]),
        (mismatched_rate.get("error"), mismatched_time.get("error")),
    )

    single = attach_vibration_csv(
        RECORD,
        device_id,
        measured_at,
        "驱动端水平",
        256,
        "mm/s",
        "single.csv",
        ("value\n" + "\n".join(str(math.sin(2 * math.pi * 25 * index / 256)) for index in range(256)) + "\n").encode("utf-8"),
        data_origin="synthetic_test_signal",
    )
    after_single = validate_record_physics(RECORD)
    phase_only = find_check(after_single, "supplement_phase")
    inter_only = find_check(after_single, "supplement_interchannel_phase")
    check(
        "3 单通道不能生成通道间相位",
        single["stored"] is True
        and phase_only["status"] == "insufficient_data"
        and "只有一个通道" in phase_only["limit"]
        and inter_only == {},
        phase_only.get("limit"),
    )

    stored = attach_synchronous_csv(
        RECORD,
        device_id,
        measured_at,
        256,
        "mm/s",
        "synthetic-phase-90.csv",
        content,
        channels(),
        sync_method="shared_time_column",
        time_base="relative_seconds",
        data_origin="synthetic_test_signal",
    )
    measurement = stored["measurement"]
    phase = measurement["phase"]
    check(
        "4 没有键相参考时不能生成绝对相位",
        stored["stored"] is True
        and phase["phase_reliable"] is True
        and phase["absolute_phase_deg"] is None
        and phase["keyphasor_present"] is False
        and phase["clause_satisfied"] is False
        and abs(float(phase["phase_difference_deg"]) - 90) < 5
        and "缺少键相参考" in phase["reason"],
        phase,
    )

    short = attach_synchronous_csv(
        RECORD,
        device_id,
        measured_at,
        256,
        "mm/s",
        "short.csv",
        sync_csv(256, 16, 90),
        channels(),
        sync_method="shared_time_column",
        time_base="relative_seconds",
        data_origin="synthetic_test_signal",
    )
    noisy = attach_synchronous_csv(
        RECORD,
        device_id,
        measured_at,
        256,
        "mm/s",
        "off-bin.csv",
        sync_csv(256, 256, 90, frequency=80),
        channels(),
        sync_method="shared_time_column",
        time_base="relative_seconds",
        data_origin="synthetic_test_signal",
    )
    short_phase = (short.get("measurement") or {}).get("phase") or {}
    noisy_phase = (noisy.get("measurement") or {}).get("phase") or {}
    check(
        "5 1X 信号质量不足时不输出可靠相位",
        short.get("ok") is True
        and short_phase.get("phase_reliable") is False
        and ("周期" in short_phase.get("reason", "") or "1X" in short_phase.get("reason", ""))
        and noisy.get("ok") is True
        and noisy_phase.get("phase_reliable") is False
        and "1X" in noisy_phase.get("reason", ""),
        (short_phase.get("reason"), noisy_phase.get("reason")),
    )

    bad_device = attach_synchronous_csv(
        RECORD,
        "XX-99",
        measured_at,
        256,
        "mm/s",
        "device.csv",
        content,
        channels(),
        sync_method="shared_time_column",
        time_base="relative_seconds",
        data_origin="synthetic_test_signal",
    )
    bad_time = attach_synchronous_csv(
        RECORD,
        device_id,
        "2000-01-01 00:00:00",
        256,
        "mm/s",
        "time.csv",
        content,
        channels(),
        sync_method="shared_time_column",
        time_base="relative_seconds",
        data_origin="synthetic_test_signal",
    )
    check(
        "6 设备或记录时间不匹配时拒绝关联",
        bad_device["ok"] is False and "设备" in bad_device["error"] and bad_time["ok"] is False and "时间" in bad_time["error"],
        (bad_device.get("error"), bad_time.get("error")),
    )

    check(
        "7 计算结果有测量编号和 SHA-256",
        bool(measurement["measurement_id"])
        and len(measurement["content_sha256"]) == 64
        and measurement["content_sha256"] == hashlib.sha256(content).hexdigest()
        and measurement["channel_mode"] == "synchronous"
        and len(measurement["channels"]) == 2,
        measurement["measurement_id"],
    )

    report = validate_record_physics(RECORD)
    clause = find_check(report, "supplement_phase")
    inter = find_check(report, "supplement_interchannel_phase")
    official_phase = find_check(report, "phase_stability")
    check(
        "8 只有匹配的 L2 条款才能使用新增相位",
        official_phase["status"] == "insufficient_data"
        and clause["status"] == "insufficient_data"
        and clause["computed"]["clause_satisfied"] is False
        and clause["computed"]["reference_matches_clause"] is False
        and inter["status"] == "consistent"
        and inter["computed"]["interchannel_phase_computed"] is True
        and "相位" in report["missing_measurements"]
        and clause["supports_fault"] is False
        and inter["supports_fault"] is False,
        (clause.get("limit"), inter.get("status")),
    )

    followups = generate_followup_tasks(RECORD)
    synced = sync_followup_tasks(RECORD, report)
    phase_task = task_by(synced["tasks"], "phase_stability")
    axial = task_by(synced["tasks"], "axial_eccentricity")
    check(
        "9 不适用的补测任务不会自动完成",
        phase_task["status"] == "已校验但条件不足"
        and measurement["measurement_id"] in phase_task["measurement_ids"]
        and "缺少键相参考" in phase_task["condition_text"]
        and axial["status"] == "待补测"
        and axial["measurement_ids"] == []
        and followups["hypothesis_created"] is False,
        (phase_task["status"], axial["status"]),
    )

    prior = save_assessment(
        RECORD,
        device_id,
        diagnosis_level="diagnostic_clue",
        stop_reason="现有数据无法验证关键条件",
        prior_hypotheses=[],
        hypotheses=[
            {
                "hypothesis_id": "H1",
                "compound": False,
                "faults": ["转子不平衡"],
                "equipment": [],
                "components": [],
                "basis": "测试用的未验证假设。",
                "version": 1,
                "verification_status": "未验证",
                "credibility": "未评估",
                "verification_method": "测试",
                "source_locators": ["KB-02 第38行"],
                "version_history": [],
                "supporting_evidence": [{"evidence_id": "D-1", "source": "diagnose_fault", "locator": "KB-02 第38行", "detail": "线索", "fault": "转子不平衡"}],
                "opposing_evidence": [],
                "missing_measurements": ["相位"],
                "revision_reason": "测试用的未验证假设。",
                "written_to_graph": False,
                "causal_conclusion": False,
            }
        ],
        physics={},
        measurements=[],
        missing_measurements=["相位"],
        public_answer="未验证",
    )
    duplicate = attach_synchronous_csv(
        RECORD,
        device_id,
        measured_at,
        256,
        "mm/s",
        "synthetic-phase-90-again.csv",
        content,
        channels(),
        sync_method="shared_time_column",
        time_base="relative_seconds",
        data_origin="synthetic_test_signal",
    )
    reassess = assess_saved_record(
        RECORD,
        device_id,
        [
            {
                "id": "analysis",
                "name": "analyze_equipment_record",
                "result": {
                    "ok": True,
                    "record_id": RECORD,
                    "device_id": device_id,
                    "diagnosis_level": "diagnostic_clue",
                    "confirmed": False,
                    "candidate_faults": [],
                    "diagnostic_clues": [],
                },
                "arguments": {"record_id": RECORD},
            }
        ],
        execute=lambda *_args, **_kwargs: {"ok": False, "error": "测试不应再调用工具"},
        physics=lambda *_args, **_kwargs: validate_record_physics(RECORD),
        diagnosis_level="diagnostic_clue",
        max_rounds=1,
        measurements=list_for_record(RECORD) + list_synchronous_for_record(RECORD),
    )
    latest = next((item for item in reassess["hypotheses"] if item.get("faults") == ["转子不平衡"]), {})
    digest = hashlib.sha256(content).hexdigest()
    same_digest = [item for item in list_synchronous_for_record(RECORD) if item["content_sha256"] == digest]
    check(
        "10 重复数据不造成重复证据和假设升版",
        duplicate["duplicate"] is True
        and duplicate["stored"] is False
        and len(same_digest) == 1
        and latest.get("version") == 1
        and len(list_assessments(RECORD)) >= 2
        and prior["assessment_id"] != reassess.get("assessment_id"),
        (duplicate.get("duplicate"), latest.get("version"), len(same_digest)),
    )

    check(
        "11 原有单通道分析和诊断历史保持正常",
        len(list_for_record(RECORD)) >= 1
        and single["measurement"]["measurement_id"] in {item["measurement_id"] for item in list_for_record(RECORD)}
        and find_check(validate_record_physics(RECORD), "supplement_dominant_1x")["status"] in {"consistent", "contradicted", "insufficient_data"}
        and list_assessments(RECORD)[0]["assessment_id"] == prior["assessment_id"],
    )

    after = {
        "graph": file_hash(GRAPH),
        "excel": file_hash(EXCEL),
        "events": file_hash(EVENTS) if EVENTS.is_file() else "",
        "env": file_hash(ENV_FILE) if ENV_FILE.is_file() else "",
    }
    check("12 正式图谱、官方 Excel 和监测事件库不变", before == after, {"before": before, "after": after})
    print(f"RESULT passed={sum(RESULTS)} failed={len(RESULTS) - sum(RESULTS)}")
    if not all(RESULTS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
