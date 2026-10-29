"""只读检查官方开发集查询。标签只在本脚本中读取，用于确认查询结果没有泄漏。"""

import hashlib
import json
import py_compile
from xml.etree import ElementTree
from zipfile import ZipFile

from app.services.official_dataset import (
    DATASET_FILE,
    MEASUREMENT_FIELDS,
    DatasetError,
    _WITHHELD_FIELDS,
    get_dataset_summary,
    get_device_records,
    get_observation,
    list_devices,
)

ROOT = DATASET_FILE.parents[2]
CHECKS = []
EXPECTED_XLSX = "1e9caeba1fe526f3ad1b5dd6445a9dbcc02fa22ec02273d9863ebc84e3e30f87"
PROTECTED = {
    "backend/app/main.py": "203478eeef6a51d27aec9c035afe846da41b3f3e1788434cf3d5f0eec62275a5",
    "backend/app/services/deepseek.py": "767b4bff9b1b36b095eb616117fde9e7a64bd30a9dfde78b71889d0830a64c0a",
    "backend/app/services/agent_tools.py": "50ca60a3a5206e4bc127cefd6329e8b425cebf378254c63c38c20d95f9e9d2ae",
    "backend/app/services/vibration_analysis.py": "07ed9d9508e99890d17571695a65f65e92c87b74205a58fbe2296b22b0a2a8b5",
    "backend/app/services/vibration_csv.py": "5aa297884fe8a75cf25b79756ee3c86c15098eca443845fce7fbbe60d9cdc02f",
    "backend/app/services/knowledge_search.py": "9f104ab11995765e6f5b3ff23d37f44cbc7831ec3babbe635646a1006c97e983",
    "backend/app/services/knowledge_graph.py": "cdf4a2b38debca5d433e5e8a8fd69234e7b1e651a4657369152bcb8da4cf7475",
}
TEXT_LABELS = ("设备状态", "故障类型", "故障位置", "频谱描述", "根因分析", "建议措施", "优先级", "影响程度")


def check(name, ok, detail=""):
    CHECKS.append(ok)
    print(("PASS" if ok else "FAIL"), name, detail)


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def walk_keys(value, found):
    if isinstance(value, dict):
        found.update(value)
        for item in value.values():
            walk_keys(item, found)
    elif isinstance(value, list):
        for item in value:
            walk_keys(item, found)


def collect_label_texts():
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    texts = set()
    with ZipFile(DATASET_FILE) as workbook:
        for name in workbook.namelist():
            if not name.startswith("xl/worksheets/sheet"):
                continue
            header = None
            indexes = {}
            for _, element in ElementTree.iterparse(workbook.open(name), events=("end",)):
                if element.tag != f"{{{ns}}}row":
                    continue
                row = {}
                for cell in element.findall(f"{{{ns}}}c"):
                    letters = "".join(char for char in (cell.get("r") or "A") if char.isalpha())
                    number = 0
                    for char in letters:
                        number = number * 26 + ord(char) - 64
                    if cell.get("t") == "inlineStr":
                        raw = "".join(node.text or "" for node in cell.iter(f"{{{ns}}}t"))
                    else:
                        value = cell.find(f"{{{ns}}}v")
                        raw = "" if value is None or value.text is None else value.text
                    row[number - 1] = raw.strip()
                element.clear()
                if header is None:
                    header = [row.get(index, "") for index in range(max(row) + 1)]
                    indexes = {name: index for index, name in enumerate(header) if name in TEXT_LABELS}
                    continue
                for index in indexes.values():
                    text = row.get(index, "")
                    if len(text) >= 2 and text != "-":
                        texts.add(text)
    return texts


before = file_hash(DATASET_FILE)
summary = get_dataset_summary()
devices = list_devices()
print("SUMMARY", summary["sheet_count"], summary["record_count"], summary["device_count"])
check("A 六张表共 24000 条", summary["sheet_count"] == 6 and summary["record_count"] == 24000 and len(summary["sheets"]) == 6)
check(
    "A 齿轮箱表名与类别不同",
    any(
        sheet["sheet_name"] == "主减速机_齿轮箱"
        and sheet["equipment_category"] == "主减速机/齿轮箱"
        and sheet["sheet_name_matches_category"] is False
        for sheet in summary["sheets"]
    ),
)
check("B 设备清单数量", devices["device_count"] == 46 and len(devices["devices"]) == 46)

samples = {}
for device_id, category in (("MM-01", "主轧机电机"), ("GB-01", "主减速机/齿轮箱"), ("CP-01", "冷却水循环泵"), ("FF-01", "加热炉助燃风机"), ("DP-01", "高压除鳞泵"), ("CC-01", "连铸机扇形段驱动电机")):
    page = get_device_records(device_id, limit=100)
    samples[device_id] = page
    print(device_id, page["found"], page["equipment_category"], page["matched_count"], page["returned_count"], page["duplicate_timestamp_groups"])
    check(
        "D 可查询 " + device_id,
        page["found"] is True and page["equipment_category"] == category and page["matched_count"] > 100 and page["returned_count"] == 100 and page["truncated"] is True,
        f"matched={page['matched_count']} category={page['equipment_category']}",
    )

observation = get_observation(samples["MM-01"]["records"][0]["record_id"])
check(
    "B 记录编号可回查",
    observation["found"] is True and observation["record"]["record_id"] == samples["MM-01"]["records"][0]["record_id"] and observation["record"]["excel_row"] >= 2,
)

ordered = samples["MM-01"]["records"]
order_ok = all(
    (left["collected_at"], left["excel_row"], left["record_id"]) <= (right["collected_at"], right["excel_row"], right["record_id"])
    for left, right in zip(ordered, ordered[1:])
)
check("C 同设备按时间排序", order_ok)

fan = samples["FF-01"]
marked = next(record for record in fan["records"] if record["duplicate_timestamp"])
same_time = get_device_records("FF-01", start_time=marked["collected_at"], end_time=marked["collected_at"], limit=100)
same_ids = [record["record_id"] for record in same_time["records"]]
same_rows = [record["excel_row"] for record in same_time["records"]]
print("DUPLICATE", marked["collected_at"], same_time["returned_count"], same_time["records"][0]["same_timestamp_count"])
check(
    "C 相同时间的记录全部保留",
    same_time["found"] is True
    and same_time["returned_count"] >= 2
    and same_time["returned_count"] == same_time["records"][0]["same_timestamp_count"]
    and len(same_ids) == len(set(same_ids))
    and same_rows == sorted(same_rows)
    and all(record["collected_at"] == marked["collected_at"] and record["duplicate_timestamp"] is True for record in same_time["records"]),
    f"count={same_time['returned_count']}",
)

missing_device = get_device_records("NO-SUCH-DEVICE")
missing_record = get_observation("REC-DOES-NOT-EXIST")
check("F 不存在的设备返回空结果", missing_device["found"] is False and missing_device["records"] == [] and missing_device["matched_count"] == 0)
check("F 不存在的记录返回空结果", missing_record["found"] is False and missing_record["record"] is None)

changed = dict(observation["record"])
changed["values"] = dict(changed["values"])
changed["values"]["转速(rpm)"] = -1
again = get_observation(observation["record"]["record_id"])
check("缓存不被调用方修改", again["record"]["values"]["转速(rpm)"] != -1)

payloads = [summary, devices, *samples.values(), observation, same_time, missing_device, missing_record]
keys = set()
for payload in payloads:
    walk_keys(payload, keys)
blob = json.dumps(payloads, ensure_ascii=False)
label_texts = collect_label_texts()
long_leaks = [text for text in label_texts if len(text) >= 8 and text in blob]
exact_leaks = []
for payload in payloads:
    stack = [payload]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
        elif isinstance(current, str) and current in label_texts:
            exact_leaks.append(current)
value_keys = set()
for record in samples["MM-01"]["records"]:
    value_keys.update(record["values"])
check("E 查询结果没有标签字段", _WITHHELD_FIELDS.isdisjoint(keys) and "数据集划分" not in keys)
check("E 查询结果没有标签原文", not long_leaks and not exact_leaks, f"long={len(long_leaks)} exact={len(exact_leaks)}")
check("E 数值字段只来自白名单", value_keys == set(MEASUREMENT_FIELDS))
finite_frequency = samples["CP-01"]["records"][0]["values"]["BPFO频率(Hz)"]
check("长小数仍作为有限数值", isinstance(finite_frequency, float) and abs(finite_frequency - 65.4) < 1e-6)

limit_error = False
try:
    get_device_records("MM-01", limit=101)
except DatasetError:
    limit_error = True
check("单次查询最多 100 条", limit_error and samples["MM-01"]["limit"] == 100)

after = file_hash(DATASET_FILE)
check("G Excel 哈希不变", before == after == EXPECTED_XLSX, before)
py_compile.compile(ROOT / "backend" / "app" / "services" / "official_dataset.py", doraise=True)
protected_ok = all(file_hash(ROOT / relative) == digest for relative, digest in PROTECTED.items())
check("H 编译通过且原有文件未改", protected_ok)

print("SUMMARY", sum(1 for item in CHECKS if item), "/", len(CHECKS))
raise SystemExit(0 if all(CHECKS) else 1)
