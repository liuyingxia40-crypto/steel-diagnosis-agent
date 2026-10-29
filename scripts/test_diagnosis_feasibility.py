"""验收官方数据故障识别可行性审计。不训练模型，不调用 DeepSeek。"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
SCRIPTS = ROOT / "scripts"
for path in (BACKEND, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import audit_official_dataset_diagnosis as audit  # noqa: E402
from app.services import official_dataset  # noqa: E402
from app.services.official_dataset import (  # noqa: E402
    MEASUREMENT_FIELDS,
    _WITHHELD_FIELDS,
    get_dataset_summary,
    get_device_records,
)

CHECKS = []
EXPECTED_HASH = audit.EXPECTED_HASH
PROTECTED = (
    ROOT / "backend" / "app" / "services" / "official_dataset.py",
    ROOT / "backend" / "app" / "services" / "anomaly_detection.py",
    ROOT / "backend" / "app" / "services" / "diagnostic_bridge.py",
    ROOT / "backend" / "app" / "main.py",
    ROOT / "backend" / "app" / "services" / "agent_tools.py",
    ROOT / "backend" / "app" / "services" / "deepseek.py",
    audit.DATASET_FILE,
)
BANNED_IMPORTS = {
    "deepseek",
    "agent_tools",
    "diagnostic_bridge",
    "anomaly_detection",
    "fastapi",
    "requests",
    "urllib",
    "openai",
}


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append(ok)
    print(("PASS" if ok else "FAIL"), name, detail)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
            names.update(part.split(".")[-1] for part in node.module.split("."))
    return names


def independent_fault_counts() -> Counter:
    """用另一段表头定位代码重数故障类型，避免只相信审计脚本自己的汇总。"""
    counts: Counter = Counter()
    rows = 0
    with ZipFile(audit.DATASET_FILE) as workbook:
        for _sheet_name, target in audit._sheet_targets(workbook):
            header = None
            fault_index = None
            with workbook.open(target) as handle:
                for _, element in ElementTree.iterparse(handle, events=("end",)):
                    if element.tag != audit._ROW:
                        continue
                    cells = {}
                    for cell in element.findall(audit._CELL):
                        cells[audit._column_index(cell.get("r") or "A1")] = audit._cell_text(cell)
                    element.clear()
                    if header is None:
                        width = max(cells) + 1 if cells else 0
                        header = [str(cells.get(index) or "").strip() for index in range(width)]
                        fault_index = header.index("故障类型")
                        continue
                    rows += 1
                    raw = str(cells.get(fault_index) or "").strip()
                    counts[raw if raw else audit.EMPTY_TOKEN] += 1
    counts["__rows__"] = rows
    return counts


def main() -> None:
    before = {path: file_hash(path) for path in PROTECTED}
    check("业务缓存在审计前为空", official_dataset._CACHE is None)
    banned = imported_modules(ROOT / "scripts" / "audit_official_dataset_diagnosis.py") & BANNED_IMPORTS
    check("审计脚本没有导入 DeepSeek 或业务诊断模块", not banned, str(sorted(banned)))
    audit.main()
    after = {path: file_hash(path) for path in PROTECTED}
    check("业务代码和原始 Excel 未被修改", before == after)
    check("Excel SHA-256 保持不变", after[audit.DATASET_FILE] == EXPECTED_HASH)
    check("审计后业务缓存仍为空", official_dataset._CACHE is None)

    summary = json.loads(audit.SUMMARY_PATH.read_text(encoding="utf-8"))
    report = audit.REPORT_PATH.read_text(encoding="utf-8")
    check("报告文件已生成", audit.REPORT_PATH.is_file() and audit.SUMMARY_PATH.is_file())
    check("规模为 6 表、46 台设备、24000 条", summary["source"]["sheet_count"] == 6 and summary["source"]["device_count"] == 46 and summary["source"]["record_count"] == 24000)
    check("没有训练模型且没有调用 DeepSeek", summary["model_trained"] is False and summary["deepseek_called"] is False)
    check("输入特征就是观测白名单", summary["input_features"] == list(MEASUREMENT_FIELDS))
    check("输入特征不含 10 个结果标签", not (set(summary["input_features"]) & set(_WITHHELD_FIELDS)))
    check("数据集划分不是输入特征", "数据集划分" not in summary["input_features"])
    fault_total = sum(item["count"] for item in summary["fault_types"])
    check("故障类型计数之和为 24000", fault_total == 24000, str(fault_total))
    check("每类故障都有设备", all(item["device_count"] > 0 for item in summary["fault_types"]))
    check("报告和摘要不含逐条记录编号", "REC-" not in report and "REC-" not in json.dumps(summary, ensure_ascii=False))
    spectrum = next(item for item in summary["label_fields"] if item["field"] == "频谱描述")
    check("摘要没有保存频谱描述原文", "value_counts" not in spectrum and "text" not in spectrum)
    for letter in "ABCDEFG":
        check(f"结论 {letter} 已写入", bool(f"### {letter}." in report and summary["conclusions"][letter]))

    recounted = independent_fault_counts()
    reported = {item["name"]: item["count"] for item in summary["fault_types"]}
    check("独立重数的记录数一致", recounted.pop("__rows__") == 24000)
    check("独立重数的故障类型数量一致", recounted == Counter(reported), str(dict(recounted)))
    check("识别了重复时间和冲突样本", summary["duplicates"]["same_device_same_time_groups"] > 0 and summary["duplicates"]["groups_with_measurement_conflict"] > 0 and summary["duplicates"]["groups_with_fault_type_conflict"] > 0)

    dataset = get_dataset_summary()
    check("业务读取仍为 6 表、46 台设备、24000 条", dataset["sheet_count"] == 6 and dataset["device_count"] == 46 and dataset["record_count"] == 24000)
    same_sheets = [(item["sheet_name"], item["record_count"], item["device_count"]) for item in dataset["sheets"]] == [
        (item["sheet_name"], item["record_count"], item["device_count"]) for item in summary["source"]["sheets"]
    ]
    check("审计与业务读取的工作表规模一致", same_sheets)
    public = get_device_records("MM-01", limit=1)
    public_text = json.dumps(public, ensure_ascii=False)
    check("业务查询结果不含结果标签", all(label not in public_text for label in _WITHHELD_FIELDS))
    check("业务查询结果不含频谱描述", "频谱描述" not in public_text)

    print(f"{sum(CHECKS)}/{len(CHECKS)} passed")
    if not all(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
