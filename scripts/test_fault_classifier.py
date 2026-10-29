"""验收官方数值故障分类基线。"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
SCRIPTS = ROOT / "scripts"
for path in (BACKEND, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import evaluate_fault_classifier as baseline  # noqa: E402
from audit_official_dataset_diagnosis import SUMMARY_PATH as AUDIT_SUMMARY  # noqa: E402

CHECKS = []
BANNED_IMPORTS = {"deepseek", "agent_tools", "diagnostic_bridge", "anomaly_detection", "fastapi", "requests", "openai"}
LABELS = {
    "设备状态",
    "故障类型",
    "故障严重度",
    "故障位置",
    "诊断置信度",
    "频谱描述",
    "根因分析",
    "建议措施",
    "优先级",
    "影响程度",
}


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append(bool(ok))
    print(("PASS" if ok else "FAIL"), name, detail)


def file_hash(path: Path) -> str:
    import hashlib

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
            names.update(part for part in node.module.split("."))
    return names


def synthetic_tree() -> None:
    rows = [baseline.Sample((0.0,), 0, "d", "s", "c", "t0", None) for _index in range(5)]
    rows += [baseline.Sample((1.0,), 1, "d", "s", "c", "t1", None) for _index in range(5)]
    tree = baseline.fit_tree(rows, ("x",), 3)
    check("合成数据树深不超过 3", baseline.tree_depth(tree) <= 3)
    check("合成数据低值预测为正常序号", baseline.predict_tree(tree, (0.0,)) == 0)
    check("合成数据高值预测为第二类", baseline.predict_tree(tree, (1.0,)) == 1)
    check("多数类并列时取更靠前的类别", baseline.majority_index([4, 4, 1]) == 0)
    check("多数类取频数更高者", baseline.majority_index([1, 9, 3]) == 1)


def matrix_ok(result: dict, test_records: int) -> bool:
    matrix = result["confusion_matrix"]
    if len(matrix) != 5 or any(len(row) != 5 for row in matrix):
        return False
    if sum(sum(row) for row in matrix) != test_records:
        return False
    metrics = result["metrics"]
    required = ("accuracy", "macro_precision", "macro_recall", "macro_f1", "balanced_accuracy", "per_class")
    return all(key in metrics for key in required) and len(metrics["per_class"]) == 5


def main() -> None:
    synthetic_tree()
    source = SCRIPTS / "evaluate_fault_classifier.py"
    banned = imported_modules(source) & BANNED_IMPORTS
    check("评估脚本没有导入 DeepSeek 或业务诊断模块", not banned, str(sorted(banned)))
    protected = [path for path in baseline.PROTECTED if path.is_file()]
    if baseline.SQLITE_PATH.is_file():
        protected.append(baseline.SQLITE_PATH)
    before = {path: file_hash(path) for path in protected}
    summary = baseline.main()
    after = {path: file_hash(path) for path in protected}
    check("原始 Excel、业务代码和 SQLite 未被修改", before == after)
    check("Excel SHA-256 保持不变", summary["excel_sha256"] == baseline.EXPECTED_HASH)
    report = baseline.REPORT_PATH.read_text(encoding="utf-8")
    raw = baseline.SUMMARY_PATH.read_text(encoding="utf-8")
    check("Markdown 和 JSON 已生成", baseline.REPORT_PATH.is_file() and baseline.SUMMARY_PATH.is_file())
    check("输出不含逐条记录编号", "REC-" not in report and "REC-" not in raw)
    check("记录数为 24000 且有 5 类", summary["record_count"] == 24000 and summary["class_names"] == list(baseline.CLASS_NAMES))
    check("输入特征不含答案标签", not (set(summary["input_features"]) & LABELS))
    check("输入特征不含被排除的频率、转速和最大振动", not (set(summary["input_features"]) & set(baseline.EXCLUDED_MEASUREMENTS)))
    check("没有使用随机记录划分", summary["random_record_split_used"] is False)
    check("没有调用 DeepSeek", summary["deepseek_called"] is False)

    audit = json.loads(AUDIT_SUMMARY.read_text(encoding="utf-8"))
    check("冲突组数量与审计一致", summary["conflict_groups"] == audit["duplicates"]["groups_with_fault_type_conflict"])
    check("冲突记录数量与审计一致", summary["conflict_records"] == audit["duplicates"]["records_in_fault_type_conflict_groups"])
    within_audit = audit["splits"]["within_device_time_80_20"]
    holdout_audit = audit["splits"]["device_holdout_by_category"]
    within = summary["schemes"]["within_device_time"]["primary"]["split"]
    holdout = summary["schemes"]["device_holdout"]["primary"]["split"]
    check(
        "同设备时间划分样本数与审计一致",
        within["train_records"] == within_audit["train_records"] and within["test_records"] == within_audit["test_records"],
        f"{within['train_records']}/{within['test_records']}",
    )
    check(
        "未见设备划分样本数与审计一致",
        holdout["train_records"] == holdout_audit["train_records"] and holdout["test_records"] == holdout_audit["test_records"],
        f"{holdout['train_records']}/{holdout['test_records']}",
    )
    held_ids = [item["device_id"] for item in summary["schemes"]["device_holdout"]["primary"]["held_out_devices"]]
    audit_ids = [item["device_id"] for item in holdout_audit["held_out_devices"]]
    check("留出设备与审计方案一致", held_ids == audit_ids, ",".join(held_ids))
    check("同设备时间划分没有时间倒流", within["test_records_not_strictly_after_same_device_train"] == 0)
    check("留出设备没有设备交叉", holdout["devices_in_both"] == 0)
    check(
        "两种划分都没有拆开同时间组",
        within["same_time_groups_crossing_split"] == 0 and holdout["same_time_groups_crossing_split"] == 0,
    )

    for scheme_name, scheme in summary["schemes"].items():
        for stage_name in ("primary", "without_conflict_groups"):
            stage = scheme[stage_name]
            check(f"{scheme_name} {stage_name} 多数类指标完整", matrix_ok(stage["majority"], stage["split"]["test_records"]))
            check(f"{scheme_name} {stage_name} 决策树指标完整", matrix_ok(stage["decision_tree"], stage["split"]["test_records"]))
            check(f"{scheme_name} {stage_name} 树深不超过 3", stage["decision_tree"]["tree"]["actual_depth"] <= 3)
            used = set(stage["decision_tree"]["tree"]["features_used"])
            check(f"{scheme_name} {stage_name} 树只使用允许的特征", used <= set(summary["input_features"]))
            removed_ok = True
            if stage_name == "without_conflict_groups":
                removed = stage["removed_train_records"] + stage["removed_test_records"]
                groups = stage["conflict_groups_removed_from_train"] + stage["conflict_groups_removed_from_test"]
                removed_ok = removed == summary["conflict_records"] and groups == summary["conflict_groups"]
            check(f"{scheme_name} {stage_name} 冲突样本处理正确", removed_ok)

    print(f"{sum(CHECKS)}/{len(CHECKS)} passed")
    if not all(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
