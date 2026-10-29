"""补充振动测量进入 L2 的测试。信号是合成测试信号，不是工厂测量。"""

from __future__ import annotations

import hashlib
import math
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
TEMP = Path(tempfile.mkdtemp(prefix="supplement-measure-"))
os.environ["SUPPLEMENTARY_MEASUREMENT_DB"] = str(TEMP / "supplements.sqlite3")
os.environ["DIAGNOSIS_HISTORY_DB"] = str(TEMP / "diagnosis-history.sqlite3")
os.environ["FOLLOWUP_TASK_DB"] = str(TEMP / "followup-tasks.sqlite3")
os.environ["MONITORING_EVENTS_DB"] = str(TEMP / "events.sqlite3")
os.environ["SUPERVISOR_TRACE_DB"] = str(TEMP / "traces.sqlite3")
os.environ["STARTUP_TOOL_SELFTEST"] = "0"

from app.services.diagnostic_bridge import analyze_record_for_diagnosis
from app.services.fault_revision import _local_reassess_physics, reassess_record, run_closed_loop
from app.services.knowledge_physical_validator import validate_record_physics
from app.services.official_dataset import get_observation
from app.services.supplementary_measurement import attach_vibration_csv, list_for_record, revalidate_record

RECORD = "REC-MA-20260403-00346"
GRAPH = ROOT / "data" / "processed" / "graph" / "graph.json"
EXCEL = ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"
EVENTS = ROOT / "data" / "runtime" / "monitoring_events.sqlite3"
ENV_FILE = ROOT / "backend" / ".env"
DEFAULT_DB = ROOT / "data" / "runtime" / "supplementary_measurements.sqlite3"
RESULTS: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append(bool(ok))
    print(("PASS" if ok else "FAIL"), name, detail)


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
    text = "value\n" + "\n".join(str(item) for item in samples) + "\n"
    return text.encode("utf-8")


def find_check(report: dict, check_id: str) -> dict:
    return next((item for item in report["physical_checks"] if item["check_id"] == check_id), {})


def statuses(report: dict) -> dict[str, str]:
    return {item["check_id"]: item["status"] for item in report["physical_checks"]}


def diagnosis_view(result: dict) -> dict:
    detected = result.get("statistical_deviation") if isinstance(result.get("statistical_deviation"), dict) else {}
    clues = result.get("diagnostic_clues") if isinstance(result.get("diagnostic_clues"), list) else []
    return {
        "diagnosis_level": result.get("diagnosis_level"),
        "confirmed": result.get("confirmed"),
        "candidate_faults": result.get("candidate_faults"),
        "diagnose_fault_called": result.get("diagnose_fault_called"),
        "detection_status": detected.get("detection_status"),
        "clue_codes": [item.get("feature_code") for item in clues if isinstance(item, dict)],
    }


def main() -> None:
    before = {str(path): file_hash(path) for path in (GRAPH, EXCEL, EVENTS, ENV_FILE)}
    default_existed = DEFAULT_DB.is_file()
    default_hash = file_hash(DEFAULT_DB)
    found = get_observation(RECORD)
    record = found["record"]
    device_id = record["device_id"]
    measured_at = record["collected_at"]
    baseline_bridge = diagnosis_view(analyze_record_for_diagnosis(RECORD))
    baseline = validate_record_physics(RECORD)
    baseline_status = statuses(baseline)
    check(
        "没有补充数据时真实记录仍不能由波形确诊",
        baseline["supplementary_measurements"] == []
        and baseline_status["waveform_statistics"] == "insufficient_data"
        and baseline_status["second_harmonic_ratio"] == "insufficient_data"
        and baseline_status["phase_stability"] == "insufficient_data"
        and baseline_status["speed_frequency"] == "consistent"
        and "峭度" in baseline["missing_measurements"]
        and "相位" in baseline["missing_measurements"]
        and baseline["causal_conclusion"] is False
        and baseline["written_to_graph"] is False
        and baseline_bridge["candidate_faults"] == []
        and baseline_bridge["confirmed"] is False,
        baseline["overall_status"],
    )

    rejected = [
        attach_vibration_csv(RECORD, "XX-99", measured_at, "驱动端", 256, "mm/s", "reject.csv", csv_bytes(tone(80, 256, 256, 1)), data_origin="synthetic_test_signal"),
        attach_vibration_csv(RECORD, device_id, "2000-01-01 00:00:00", "驱动端", 256, "mm/s", "reject.csv", csv_bytes(tone(80, 256, 256, 1)), data_origin="synthetic_test_signal"),
        attach_vibration_csv(RECORD, device_id, measured_at, "驱动端", 256, "°C", "reject.csv", csv_bytes(tone(80, 256, 256, 1)), data_origin="synthetic_test_signal"),
        attach_vibration_csv(RECORD, device_id, measured_at, "驱动端", 256, "mm/s", "reject.csv", csv_bytes([0, 1, 0, 1]), data_origin="synthetic_test_signal"),
        attach_vibration_csv(RECORD, device_id, measured_at, "驱动端", 256, "mm/s", "D:\\hidden\\reject.csv", csv_bytes(tone(80, 256, 256, 1)), data_origin="synthetic_test_signal"),
    ]
    check(
        "设备、时间、单位或样本不合格时不保存",
        all(item["ok"] is False and item["stored"] is False for item in rejected)
        and list_for_record(RECORD) == []
        and not Path(os.environ["SUPPLEMENTARY_MEASUREMENT_DB"]).is_file(),
        " | ".join(item["error"] for item in rejected),
    )

    oppose_samples = tone(80, 256, 256, 1)
    oppose_bytes = csv_bytes(oppose_samples)
    oppose = attach_vibration_csv(
        RECORD,
        device_id,
        measured_at,
        "驱动端水平",
        256,
        "mm/s",
        "synthetic-oppose.csv",
        oppose_bytes,
        data_origin="synthetic_test_signal",
    )
    measurement = oppose["measurement"]
    metrics = measurement["metrics"]
    refreshed = revalidate_record(RECORD)["report"]
    dominant = find_check(refreshed, "supplement_dominant_1x")
    waveform = find_check(refreshed, "supplement_waveform_statistics")
    ratio = find_check(refreshed, "supplement_second_harmonic_ratio")
    phase = find_check(refreshed, "supplement_phase")
    bearing = find_check(refreshed, "supplement_bearing_harmonics")
    peaks = find_check(refreshed, "supplement_frequency_peaks")
    isolation = find_check(refreshed, "supplement_amplitude_isolation")
    check(
        "合成信号算出峭度、峰值因子和频率峰且不伪装成工厂数据",
        oppose["stored"] is True
        and measurement["data_origin"] == "synthetic_test_signal"
        and "不是工厂测量" in measurement["origin_note"]
        and measurement["content_sha256"] == hashlib.sha256(oppose_bytes).hexdigest()
        and metrics["algorithm"] == "analyze_vibration"
        and metrics["crest_factor"] is not None
        and metrics["kurtosis_pearson"] is not None
        and metrics["phase_difference"] is None
        and metrics["bearing_harmonics"] is None
        and metrics["compared_with_official_amplitudes"] is False
        and peaks["status"] == "consistent"
        and waveform["status"] == "consistent"
        and waveform["supports_fault"] is False
        and abs(float(dominant["computed"]["measurements"][0]["dominant_frequency_hz"]) - 80) <= 1
        and dominant["status"] == "contradicted"
        and dominant["condition_effect"] == "opposes_condition"
        and dominant["supports_fault"] is False
        and ratio["status"] == "insufficient_data"
        and phase["status"] == "insufficient_data"
        and bearing["status"] == "insufficient_data"
        and isolation["computed"]["compared_with_official_amplitudes"] is False
        and find_check(refreshed, "waveform_statistics")["status"] == "insufficient_data"
        and find_check(refreshed, "speed_frequency")["status"] == "consistent"
        and "峭度" not in refreshed["missing_measurements"]
        and "相位" in refreshed["missing_measurements"]
        and "2X幅值" in refreshed["missing_measurements"]
        and "BPFO及其谐波的频谱形态" in refreshed["missing_measurements"]
        and refreshed["overall_status"] == "numeric_contradiction"
        and refreshed["causal_conclusion"] is False
        and refreshed["written_to_graph"] is False
        and refreshed["research_hypotheses"] == [],
        dominant.get("status"),
    )

    duplicate = attach_vibration_csv(
        RECORD,
        device_id,
        measured_at,
        "另一个位置",
        256,
        "mm/s",
        "synthetic-oppose-again.csv",
        oppose_bytes,
        data_origin="synthetic_test_signal",
    )
    check(
        "相同文件重复上传不新增记录",
        duplicate["duplicate"] is True
        and duplicate["stored"] is False
        and duplicate["measurement"]["measurement_id"] == measurement["measurement_id"]
        and len(list_for_record(RECORD)) == 1,
    )

    def physics(_entity_a: str, _entity_b: str, record_id: str) -> dict:
        return validate_record_physics(record_id)

    def execute(name: str, _arguments: str) -> dict:
        if name != "search_knowledge":
            raise AssertionError(name)
        return {"ok": True, "results": [], "query": "轴承磨损"}

    loop = run_closed_loop(
        f"记录 {RECORD} 可能是什么故障？",
        [
            {"id": "analysis", "name": "analyze_equipment_record", "result": {"ok": True, "record_id": RECORD, "device_id": device_id, "diagnostic_clues": [], "candidate_faults": []}, "arguments": {"record_id": RECORD}},
            {"id": "relations", "name": "get_equipment_relations", "result": {"ok": True, "ambiguous": False, "count": 0, "node": {"name": "棒线主轧机电机"}, "relations": []}, "arguments": {"name": "棒线主轧机电机"}},
            {
                "id": "diagnosis",
                "name": "diagnose_fault",
                "result": {
                    "ok": True,
                    "status": "possible_match",
                    "confirmed": False,
                    "equipment_name": "棒线主轧机电机",
                    "matches": [{"fault": "轴承磨损", "confirmed": False, "matched_features": [{"document": "KB-02.md", "locator": "第8行"}]}],
                },
                "arguments": {"observed_features": ["旧特征"]},
            },
        ],
        execute=execute,
        physics=physics,
        max_rounds=2,
    )
    hypothesis = next(item for item in loop["fault_report"]["hypotheses"] if item["faults"] == ["轴承磨损"])
    check(
        "补充测量的数值矛盾只削弱假设且不确诊",
        hypothesis["verification_status"] == "被削弱"
        and hypothesis["version"] == 1
        and hypothesis["version_history"] == []
        and hypothesis["causal_conclusion"] is False
        and hypothesis["written_to_graph"] is False
        and any(item.get("evidence_id") == "L2-supplement_dominant_1x" and measurement["measurement_id"] in item.get("detail", "") for item in hypothesis["opposing_evidence"])
        and "已验证" not in hypothesis["verification_status"]
        and "已确诊" not in loop["fault_report"]["public_answer"],
        hypothesis["verification_status"],
    )

    aligned_bytes = csv_bytes([left + right for left, right in zip(tone(25, 256, 256, 1), tone(50, 256, 256, 0.3))])
    aligned = attach_vibration_csv(
        RECORD,
        device_id,
        measured_at,
        "驱动端水平",
        256,
        "mm/s",
        "synthetic-aligned.csv",
        aligned_bytes,
        data_origin="synthetic_test_signal",
    )
    combined = validate_record_physics(RECORD)
    combined_dominant = find_check(combined, "supplement_dominant_1x")
    combined_wave = find_check(combined, "supplement_waveform_statistics")
    combined_ratio = find_check(combined, "supplement_second_harmonic_ratio")
    ratio_rows = combined_ratio["computed"].get("measurements") or []
    check(
        "两份补充测量分别保留，不合并成一个幅值",
        aligned["stored"] is True
        and len(list_for_record(RECORD)) == 2
        and combined_dominant["status"] == "insufficient_data"
        and combined_dominant["computed"]["values_merged"] is False
        and {item["dominant_is_1x"] for item in combined_dominant["computed"]["measurements"]} == {True, False}
        and combined_wave["computed"]["values_merged"] is False
        and len(combined_wave["computed"]["measurements"]) == 2
        and "crest_factor" not in combined_wave["computed"]
        and combined_ratio["status"] == "insufficient_data"
        and ratio_rows == []
        and combined["causal_conclusion"] is False
        and all(item["supports_fault"] is False for item in combined["physical_checks"]),
        combined_dominant.get("limit"),
    )
    single_ratio = None
    saved = os.environ["SUPPLEMENTARY_MEASUREMENT_DB"]
    only_aligned = TEMP / "aligned-only.sqlite3"
    os.environ["SUPPLEMENTARY_MEASUREMENT_DB"] = str(only_aligned)
    alone = attach_vibration_csv(
        RECORD,
        device_id,
        measured_at,
        "驱动端水平",
        256,
        "mm/s",
        "synthetic-aligned.csv",
        aligned_bytes,
        data_origin="synthetic_test_signal",
    )
    alone_report = validate_record_physics(RECORD)
    alone_ratio = find_check(alone_report, "supplement_second_harmonic_ratio")
    alone_dominant = find_check(alone_report, "supplement_dominant_1x")
    if alone_ratio["computed"].get("measurements"):
        single_ratio = alone_ratio["computed"]["measurements"][0]
    os.environ["SUPPLEMENTARY_MEASUREMENT_DB"] = saved
    check(
        "同一信号能得到1X、2X和比值且不比较官方幅值",
        alone["stored"] is True
        and single_ratio is not None
        and abs(float(single_ratio["frequency_1x_hz"]) - 25.05) <= 0.5
        and abs(float(single_ratio["frequency_2x_hz"]) - 50.1) <= 0.5
        and float(single_ratio["amplitude_1x"]) > float(single_ratio["amplitude_2x"]) > 0
        and 0.15 <= float(single_ratio["ratio_2x_to_1x"]) <= 0.5
        and alone_dominant["status"] == "consistent"
        and alone_dominant["supports_fault"] is False
        and find_check(alone_report, "supplement_phase")["status"] == "insufficient_data"
        and find_check(alone_report, "supplement_bearing_harmonics")["status"] == "insufficient_data"
        and find_check(alone_report, "speed_frequency")["inputs"]["转速(rpm)"] == find_check(baseline, "speed_frequency")["inputs"]["转速(rpm)"]
        and "1X是否为频谱主导峰" not in alone_report["missing_measurements"]
        and "2X幅值" not in alone_report["missing_measurements"]
        and "相位" in alone_report["missing_measurements"],
        single_ratio,
    )

    version_db = TEMP / "version.sqlite3"
    os.environ["SUPPLEMENTARY_MEASUREMENT_DB"] = str(version_db)
    version_saved = attach_vibration_csv(
        RECORD,
        device_id,
        measured_at,
        "驱动端水平",
        256,
        "mm/s",
        "synthetic-oppose.csv",
        oppose_bytes,
        data_origin="synthetic_test_signal",
    )
    version_tools = [
        {"id": "analysis", "name": "analyze_equipment_record", "result": {"ok": True, "record_id": RECORD, "device_id": device_id, "diagnostic_clues": [], "candidate_faults": []}, "arguments": {"record_id": RECORD}},
        {"id": "relations", "name": "get_equipment_relations", "result": {"ok": True, "ambiguous": False, "count": 0, "node": {"name": "棒线主轧机电机"}, "relations": []}, "arguments": {"name": "棒线主轧机电机"}},
        {
            "id": "diagnosis",
            "name": "diagnose_fault",
            "result": {
                "ok": True,
                "status": "possible_match",
                "confirmed": False,
                "matches": [{"fault": "轴承磨损", "confirmed": False, "matched_features": [{"document": "KB-02.md", "locator": "第8行"}]}],
            },
            "arguments": {"observed_features": ["旧特征"]},
        },
    ]
    from app.services import knowledge_research_reasoner as reasoner

    model_calls: list[str] = []
    original_chat = reasoner.complete_research_chat

    def blocked_chat(*_args, **_kwargs):
        model_calls.append("deepseek")
        raise AssertionError("重新评估不应调用 DeepSeek")

    reasoner.complete_research_chat = blocked_chat
    first_loop = run_closed_loop(f"记录 {RECORD} 可能是什么故障？", version_tools, physics=_local_reassess_physics, max_rounds=1)
    second_loop = run_closed_loop(
        f"记录 {RECORD} 可能是什么故障？",
        version_tools,
        physics=_local_reassess_physics,
        max_rounds=1,
        prior_hypotheses=first_loop["fault_report"]["hypotheses"],
        prior_physics=first_loop["fault_report"]["physics"],
    )
    first_fault = next(item for item in first_loop["fault_report"]["hypotheses"] if item["faults"] == ["轴承磨损"])
    second_fault = next(item for item in second_loop["fault_report"]["hypotheses"] if item["faults"] == ["轴承磨损"])
    local_reassess = reassess_record(RECORD)
    reasoner.complete_research_chat = original_chat
    check(
        "补充证据进入闭环且重复评估不升版也不确诊",
        version_saved["stored"] is True
        and model_calls == []
        and first_fault["verification_status"] == "被削弱"
        and first_fault["version"] == 1
        and second_fault["version"] == 1
        and second_fault["version_history"] == []
        and version_saved["measurement"]["measurement_id"] in first_fault["opposing_evidence"][0]["detail"]
        and all(item.get("verification_status") != "已验证" for item in second_loop["fault_report"]["hypotheses"])
        and "转子不平衡" not in [name for item in local_reassess["hypotheses"] for name in item["faults"]]
        and local_reassess["model_called"] is False
        and local_reassess["written_to_graph"] is False
        and local_reassess["confirmed"] is False
        and version_saved["measurement"]["measurement_id"] in local_reassess["public_answer"],
        local_reassess.get("stop_reason"),
    )
    os.environ["SUPPLEMENTARY_MEASUREMENT_DB"] = saved
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        params = {
            "device_id": device_id,
            "measured_at": measured_at,
            "sensor_position": "驱动端水平",
            "sampling_rate": "256",
            "unit": "mm/s",
            "file_name": "synthetic-oppose.csv",
            "data_origin": "synthetic_test_signal",
        }
        mismatched = client.post(
            f"/api/dataset/records/{RECORD}/supplementary-vibration",
            params={**params, "device_id": "XX-99"},
            content=oppose_bytes,
            headers={"Content-Type": "text/csv"},
        )
        duplicate = client.post(
            f"/api/dataset/records/{RECORD}/supplementary-vibration",
            params=params,
            content=oppose_bytes,
            headers={"Content-Type": "text/csv"},
        )
        listed = client.get(f"/api/dataset/records/{RECORD}/supplementary-vibration")
        revalidated = client.post(f"/api/dataset/records/{RECORD}/physical-revalidation")
    mismatch_body = mismatched.json()
    duplicate_body = duplicate.json()
    listed_body = listed.json()
    report = revalidated.json()["report"]
    official_ratio = next(item for item in report["physical_checks"] if item["check_id"] == "second_harmonic_ratio")
    check(
        "接口拒绝不匹配设备并避免重复保存",
        mismatched.status_code == 422
        and "设备" in mismatch_body["message"]
        and duplicate.status_code == 200
        and duplicate_body["duplicate"] is True
        and any(item["content_sha256"] == hashlib.sha256(oppose_bytes).hexdigest() for item in listed_body["measurements"])
        and official_ratio["status"] == "insufficient_data"
        and report["written_to_graph"] is False,
        mismatch_body.get("message"),
    )

    os.environ["SUPPLEMENTARY_MEASUREMENT_DB"] = str(TEMP / "absent.sqlite3")
    untouched = validate_record_physics(RECORD)
    later_bridge = diagnosis_view(analyze_record_for_diagnosis(RECORD))
    check(
        "撤掉补充数据库后真实记录诊断与原来一致",
        statuses(untouched) == baseline_status
        and untouched["physical_checks"] == baseline["physical_checks"]
        and untouched["missing_measurements"] == baseline["missing_measurements"]
        and later_bridge == baseline_bridge,
    )
    after = {str(path): file_hash(path) for path in (GRAPH, EXCEL, EVENTS, ENV_FILE)}
    check(
        "正式图谱、Excel、监测库和密钥文件没有变化",
        before == after and DEFAULT_DB.is_file() is default_existed and file_hash(DEFAULT_DB) == default_hash,
    )
    print(f"RESULT passed={sum(RESULTS)} failed={len(RESULTS) - sum(RESULTS)}")
    if not all(RESULTS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
