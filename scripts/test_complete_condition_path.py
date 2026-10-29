"""完整条件可达性与特征映射回归。

使用临时库；合成轴向偏心证据仅用于验证代码分支，不冒充官方 Excel 观测。
不改正式图谱 / Excel / 正式监测库。
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TEMP = Path(tempfile.mkdtemp(prefix="complete-condition-", dir=str(ROOT / "data" / "runtime")))
os.environ["DIAGNOSTIC_EVIDENCE_DB"] = str(TEMP / "evidence.sqlite3")
os.environ["SUPPLEMENTARY_MEASUREMENT_DB"] = str(TEMP / "supplements.sqlite3")
os.environ["FOLLOWUP_TASK_DB"] = str(TEMP / "followup.sqlite3")
os.environ["DIAGNOSIS_HISTORY_DB"] = str(TEMP / "history.sqlite3")
os.environ["MONITORING_EVENTS_DB"] = str(TEMP / "events.sqlite3")
os.environ["SUPERVISOR_TRACE_DB"] = str(TEMP / "traces.sqlite3")
os.environ["EPISODE_DIAGNOSIS_ENABLED"] = "0"
os.environ["STARTUP_TOOL_SELFTEST"] = "0"

from app.services.diagnostic_bridge import analyze_record_for_diagnosis
from app.services.diagnostic_evidence import register_evidence
from app.services.fault_confirmation import evaluate_fault_confirmation
from app.services.official_dataset import get_observation

EXCEL = ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"
EXPECTED_HASH = "1e9caeba1fe526f3ad1b5dd6445a9dbcc02fa22ec02273d9863ebc84e3e30f87"
# 官方真实：统计偏离、X<=Y
STATS_ONLY = "REC-MA-20260401-00319"
# 官方真实：X>Y，无轴向偏心
PARTIAL_XY = "REC-MA-20260314-00001"
# 官方真实：可用于挂载合成轴向偏心（与 test_diagnostic_evidence 一致）
AXIAL_HOST = "REC-MA-20260403-00346"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((name, passed, detail))
    print(("PASS " if passed else "FAIL ") + name + ("" if not detail else " | " + detail))


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def amplitude_check(analysis: dict) -> dict:
    for item in analysis.get("feature_checks") or []:
        if isinstance(item, dict) and item.get("feature_code") == "amplitude":
            return item
    return {}


def temperature_check(analysis: dict) -> dict:
    for item in analysis.get("feature_checks") or []:
        if isinstance(item, dict) and item.get("feature_code") == "temperature":
            return item
    return {}


def main() -> None:
    before = file_hash(EXCEL)
    check("预检 Excel 未改", before == EXPECTED_HASH, before)

    stats = analyze_record_for_diagnosis(STATS_ONLY)
    conf_stats = evaluate_fault_confirmation(analysis=stats)
    check(
        "1 官方统计偏离记录不告警",
        stats.get("ok") is True
        and conf_stats.get("fault_alert_required") is False
        and (stats.get("statistical_deviation") or {}).get("detection_status") == "偏离基线"
        and not any(c.get("complete_condition_met") is True for c in (stats.get("feature_checks") or []) if isinstance(c, dict)),
        conf_stats.get("diagnosis_phase"),
    )

    partial = analyze_record_for_diagnosis(PARTIAL_XY)
    amp = amplitude_check(partial)
    conf_partial = evaluate_fault_confirmation(analysis=partial)
    check(
        "2 X/Y 不平衡但缺轴向测量时不是完整条件",
        amp.get("partial_condition_met") is True
        and amp.get("complete_condition_met") is not True
        and conf_partial.get("fault_alert_required") is False,
        f"partial={amp.get('partial_condition_met')} complete={amp.get('complete_condition_met')} phase={conf_partial.get('diagnosis_phase')}",
    )

    check(
        "3 缺少必要测量时保持数据不足说明",
        "轴向" in str(amp.get("unverified_reason") or "")
        and "轴向偏心测量" in (partial.get("missing_data") or []),
        str(amp.get("unverified_reason") or "")[:120],
    )

    # 特征映射：应能得到 matched_feature_codes，但仍不确诊/不告警
    cands = partial.get("candidate_faults") or []
    codes = []
    for cand in cands:
        if isinstance(cand, dict):
            codes.extend(cand.get("matched_feature_codes") or [])
    check(
        "4 结构化特征映射可产生候选代码但不确诊",
        partial.get("diagnose_fault_called") is True
        and "amplitude" in codes
        and all(isinstance(c, dict) and c.get("confirmed") is not True for c in cands)
        and conf_partial.get("fault_alert_required") is False,
        f"called={partial.get('diagnose_fault_called')} codes={codes} level={partial.get('diagnosis_level')}",
    )

    # 合成轴向偏心：明确标注 synthetic，挂到官方记录上仅测代码分支
    host = get_observation(AXIAL_HOST)["record"]
    registered = register_evidence(
        AXIAL_HOST,
        {
            "device_id": host["device_id"],
            "measured_at": host["collected_at"],
            "sensor_position": "轴向探头-合成测试",
            "data_origin": "synthetic_test_signal",
            "evidence_kind": "axial_eccentricity",
            "value": 15,
            "unit": "um",
            "method": "runout_measurement",
            "reference_basis": "shaft_axis",
        },
    )
    check("5a 合成轴向偏心已登记", registered.get("stored") is True, str(registered.get("evidence", {}).get("evidence_id")))

    with_axial = analyze_record_for_diagnosis(AXIAL_HOST)
    amp_full = amplitude_check(with_axial)
    host_xy = None
    values = (with_axial.get("observation") or {}).get("values") or {}
    x_val, y_val = values.get("X向振动(mm/s)"), values.get("Y向振动(mm/s)")
    if isinstance(x_val, (int, float)) and isinstance(y_val, (int, float)):
        host_xy = x_val > y_val
    check(
        "5b 合成证据+官方X>Y时完整条件路径可执行",
        host_xy is True
        and amp_full.get("complete_condition_met") is True
        and amp_full.get("partial_condition_met") is True
        and bool(amp_full.get("evidence_refs")),
        f"xy={host_xy} complete={amp_full.get('complete_condition_met')} x={x_val} y={y_val}",
    )

    conf_full = evaluate_fault_confirmation(analysis=with_axial)
    full_cands = with_axial.get("candidate_faults") or []
    full_codes = []
    for cand in full_cands:
        if isinstance(cand, dict):
            full_codes.extend(str(code) for code in (cand.get("matched_feature_codes") or []))
    # 完整条件 + amplitude 候选对齐时应允许告警资格
    alert_ok = conf_full.get("fault_alert_required") is True and "amplitude" in full_codes
    check(
        "5c 完整条件与候选特征对齐时可告警（合成证据，非官方真实正向样本）",
        alert_ok and with_axial.get("confirmed") is False,
        f"alert={conf_full.get('fault_alert_required')} phase={conf_full.get('diagnosis_phase')} codes={full_codes} SYNTHETIC",
    )

    # 故意错配：完整条件存在但候选代码不含 amplitude
    mismatched = evaluate_fault_confirmation(
        analysis={
            **with_axial,
            "candidate_faults": [
                {
                    "fault": "轴承磨损",
                    "confirmed": False,
                    "matched_feature_codes": ["envelope_spectrum"],
                    "source": "diagnose_fault",
                }
            ],
        }
    )
    check(
        "6 完整条件与特征代码不匹配时不告警",
        mismatched.get("fault_alert_required") is False,
        mismatched.get("diagnosis_phase"),
    )

    failed = evaluate_fault_confirmation(model_failed=True, diagnosis_error="工具失败")
    check(
        "7 工具/模型失败不告警",
        failed.get("fault_alert_required") is False and failed.get("diagnosis_phase") == "diagnosis_failed",
    )

    # 温度不得因自定义趋势变完整
    temp_sample = analyze_record_for_diagnosis("REC-CC-20260314-03371")
    temp = temperature_check(temp_sample)
    check(
        "8 温度条款即使 >80 与自定义上升也不是完整条件",
        temp.get("complete_condition_met") is not True,
        f"partial={temp.get('partial_condition_met')} complete={temp.get('complete_condition_met')}",
    )

    # 轴向位移不能代替偏心
    os.environ["DIAGNOSTIC_EVIDENCE_DB"] = str(TEMP / "evidence-disp.sqlite3")
    host2 = get_observation(PARTIAL_XY)["record"]
    register_evidence(
        PARTIAL_XY,
        {
            "device_id": host2["device_id"],
            "measured_at": host2["collected_at"],
            "sensor_position": "轴向探头",
            "data_origin": "synthetic_test_signal",
            "evidence_kind": "axial_displacement",
            "value": 8.5,
            "unit": "um",
            "method": "eddy_current_probe",
            "reference_basis": "housing_relative",
        },
    )
    disp_only = analyze_record_for_diagnosis(PARTIAL_XY)
    amp_disp = amplitude_check(disp_only)
    check(
        "9 轴向位移不能代替轴向偏心完整条件",
        amp_disp.get("partial_condition_met") is True and amp_disp.get("complete_condition_met") is not True,
        amp_disp.get("unverified_reason", "")[:100],
    )

    check("10 正式 Excel 未被改写", file_hash(EXCEL) == before == EXPECTED_HASH)
    check(
        "11 合成分支明细含 SYNTHETIC 标注",
        any("SYNTHETIC" in detail for _, _, detail in RESULTS),
    )

    failed_names = [name for name, passed, _ in RESULTS if not passed]
    print(f"{len(RESULTS) - len(failed_names)}/{len(RESULTS)} passed")
    if failed_names:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
