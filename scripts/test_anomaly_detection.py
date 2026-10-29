"""异常检测测试。人工序列会标明为算法测试序列，不是官方设备数据。"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from app.services.anomaly_detection import detect_anomaly, detect_test_series
from app.services.official_dataset import (
    DATASET_FILE,
    MAX_LIMIT,
    _WITHHELD_FIELDS,
    get_device_records,
    get_observation,
)

ROOT = DATASET_FILE.parents[2]
CHECKS = []
XLSX_HASH = "1e9caeba1fe526f3ad1b5dd6445a9dbcc02fa22ec02273d9863ebc84e3e30f87"
DATASET_HASH = "871ff82d14c5ac80413fde16dbfc215c311fc5ee789cd30685cf81865fcd6b97"
LABEL_NAMES = _WITHHELD_FIELDS | {"数据集划分"}


def check(name, ok, detail=""):
    CHECKS.append(ok)
    print(("PASS" if ok else "FAIL"), name, detail)


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def earlier_records(device_id, current_time):
    found = []
    seen = set()
    start = None
    while True:
        page = get_device_records(device_id, start_time=start, end_time=current_time, limit=MAX_LIMIT)
        rows = page["records"]
        if not rows:
            break
        for row in rows:
            if row["record_id"] in seen:
                continue
            seen.add(row["record_id"])
            if row["collected_at"] < current_time:
                found.append(row)
        if not page["truncated"] or rows[-1]["collected_at"] == current_time:
            break
        nxt = rows[-1]["collected_at"]
        if nxt == start:
            break
        start = nxt
    return found


def last_record(device_id):
    start = None
    last = None
    while True:
        page = get_device_records(device_id, start_time=start, limit=MAX_LIMIT)
        if not page["records"]:
            break
        last = page["records"][-1]
        if not page["truncated"]:
            break
        nxt = last["collected_at"]
        if nxt == start:
            break
        start = nxt
    return last


def walk(value, keys, texts):
    if isinstance(value, dict):
        keys.update(value)
        for item in value.values():
            walk(item, keys, texts)
    elif isinstance(value, list):
        for item in value:
            walk(item, keys, texts)
    elif isinstance(value, str):
        texts.append(value)


def metric_map(result):
    return {item["field"]: item for item in result["analyzed_metrics"]}


before_xlsx = file_hash(DATASET_FILE)
official_results = []
for device_id in ("MM-01", "GB-01", "CP-01"):
    page = get_device_records(device_id, limit=100)
    chosen = page["records"][80]
    result = detect_anomaly(chosen["record_id"])
    official_results.append(result)
    item = metric_map(result)["X向振动(mm/s)"]
    for name in result["anomalous_metrics"]:
        found = metric_map(result)[name]
        print("A-metric", device_id, name, found["current_value"], found["median"], found["mad"], found["robust_z"], found["unit"])
    print(
        "A",
        device_id,
        result["record_id"],
        result["observation_time"],
        result["detection_status"],
        "history",
        result["history_sample_count"],
        "X",
        item["current_value"],
        "median",
        item["median"],
        "mad",
        item["mad"],
        "z",
        item["robust_z"],
        item["metric_status"],
        "anomalous",
        result["anomalous_metrics"],
    )
    check(
        "A 真实记录可检测 " + device_id,
        result["official_record"] is True
        and result["device_id"] == device_id
        and result["history_sample_count"] >= 30
        and result["detection_status"] in {"偏离基线", "未发现显著偏离", "基线不足", "无法判定"}
        and item["median"] is not None,
        result["detection_status"],
    )

late = last_record("MM-01")
late_result = detect_anomaly(late["record_id"])
manual_earlier = earlier_records("MM-01", late["collected_at"])
print(
    "C",
    late["record_id"],
    late["collected_at"],
    "module",
    late_result["history_sample_count"],
    "manual",
    len(manual_earlier),
    "end",
    late_result["history_time_end"],
)
check(
    "C 基线只含本设备当前时刻以前的记录",
    late_result["history_sample_count"] == len(manual_earlier)
    and late_result["history_time_end"] < late["collected_at"]
    and all(row["device_id"] == "MM-01" and row["collected_at"] < late["collected_at"] for row in manual_earlier)
    and late["record_id"] not in {row["record_id"] for row in manual_earlier},
)

first = get_device_records("MM-01", limit=1)["records"][0]
early = detect_anomaly(first["record_id"])
print("B", first["record_id"], early["detection_status"], early["history_sample_count"], early["anomalous_metrics"])
check(
    "B 最早记录基线不足",
    early["detection_status"] == "基线不足" and early["history_sample_count"] == 0 and early["anomalous_metrics"] == [],
)

duplicate_page = get_device_records("FF-01", limit=100)
duplicate = next(record for record in duplicate_page["records"] if record["duplicate_timestamp"])
duplicate_result = detect_anomaly(duplicate["record_id"])
same_time = get_device_records("FF-01", start_time=duplicate["collected_at"], end_time=duplicate["collected_at"], limit=100)
print(
    "D",
    duplicate["collected_at"],
    "excluded",
    duplicate_result["same_timestamp_excluded_count"],
    "group",
    len(same_time["records"]),
    "history_end",
    duplicate_result["history_time_end"],
)
check(
    "D 同一时刻的其他记录没有进入基线",
    duplicate_result["same_timestamp_excluded_count"] == len(same_time["records"]) - 1
    and (duplicate_result["history_time_end"] is None or duplicate_result["history_time_end"] < duplicate["collected_at"])
    and duplicate_result["history_sample_count"] == len(earlier_records("FF-01", duplicate["collected_at"])),
)

payloads = official_results + [late_result, early, duplicate_result]
keys = set()
texts = []
for payload in payloads:
    walk(payload, keys, texts)
blob = json.dumps(payloads, ensure_ascii=False)
check("E 结果没有标签字段", LABEL_NAMES.isdisjoint(keys) and "故障类型" not in blob and "根因分析" not in blob and "频谱描述" not in blob)

speed = {"转速(rpm)": 1500, "转频(Hz)": 25}
stable_history = []
for index in range(40):
    stable_history.append({**speed, "X向振动(mm/s)": 1 + ((index % 5) - 2) * 0.01, "电机电流(A)": 100 + (index % 3) * 0.2})
stable = detect_test_series({**speed, "X向振动(mm/s)": 1.0, "电机电流(A)": 100.2}, stable_history)
shifted = detect_test_series({**speed, "X向振动(mm/s)": 30, "电机电流(A)": 100.2}, stable_history)
print("F", stable["data_source"], stable["detection_status"], metric_map(stable)["X向振动(mm/s)"]["robust_z"])
print("F-shift", shifted["detection_status"], metric_map(shifted)["X向振动(mm/s)"]["robust_z"], shifted["anomalous_metrics"])
check(
    "F 稳定测试序列不报警",
    stable["data_source"] == "算法测试序列"
    and stable["official_record"] is False
    and stable["detection_status"] == "未发现显著偏离"
    and "X向振动(mm/s)" not in stable["anomalous_metrics"],
)
check(
    "F 明显偏离的测试序列被标出",
    shifted["data_source"] == "算法测试序列"
    and shifted["detection_status"] == "偏离基线"
    and "X向振动(mm/s)" in shifted["anomalous_metrics"]
    and abs(metric_map(shifted)["X向振动(mm/s)"]["robust_z"]) > 3.5,
)

wide_speed = [{"转速(rpm)": 1000 + index * 30, "X向振动(mm/s)": 1.0} for index in range(40)]
wide = detect_test_series({"转速(rpm)": 1600, "X向振动(mm/s)": 80}, wide_speed)
check(
    "工况跨度大时不把振动偏离当成结论",
    wide["operating_condition"]["comparable"] is False and wide["detection_status"] == "无法判定" and wide["anomalous_metrics"] == [],
    wide["detection_status"],
)

zeros = detect_test_series({"转速(rpm)": 0, "X向振动(mm/s)": 0}, [{"转速(rpm)": 0, "X向振动(mm/s)": 0} for _ in range(40)])
constants = detect_test_series({"转速(rpm)": 1500, "X向振动(mm/s)": 2}, [{"转速(rpm)": 1500, "X向振动(mm/s)": 2} for _ in range(40)])
missing = detect_test_series({"转速(rpm)": 1500, "X向振动(mm/s)": None}, stable_history)
unknown = detect_anomaly("REC-DOES-NOT-EXIST")
print("G", zeros["detection_status"], constants["detection_status"], missing["detection_status"], unknown["detection_status"])
check(
    "G 全零、常数、缺失和非法编号都安全返回",
    zeros["detection_status"] == "无法判定"
    and zeros["anomalous_metrics"] == []
    and constants["detection_status"] == "无法判定"
    and constants["anomalous_metrics"] == []
    and metric_map(missing)["X向振动(mm/s)"]["metric_status"] == "无法判定"
    and unknown["detection_status"] == "无法判定"
    and unknown["analyzed_metrics"] == []
    and unknown["anomalous_metrics"] == [],
)
rms = metric_map(official_results[0])["振动均方根"]
check("振动均方根不填写 mm/s", rms["unit"] is None and "mm/s" not in rms["note"])

after_xlsx = file_hash(DATASET_FILE)
check("官方 Excel 哈希不变", before_xlsx == after_xlsx == XLSX_HASH)
check("official_dataset.py 未被修改", file_hash(ROOT / "backend/app/services/official_dataset.py") == DATASET_HASH)

completed = subprocess.run(
    [sys.executable, str(ROOT / "scripts" / "test_official_dataset.py")],
    cwd=ROOT / "backend",
    env={**dict(**{key: value for key, value in __import__("os").environ.items()}), "PYTHONPATH": str(ROOT / "backend"), "PYTHONIOENCODING": "utf-8"},
    capture_output=True,
    text=True,
    encoding="utf-8",
)
print(completed.stdout)
if completed.returncode != 0:
    print(completed.stderr)
check("H 原有 22 项数据读取测试通过", completed.returncode == 0 and "SUMMARY 22 / 22" in completed.stdout)

observed = get_observation(official_results[0]["record_id"])["record"]
recomputed = detect_anomaly(observed["record_id"])
check("重复调用不改写观测", recomputed["record_id"] == official_results[0]["record_id"] and observed["values"]["X向振动(mm/s)"] == get_observation(observed["record_id"])["record"]["values"]["X向振动(mm/s)"])

print("SUMMARY", sum(1 for item in CHECKS if item), "/", len(CHECKS))
raise SystemExit(0 if all(CHECKS) else 1)
