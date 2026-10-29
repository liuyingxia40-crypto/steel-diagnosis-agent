"""结构化轴向和其他诊断证据。使用临时库和合成数据。"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
TEMP = Path(tempfile.mkdtemp(prefix="diagnostic-evidence-"))
os.environ["DIAGNOSTIC_EVIDENCE_DB"] = str(TEMP / "evidence.sqlite3")
os.environ["SUPPLEMENTARY_MEASUREMENT_DB"] = str(TEMP / "supplements.sqlite3")
os.environ["FOLLOWUP_TASK_DB"] = str(TEMP / "followup.sqlite3")
os.environ["DIAGNOSIS_HISTORY_DB"] = str(TEMP / "history.sqlite3")
os.environ["MONITORING_EVENTS_DB"] = str(TEMP / "events.sqlite3")
os.environ["SUPERVISOR_TRACE_DB"] = str(TEMP / "traces.sqlite3")
os.environ["STARTUP_TOOL_SELFTEST"] = "0"

from app.services.diagnosis_history import list_assessments, save_assessment
from app.services.diagnostic_evidence import list_evidence, register_evidence
from app.services.fault_revision import assess_saved_record
from app.services.followup_tasks import generate_followup_tasks, sync_followup_tasks
from app.services.knowledge_physical_validator import validate_record_physics
from app.services.official_dataset import get_observation

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


def find_check(report: dict, check_id: str) -> dict:
    return next((item for item in report["physical_checks"] if item["check_id"] == check_id), {})


def task_by(tasks: list[dict], item_id: str) -> dict:
    return next(item for item in tasks if item["item_id"] == item_id)


def base_payload(**extra):
    observation = get_observation(RECORD)["record"]
    payload = {
        "device_id": observation["device_id"],
        "measured_at": observation["collected_at"],
        "sensor_position": "轴向探头",
        "data_origin": "synthetic_test_signal",
    }
    payload.update(extra)
    return payload


def main() -> None:
    before = {
        "graph": file_hash(GRAPH),
        "excel": file_hash(EXCEL),
        "events": file_hash(EVENTS) if EVENTS.is_file() else "",
        "env": file_hash(ENV_FILE) if ENV_FILE.is_file() else "",
    }
    observation = get_observation(RECORD)["record"]
    device_id = observation["device_id"]

    missing = TEMP / "absent.sqlite3"
    os.environ["DIAGNOSTIC_EVIDENCE_DB"] = str(missing)
    absent = list_evidence(RECORD)
    os.environ["DIAGNOSTIC_EVIDENCE_DB"] = str(TEMP / "evidence.sqlite3")
    check("读取结构化证据时不创建数据库", absent == [] and not missing.exists())

    try:
        register_evidence(RECORD, base_payload(evidence_kind="axial_eccentricity", value="正常", unit="um", method="runout_measurement", reference_basis="shaft_axis"))
        verbal_ok = False
        verbal_detail = "accepted"
    except Exception as exc:
        verbal_ok = "口头" in str(exc) or "数值" in str(exc)
        verbal_detail = str(exc)
    check("拒绝口头正常结论", verbal_ok, verbal_detail)

    try:
        register_evidence(
            RECORD,
            base_payload(evidence_kind="axial_eccentricity", value=12, unit="mm/s", method="runout_measurement", reference_basis="shaft_axis"),
        )
        unit_mixed = False
    except Exception as exc:
        unit_mixed = "单位" in str(exc)
    check("轴向位移/振动/偏心单位不能混用", unit_mixed)

    displacement = register_evidence(
        RECORD,
        base_payload(evidence_kind="axial_displacement", value=8.5, unit="um", method="eddy_current_probe", reference_basis="housing_relative"),
    )
    vibration = register_evidence(
        RECORD,
        base_payload(evidence_kind="axial_vibration", value=1.2, unit="mm/s", method="accelerometer", reference_basis="bearing_housing_axial"),
    )
    eccentricity = register_evidence(
        RECORD,
        base_payload(evidence_kind="axial_eccentricity", value=15, unit="um", method="runout_measurement", reference_basis="shaft_axis"),
    )
    check(
        "轴向位移、振动、偏心分别保存且不混淆",
        displacement["stored"] is True
        and vibration["stored"] is True
        and eccentricity["stored"] is True
        and {item["evidence_kind"] for item in list_evidence(RECORD)} >= {"axial_displacement", "axial_vibration", "axial_eccentricity"}
        and displacement["evidence"]["values"]["not_axial_eccentricity"] is True
        and eccentricity["evidence"]["values"]["not_axial_displacement"] is True
        and eccentricity["evidence"]["values"]["clause_satisfied"] is False,
        [item["evidence_kind"] for item in list_evidence(RECORD)],
    )

    try:
        register_evidence(
            RECORD,
            base_payload(evidence_kind="axial_eccentricity", value=15, unit="um", method="runout_measurement", reference_basis="unknown"),
        )
        no_ref = False
    except Exception as exc:
        no_ref = "参考基准" in str(exc)
    check("缺少参考基准时拒绝", no_ref)

    single_load = register_evidence(
        RECORD,
        base_payload(
            evidence_kind="load_1x_series",
            method="keyphasor_load_test",
            reference_basis="keyphasor",
            unit="mm/s",
            observations=[{"load": 50, "load_unit": "%", "amplitude_1x": 1.1, "amplitude_unit": "mm/s", "phase_1x_deg": 10, "keyphasor_present": True}],
        ),
    )
    check(
        "单次负荷采样不能验证相位稳定条款",
        single_load["stored"] is True
        and single_load["evidence"]["validation_status"] == "仍然证据不足"
        and "单次" in single_load["evidence"]["validation_note"]
        and single_load["evidence"]["values"]["clause_satisfied"] is False,
        single_load["evidence"]["validation_note"],
    )

    multi_no_key = register_evidence(
        RECORD,
        base_payload(
            evidence_kind="load_1x_series",
            method="load_sweep",
            reference_basis="process_load",
            unit="mm/s",
            observations=[
                {"load": 40, "load_unit": "%", "amplitude_1x": 1.0, "amplitude_unit": "mm/s", "phase_1x_deg": None, "keyphasor_present": False},
                {"load": 80, "load_unit": "%", "amplitude_1x": 1.4, "amplitude_unit": "mm/s", "phase_1x_deg": None, "keyphasor_present": False},
            ],
        ),
    )
    check(
        "多负荷但无键相仍不能验证相位稳定",
        multi_no_key["stored"] is True
        and multi_no_key["evidence"]["validation_status"] == "仍然证据不足"
        and "键相" in multi_no_key["evidence"]["validation_note"],
        multi_no_key["evidence"]["validation_note"],
    )

    envelope = register_evidence(
        RECORD,
        base_payload(
            evidence_kind="envelope_bearing_harmonics",
            method="envelope_spectrum",
            reference_basis="bpfo_catalog",
            unit="g",
            peaks=[{"frequency_hz": 120.0, "amplitude": 0.2, "label": "BPFO"}, {"frequency_hz": 240.0, "amplitude": 0.1, "label": "2X"}],
        ),
    )
    check(
        "包络谐波可登记但待验证",
        envelope["stored"] is True
        and envelope["evidence"]["validation_status"] == "已登记、待验证"
        and envelope["evidence"]["values"]["clause_satisfied"] is False
        and envelope["evidence"]["values"]["algorithm_verified"] is False,
        envelope["evidence"]["validation_status"],
    )

    generate_followup_tasks(RECORD)
    report = validate_record_physics(RECORD)
    axial_disp = find_check(report, "supplement_axial_displacement")
    axial_ecc = find_check(report, "supplement_axial_eccentricity")
    load_check = find_check(report, "supplement_load_1x_series")
    envelope_check = find_check(report, "supplement_envelope_bearing_harmonics")
    synced = sync_followup_tasks(RECORD, report)
    axial_task = task_by(synced["tasks"], "axial_eccentricity")
    phase_task = task_by(synced["tasks"], "phase_stability")
    check(
        "合法证据进入 L2 且条款未自动满足",
        axial_disp["status"] == "consistent"
        and axial_ecc["status"] == "consistent"
        and axial_ecc["computed"]["clause_satisfied"] is False
        and axial_ecc["supports_fault"] is False
        and load_check["status"] == "insufficient_data"
        and envelope_check["status"] == "insufficient_data"
        and "轴向偏心测量" not in report["missing_measurements"]
        and "相位" in report["missing_measurements"]
        and eccentricity["evidence"]["content_sha256"]
        and axial_task["status"] == "已获得可用证据"
        and eccentricity["evidence"]["evidence_id"] in axial_task["measurement_ids"]
        and phase_task["status"] == "待补测",
        (axial_task["status"], report["missing_measurements"]),
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
                "basis": "测试",
                "version": 1,
                "verification_status": "未验证",
                "credibility": "未评估",
                "verification_method": "测试",
                "source_locators": [],
                "version_history": [],
                "supporting_evidence": [],
                "opposing_evidence": [],
                "missing_measurements": ["轴向偏心测量"],
                "revision_reason": "测试假设",
                "written_to_graph": False,
                "causal_conclusion": False,
            }
        ],
        physics={},
        measurements=[],
        missing_measurements=["轴向偏心测量"],
        public_answer="未验证",
    )
    duplicate = register_evidence(
        RECORD,
        base_payload(evidence_kind="axial_eccentricity", value=15, unit="um", method="runout_measurement", reference_basis="shaft_axis"),
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
        execute=lambda *_a, **_k: {"ok": False, "error": "不应再调用"},
        physics=lambda *_a, **_k: validate_record_physics(RECORD),
        diagnosis_level="diagnostic_clue",
        max_rounds=1,
        measurements=list_evidence(RECORD),
    )
    latest = next((item for item in reassess["hypotheses"] if item.get("faults") == ["转子不平衡"]), {})
    check(
        "重复证据不导致假设错误升版，历史可追加",
        duplicate["duplicate"] is True
        and duplicate["stored"] is False
        and latest.get("version") == 1
        and len(list_assessments(RECORD)) >= 2
        and prior["assessment_id"] != reassess.get("assessment_id")
        and reassess["confirmed"] is False
        and reassess["written_to_graph"] is False,
        (duplicate, latest.get("version")),
    )

    after = {
        "graph": file_hash(GRAPH),
        "excel": file_hash(EXCEL),
        "events": file_hash(EVENTS) if EVENTS.is_file() else "",
        "env": file_hash(ENV_FILE) if ENV_FILE.is_file() else "",
    }
    check("正式图谱、官方 Excel 和监测事件库不变", before == after, {"before": before, "after": after})
    print(f"RESULT passed={sum(RESULTS)} failed={len(RESULTS) - sum(RESULTS)}")
    if not all(RESULTS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
