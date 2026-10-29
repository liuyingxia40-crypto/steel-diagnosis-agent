"""数值观测与故障诊断联动测试。不调用 DeepSeek，不把统计偏离写成确诊。"""

import hashlib
import json
from pathlib import Path

from app.services.agent_tools import diagnose_fault
from app.services.anomaly_detection import detect_anomaly
from app.services.diagnostic_bridge import analyze_record_for_diagnosis
from app.services.knowledge_search import MARKDOWN_DIR
from app.services.official_dataset import DATASET_FILE, _WITHHELD_FIELDS, get_device_records, list_devices

ROOT = DATASET_FILE.parents[2]
CHECKS = []
XLSX_HASH = "1e9caeba1fe526f3ad1b5dd6445a9dbcc02fa22ec02273d9863ebc84e3e30f87"
DATASET_HASH = "871ff82d14c5ac80413fde16dbfc215c311fc5ee789cd30685cf81865fcd6b97"
ANOMALY_HASH = "15e26ee7c92a9321f36af1761e5355fe0054200babe34bfc8a0ee38138f84919"
RECORD_A = "REC-MA-20260403-00346"
RECORD_B = "REC-GE-20260409-00447"
PROTECTED = {
    "backend/app/main.py": None,
    "backend/app/services/deepseek.py": None,
    "backend/app/services/agent_tools.py": None,
    "backend/app/services/anomaly_detection.py": ANOMALY_HASH,
    "backend/app/services/official_dataset.py": DATASET_HASH,
    "data/official_dataset/钢铁设备数据集_开发集.xlsx": XLSX_HASH,
}


def check(name, ok, detail=""):
    CHECKS.append(bool(ok))
    print(("PASS" if ok else "FAIL"), name, detail)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def metric(result, field):
    return next(item for item in result["analyzed_metrics"] if item["field"] == field)


def feature(result, code):
    return next(item for item in result["feature_checks"] if item["feature_code"] == code)


def keys_of(value, found=None):
    if found is None:
        found = set()
    if isinstance(value, dict):
        found.update(value)
        for item in value.values():
            keys_of(item, found)
    elif isinstance(value, list):
        for item in value:
            keys_of(item, found)
    return found


def _greater(left, right) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        return False
    return left > right


def first_match(predicate):
    for device in list_devices()["devices"]:
        start = None
        seen = set()
        while True:
            page = get_device_records(device["device_id"], start_time=start, limit=100)
            rows = page["records"]
            if not rows:
                break
            for row in rows:
                if row["record_id"] in seen:
                    continue
                seen.add(row["record_id"])
                if predicate(row):
                    return row
            if not page["truncated"]:
                break
            nxt = rows[-1]["collected_at"]
            if nxt == start:
                break
            start = nxt
    return None


before = {path: file_hash(ROOT / path) for path in PROTECTED}
direct_a = detect_anomaly(RECORD_A)
result_a = analyze_record_for_diagnosis(RECORD_A)
y_direct = metric(direct_a, "Y向振动(mm/s)")
y_bridge = metric(result_a["statistical_deviation"], "Y向振动(mm/s)")
envelope = feature(result_a, "envelope_spectrum")
print(
    "A",
    result_a["diagnosis_level"],
    result_a["diagnose_fault_called"],
    result_a["candidate_faults"],
    y_bridge["current_value"],
    y_bridge["robust_z"],
)
check(
    "A 读取统计偏离但不认定轴承磨损",
    result_a["ok"] is True
    and result_a["observation"]["device_id"] == "MM-01"
    and result_a["statistical_deviation"]["detection_status"] == "偏离基线"
    and y_bridge["current_value"] == y_direct["current_value"] == 3.017
    and y_bridge["median"] == y_direct["median"]
    and y_bridge["robust_z"] == y_direct["robust_z"]
    and "包络谱峰值(g)" in result_a["statistical_deviation"]["anomalous_metrics"]
    and "BPFO幅值(g)" in result_a["statistical_deviation"]["anomalous_metrics"]
    and envelope["complete_condition_met"] is False
    and envelope["partial_condition_met"] is False
    and envelope["passed_to_diagnose_fault"] is False
    and result_a["diagnose_fault_called"] is False
    and result_a["candidate_faults"] == []
    and result_a["confirmed"] is False
    and result_a["diagnosis_level"] != "possible_match",
    str(y_bridge["robust_z"]),
)

direct_b = detect_anomaly(RECORD_B)
result_b = analyze_record_for_diagnosis(RECORD_B)
temperature = feature(result_b, "temperature")
print(
    "B",
    result_b["diagnosis_level"],
    result_b["diagnose_fault_called"],
    [item["value"] for item in temperature["observed_fields"]],
)
check(
    "B 读取两侧温度偏离但不认定润滑故障",
    result_b["ok"] is True
    and result_b["observation"]["device_id"] == "GB-01"
    and result_b["statistical_deviation"]["anomalous_metrics"] == direct_b["anomalous_metrics"]
    and "驱动端轴承温度(°C)" in direct_b["anomalous_metrics"]
    and "非驱动端轴承温度(°C)" in direct_b["anomalous_metrics"]
    and metric(direct_b, "驱动端轴承温度(°C)")["current_value"] == 77.0
    and metric(direct_b, "非驱动端轴承温度(°C)")["current_value"] == 73.2
    and temperature["complete_condition_met"] is False
    and temperature["passed_to_diagnose_fault"] is False
    and ("持续上升" in temperature["unverified_reason"] or "80" in temperature["unverified_reason"])
    and result_b["candidate_faults"] == []
    and result_b["confirmed"] is False,
)

xy_row = first_match(
    lambda row: _greater(row["values"].get("X向振动(mm/s)"), row["values"].get("Y向振动(mm/s)"))
)
hot_row = first_match(
    lambda row: any(_greater(row["values"].get(field), 80) for field in ("驱动端轴承温度(°C)", "非驱动端轴承温度(°C)"))
)
print("C scan", None if xy_row is None else xy_row["record_id"], None if hot_row is None else hot_row["record_id"])
if xy_row is None:
    check("C 找到可直接核对的 X>Y 记录", False, "开发集中没有 X向振动大于 Y向振动的记录")
    result_c = None
else:
    result_c = analyze_record_for_diagnosis(xy_row["record_id"])
    clue = next(item for item in result_c["diagnostic_clues"] if item["feature_code"] == "amplitude")
    kb_line = (MARKDOWN_DIR / "KB-02_故障模式诊断知识库.md").read_text(encoding="utf-8").splitlines()[
        clue["line_number"] - 1
    ]
    observed = {item["field"]: item["value"] for item in clue["observed_fields"]}
    print("C", result_c["diagnosis_level"], result_c["diagnose_fault_called"], clue["line_number"], observed)
    check(
        "C X>Y 线索可追溯且没有完整特征可匹配",
        result_c["ok"] is True
        and clue["partial_condition_met"] is True
        and clue["complete_condition_met"] is False
        and clue["passed_to_diagnose_fault"] is False
        and observed["X向振动(mm/s)"] == xy_row["values"]["X向振动(mm/s)"]
        and observed["Y向振动(mm/s)"] == xy_row["values"]["Y向振动(mm/s)"]
        and clue["document"] == "KB-02_故障模式诊断知识库.md"
        and clue["related_fault_section"] == "转子不平衡 / 频谱特征"
        and "X>Y" in kb_line
        and result_c["diagnose_fault_called"] is False
        and result_c["candidate_faults"] == []
        and result_c["diagnosis_level"] == "diagnostic_clue"
        and all(item["complete_condition_met"] is False for item in result_c["feature_checks"]),
        xy_row["record_id"],
    )
    check(
        "C 没有伪造完整满足 KB-02 的候选案例",
        hot_row is not None,
        "未找到任何一条仅靠现有 Excel 字段就能完整满足的频谱特征；高于 80°C 的记录仍不能单独完成温度条目。",
    )

missing = analyze_record_for_diagnosis("REC-NOT-EXIST-123")
blank = analyze_record_for_diagnosis("  ")
print("D", missing["error"])
check(
    "D 不存在的记录编号返回错误",
    missing["ok"] is False
    and missing["diagnosis_level"] == "unable_to_evaluate"
    and missing["observation"] is None
    and missing["candidate_faults"] == []
    and "没有找到记录编号「REC-NOT-EXIST-123」" in missing["error"]
    and "3.017" not in json.dumps(missing, ensure_ascii=False)
    and blank["ok"] is False
    and "记录编号" in blank["error"],
)

blocked_codes = (
    "envelope_spectrum",
    "kurtosis",
    "crest_factor",
    "dominant_frequency",
    "harmonic_2x",
    "phase",
    "broadband",
)
check(
    "E 缺少频谱、相位或波形时不生成诊断特征",
    all(feature(result_a, code)["complete_condition_met"] is False for code in blocked_codes)
    and all(feature(result_a, code)["passed_to_diagnose_fault"] is False for code in blocked_codes)
    and {"峭度", "峰值因子", "2X幅值", "相位", "原始振动波形"} <= set(result_a["missing_data"])
    and "原始波形" in feature(result_a, "kurtosis")["unverified_reason"]
    and "2X" in feature(result_a, "harmonic_2x")["unverified_reason"]
    and "相位" in feature(result_a, "phase")["unverified_reason"],
)

if hot_row is not None:
    hot = analyze_record_for_diagnosis(hot_row["record_id"])
    hot_temp = feature(hot, "temperature")
    print("E hot", hot_row["record_id"], hot_temp["partial_condition_met"], hot_temp["complete_condition_met"], hot["diagnosis_level"])
    check(
        "E 单次高温不形成润滑故障特征",
        hot["ok"] is True
        and hot_temp["complete_condition_met"] is False
        and hot_temp["passed_to_diagnose_fault"] is False
        and hot["diagnose_fault_called"] is False
        and all(item["fault"] != "润滑故障" for item in hot["candidate_faults"]),
        hot_row["record_id"],
    )
else:
    check("E 单次高温不形成润滑故障特征", False, "没有找到温度高于 80°C 的记录")

payloads = [result_a, result_b, missing, result_c, hot if hot_row is not None else {}]
blob = json.dumps(payloads, ensure_ascii=False)
after = {path: file_hash(ROOT / path) for path in PROTECTED}
check(
    "F 没有答案标签泄漏且 Excel 哈希不变",
    _WITHHELD_FIELDS.isdisjoint(keys_of(payloads))
    and all(name not in blob for name in _WITHHELD_FIELDS)
    and before == after
    and after["data/official_dataset/钢铁设备数据集_开发集.xlsx"] == XLSX_HASH
    and after["backend/app/services/official_dataset.py"] == DATASET_HASH
    and after["backend/app/services/anomaly_detection.py"] == ANOMALY_HASH,
)

again = detect_anomaly(RECORD_A)
matched = diagnose_fault(["包络谱中出现明显的轴承特征频率（BPFO/BPFI/BSF）及其谐波（2X、3X）"])
check(
    "G 原有异常检测和故障匹配仍可用",
    again["detection_status"] == direct_a["detection_status"]
    and metric(again, "Y向振动(mm/s)")["robust_z"] == y_direct["robust_z"]
    and matched["ok"] is True
    and matched["status"] == "possible_match"
    and matched["confirmed"] is False
    and matched["matches"][0]["fault"] == "轴承磨损",
    matched["status"],
)
print("SUMMARY", sum(CHECKS), "/", len(CHECKS))
raise SystemExit(0 if all(CHECKS) else 1)
