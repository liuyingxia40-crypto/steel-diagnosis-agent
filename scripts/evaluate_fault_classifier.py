"""官方数值故障分类基线。只在本脚本中训练和评估，不接入业务接口。

当前环境没有 scikit-learn。决策树用标准库按基尼系数实现，最大深度 3。
测试集标签不参与训练、阈值选择或调参。
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
SCRIPTS = ROOT / "scripts"
for path in (BACKEND, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from audit_official_dataset_diagnosis import (  # noqa: E402
    DATASET_FILE,
    DERIVED_FIELDS,
    EXPECTED_HASH,
    FREQUENCY_FIELDS,
    LABEL_FIELDS,
    MEASUREMENT_FIELDS,
    SPLIT_FIELD,
    file_sha256,
    load_rows,
)

OUT_DIR = DATASET_FILE.parents[1] / "processed" / "evaluation"
REPORT_PATH = OUT_DIR / "fault_classification_baseline.md"
SUMMARY_PATH = OUT_DIR / "fault_classification_baseline.json"
CLASS_NAMES = ("正常", "轴承磨损", "转子不平衡", "润滑故障", "齿轮异常")
EXCLUDED_MEASUREMENTS = ("转速(rpm)", "转频(Hz)", DERIVED_FIELDS[0], *FREQUENCY_FIELDS)
MODEL_FEATURES = tuple(field for field in MEASUREMENT_FIELDS if field not in EXCLUDED_MEASUREMENTS)
MAX_DEPTH = 3
GAIN_GAP = 1e-15
VISIBLE_DELTA = 0.02
DEVICE_DROP = 0.05
SQLITE_PATH = DATASET_FILE.parents[1] / "runtime" / "monitoring_events.sqlite3"
PROTECTED = (
    ROOT / "backend" / "app" / "services" / "official_dataset.py",
    ROOT / "backend" / "app" / "services" / "anomaly_detection.py",
    ROOT / "backend" / "app" / "services" / "diagnostic_bridge.py",
    ROOT / "backend" / "app" / "services" / "agent_tools.py",
    ROOT / "backend" / "app" / "services" / "deepseek.py",
    ROOT / "backend" / "app" / "main.py",
    DATASET_FILE,
)


class ExperimentError(Exception):
    pass


class Sample:
    __slots__ = ("values", "label", "device_id", "sheet_name", "category", "collected_at", "collected_dt")

    def __init__(self, values, label, device_id, sheet_name, category, collected_at, collected_dt):
        self.values = values
        self.label = label
        self.device_id = device_id
        self.sheet_name = sheet_name
        self.category = category
        self.collected_at = collected_at
        self.collected_dt = collected_dt


class TreeNode:
    __slots__ = ("feature_index", "threshold", "left", "right", "prediction", "train_count", "class_counts")

    def __init__(self, prediction: int, train_count: int, class_counts: list[int]):
        self.feature_index = None
        self.threshold = None
        self.left = None
        self.right = None
        self.prediction = prediction
        self.train_count = train_count
        self.class_counts = class_counts


def majority_index(counts: list[int]) -> int:
    """频数更高者胜出；频数相同则取固定类别顺序中更靠前的类别。"""
    best = 0
    for index in range(1, len(counts)):
        if counts[index] > counts[best]:
            best = index
    return best


def gini_from_counts(counts: list[int], total: int) -> float:
    if total <= 0:
        return 0.0
    inverse = 1.0 / total
    score = 1.0
    for count in counts:
        if count:
            proportion = count * inverse
            score -= proportion * proportion
    return score


def class_counts(labels: list[int], n_classes: int) -> list[int]:
    counts = [0] * n_classes
    for label in labels:
        counts[label] += 1
    return counts


def count_map(samples: list[Sample]) -> dict[str, int]:
    counts = Counter(CLASS_NAMES[sample.label] for sample in samples)
    return {name: counts[name] for name in CLASS_NAMES}


def best_split(rows: list[tuple[tuple[float, ...], int]], parent_counts: list[int]):
    total = len(rows)
    parent_gini = gini_from_counts(parent_counts, total)
    best_weighted = parent_gini
    best = None
    n_classes = len(parent_counts)
    feature_count = len(rows[0][0]) if rows else 0
    for feature_index in range(feature_count):
        order = sorted(rows, key=lambda item: item[0][feature_index])
        left = [0] * n_classes
        right = parent_counts[:]
        index = 0
        while index < total:
            value = order[index][0][feature_index]
            label = order[index][1]
            left[label] += 1
            right[label] -= 1
            cursor = index + 1
            while cursor < total and order[cursor][0][feature_index] == value:
                label = order[cursor][1]
                left[label] += 1
                right[label] -= 1
                cursor += 1
            if cursor == total:
                break
            left_n = cursor
            right_n = total - cursor
            weighted = (left_n * gini_from_counts(left, left_n) + right_n * gini_from_counts(right, right_n)) / total
            if weighted < best_weighted - GAIN_GAP:
                best_weighted = weighted
                best = (feature_index, (value + order[cursor][0][feature_index]) / 2.0)
            index = cursor
    return best


def fit_tree(samples: list[Sample], feature_names: tuple[str, ...], max_depth: int = MAX_DEPTH) -> TreeNode:
    """只读取训练样本的数值和标签。"""
    rows = [(sample.values, sample.label) for sample in samples]
    n_classes = len(CLASS_NAMES)

    def build(subset: list[tuple[tuple[float, ...], int]], depth: int) -> TreeNode:
        counts = class_counts([label for _values, label in subset], n_classes)
        node = TreeNode(majority_index(counts), len(subset), counts)
        if depth >= max_depth or len(subset) < 2 or sum(count > 0 for count in counts) < 2:
            return node
        chosen = best_split(subset, counts)
        if chosen is None:
            return node
        feature_index, threshold = chosen
        left_rows = []
        right_rows = []
        for values, label in subset:
            if values[feature_index] <= threshold:
                left_rows.append((values, label))
            else:
                right_rows.append((values, label))
        if not left_rows or not right_rows:
            return node
        node.feature_index = feature_index
        node.threshold = threshold
        node.left = build(left_rows, depth + 1)
        node.right = build(right_rows, depth + 1)
        return node

    if len(feature_names) != (len(rows[0][0]) if rows else 0):
        raise ExperimentError("特征名称数量与数值向量不一致。")
    return build(rows, 0)


def predict_tree(node: TreeNode, values: tuple[float, ...]) -> int:
    while node.feature_index is not None:
        if values[node.feature_index] <= node.threshold:
            node = node.left
        else:
            node = node.right
    return node.prediction


def tree_depth(node: TreeNode) -> int:
    if node.feature_index is None:
        return 0
    return 1 + max(tree_depth(node.left), tree_depth(node.right))


def leaf_count(node: TreeNode) -> int:
    if node.feature_index is None:
        return 1
    return leaf_count(node.left) + leaf_count(node.right)


def export_tree(node: TreeNode, feature_names: tuple[str, ...]) -> dict:
    splits = []
    leaves = []
    used = []

    def walk(current: TreeNode, path: str) -> None:
        if current.feature_index is None:
            leaves.append(
                {
                    "path": path,
                    "prediction": CLASS_NAMES[current.prediction],
                    "train_count": current.train_count,
                    "train_class_counts": {CLASS_NAMES[index]: count for index, count in enumerate(current.class_counts)},
                }
            )
            return
        feature_name = feature_names[current.feature_index]
        if feature_name not in used:
            used.append(feature_name)
        splits.append(
            {
                "path": path,
                "field": feature_name,
                "threshold": current.threshold,
                "left_rule": "小于等于阈值",
                "right_rule": "大于阈值",
            }
        )
        walk(current.left, path + " ≤")
        walk(current.right, path + " >")

    walk(node, "根")
    return {
        "max_depth_limit": MAX_DEPTH,
        "actual_depth": tree_depth(node),
        "leaf_count": leaf_count(node),
        "training_rows_seen": node.train_count,
        "features_used": used,
        "splits": splits,
        "leaves": leaves,
        "split_rule": "基尼系数加权和不纯度；并列时保留字段名单中更靠前、阈值更低的划分。不使用随机数。",
    }


def confusion_matrix(truth: list[int], predicted: list[int]) -> list[list[int]]:
    size = len(CLASS_NAMES)
    matrix = [[0 for _column in range(size)] for _row in range(size)]
    for actual, guess in zip(truth, predicted):
        matrix[actual][guess] += 1
    return matrix


def round_metric(value):
    if value is None:
        return None
    return round(float(value), 6)


def score_matrix(matrix: list[list[int]]) -> dict:
    total = sum(sum(row) for row in matrix)
    correct = sum(matrix[index][index] for index in range(len(CLASS_NAMES)))
    per_class = []
    for index, name in enumerate(CLASS_NAMES):
        tp = matrix[index][index]
        support = sum(matrix[index])
        predicted = sum(matrix[row][index] for row in range(len(CLASS_NAMES)))
        precision = tp / predicted if predicted else None
        recall = tp / support if support else None
        if precision is None or recall is None:
            f1 = None
        elif precision + recall == 0:
            f1 = 0.0
        else:
            f1 = 2 * precision * recall / (precision + recall)
        per_class.append(
            {
                "name": name,
                "support": support,
                "predicted": predicted,
                "tp": tp,
                "precision": round_metric(precision),
                "recall": round_metric(recall),
                "f1": round_metric(f1),
            }
        )

    def macro(key: str):
        values = [item[key] for item in per_class]
        if any(value is None for value in values):
            return None
        return round_metric(sum(values) / len(values))

    return {
        "accuracy": round_metric(correct / total) if total else None,
        "correct_count": correct,
        "macro_precision": macro("precision"),
        "macro_recall": macro("recall"),
        "macro_f1": macro("f1"),
        "balanced_accuracy": macro("recall"),
        "per_class": per_class,
        "undefined_precision": [item["name"] for item in per_class if item["precision"] is None],
        "undefined_recall": [item["name"] for item in per_class if item["recall"] is None],
        "undefined_f1": [item["name"] for item in per_class if item["f1"] is None],
    }


def evaluate_predictions(truth: list[int], predicted: list[int]) -> dict:
    matrix = confusion_matrix(truth, predicted)
    return {"confusion_matrix": matrix, "metrics": score_matrix(matrix)}


def fit_majority(samples: list[Sample]) -> int:
    counts = class_counts([sample.label for sample in samples], len(CLASS_NAMES))
    return majority_index(counts)


def load_samples() -> tuple[list[Sample], list[str]]:
    rows, sheet_order = load_rows()
    label_index = {name: index for index, name in enumerate(CLASS_NAMES)}
    samples = []
    for row in rows:
        if row["fault_name"] not in label_index:
            raise ExperimentError(f"出现白名单之外的故障类型：{row['fault_name']}")
        values = []
        for name in MODEL_FEATURES:
            raw = row["values"][name]
            if raw is None:
                raise ExperimentError(f"{name} 存在缺失。本次不使用测试集或训练集统计量插补。")
            values.append(float(raw))
        samples.append(
            Sample(
                tuple(values),
                label_index[row["fault_name"]],
                row["device_id"],
                row["sheet_name"],
                row["equipment_category"],
                row["collected_at"],
                row["collected_at_dt"],
            )
        )
    return samples, sheet_order


def conflict_keys(samples: list[Sample]) -> set[tuple[str, str]]:
    labels: dict[tuple[str, str], set[int]] = defaultdict(set)
    for sample in samples:
        labels[(sample.device_id, sample.collected_at)].add(sample.label)
    return {key for key, seen in labels.items() if len(seen) > 1}


def records_in_keys(samples: list[Sample], keys: set[tuple[str, str]]) -> int:
    return sum(1 for sample in samples if (sample.device_id, sample.collected_at) in keys)


def within_device_split(samples: list[Sample]) -> tuple[list[Sample], list[Sample], int]:
    grouped: dict[str, list[Sample]] = defaultdict(list)
    for sample in samples:
        grouped[sample.device_id].append(sample)
    train = []
    test = []
    not_split = 0
    for items in grouped.values():
        times = sorted({item.collected_dt for item in items if item.collected_dt is not None})
        if len(times) < 2:
            not_split += 1
            train.extend(items)
            continue
        test_count = min(max(1, round(len(times) * 0.2)), len(times) - 1)
        test_times = set(times[-test_count:])
        for item in items:
            if item.collected_dt in test_times:
                test.append(item)
            else:
                train.append(item)
    return train, test, not_split


def device_holdout_split(samples: list[Sample], sheet_order: list[str]) -> tuple[list[Sample], list[Sample], list[dict]]:
    held_ids = []
    category_of = {}
    for sample in samples:
        category_of.setdefault(sample.sheet_name, sample.category)
    for sheet_name in sheet_order:
        device_ids = sorted({sample.device_id for sample in samples if sample.sheet_name == sheet_name})
        hold_count = max(1, round(len(device_ids) * 0.2))
        held_ids.extend(device_ids[-hold_count:])
    held = set(held_ids)
    train = [sample for sample in samples if sample.device_id not in held]
    test = [sample for sample in samples if sample.device_id in held]
    grouped: dict[str, list[Sample]] = defaultdict(list)
    for sample in samples:
        grouped[sample.device_id].append(sample)
    held_rows = []
    for device_id in held_ids:
        items = grouped[device_id]
        counts = Counter(CLASS_NAMES[item.label] for item in items)
        held_rows.append(
            {
                "device_id": device_id,
                "equipment_category": category_of[items[0].sheet_name],
                "record_count": len(items),
                "fault_counts": {name: counts[name] for name in CLASS_NAMES if counts[name]},
            }
        )
    return train, test, held_rows


def split_facts(train: list[Sample], test: list[Sample]) -> dict:
    train_ids = {sample.device_id for sample in train}
    test_ids = {sample.device_id for sample in test}
    train_keys = {(sample.device_id, sample.collected_at) for sample in train}
    test_keys = {(sample.device_id, sample.collected_at) for sample in test}
    train_max = {}
    for sample in train:
        if sample.collected_dt is None:
            continue
        current = train_max.get(sample.device_id)
        if current is None or sample.collected_dt > current:
            train_max[sample.device_id] = sample.collected_dt
    reversals = 0
    for sample in test:
        latest = train_max.get(sample.device_id)
        if sample.collected_dt is not None and latest is not None and not sample.collected_dt > latest:
            reversals += 1
    return {
        "train_records": len(train),
        "test_records": len(test),
        "train_devices": len(train_ids),
        "test_devices": len(test_ids),
        "devices_in_both": len(train_ids & test_ids),
        "same_time_groups_crossing_split": len(train_keys & test_keys),
        "test_records_not_strictly_after_same_device_train": reversals,
        "train_class_counts": count_map(train),
        "test_class_counts": count_map(test),
    }


def run_models(train: list[Sample], test: list[Sample], feature_names: tuple[str, ...]) -> dict:
    majority = fit_majority(train)
    majority_pred = [majority] * len(test)
    majority_result = evaluate_predictions([sample.label for sample in test], majority_pred)
    majority_result["predicted_class"] = CLASS_NAMES[majority]
    majority_result["training_rows_seen"] = len(train)
    tree = fit_tree(train, feature_names, MAX_DEPTH)
    tree_pred = [predict_tree(tree, sample.values) for sample in test]
    tree_result = evaluate_predictions([sample.label for sample in test], tree_pred)
    tree_result["tree"] = export_tree(tree, feature_names)
    if tree_result["tree"]["actual_depth"] > MAX_DEPTH:
        raise ExperimentError("决策树深度超过 3。")
    if tree_result["tree"]["training_rows_seen"] != len(train):
        raise ExperimentError("决策树看到的训练行数与训练集不一致。")
    return {"majority": majority_result, "decision_tree": tree_result}


def evaluate_scheme(name: str, rule: str, train: list[Sample], test: list[Sample], extra: dict) -> dict:
    facts = split_facts(train, test)
    train_conflict = conflict_keys(train)
    test_conflict = conflict_keys(test)
    sensitive_train = [sample for sample in train if (sample.device_id, sample.collected_at) not in train_conflict]
    sensitive_test = [sample for sample in test if (sample.device_id, sample.collected_at) not in test_conflict]
    print(f"fit {name} primary train={len(train)} test={len(test)}", flush=True)
    primary = run_models(train, test, MODEL_FEATURES)
    print(f"fit {name} sensitivity train={len(sensitive_train)} test={len(sensitive_test)}", flush=True)
    sensitivity = run_models(sensitive_train, sensitive_test, MODEL_FEATURES)
    sensitive_facts = split_facts(sensitive_train, sensitive_test)
    return {
        "name": name,
        "rule": rule,
        "parameters_shared_with_other_scheme": False,
        "primary": {"split": facts, **extra, **primary},
        "without_conflict_groups": {
            "split": sensitive_facts,
            "removed_train_records": len(train) - len(sensitive_train),
            "removed_test_records": len(test) - len(sensitive_test),
            "conflict_groups_removed_from_train": len(train_conflict),
            "conflict_groups_removed_from_test": len(test_conflict),
            "note": "冲突组是否冲突只由该组自己的官方标签判断。训练侧的判断不读取测试侧标签。不挑选其中一个标签，也不改官方标签。",
            **sensitivity,
        },
    }


def metric_text(value) -> str:
    if value is None:
        return "无法计算"
    return f"{value:.4f}"


def names_text(names: list[str]) -> str:
    return "、".join(names) if names else "无"


def counts_text(counts: dict[str, int]) -> str:
    return "；".join(f"{name} {counts[name]}" for name in CLASS_NAMES)


def render_metrics(title: str, result: dict) -> list[str]:
    metrics = result["metrics"]
    lines = [
        f"### {title}",
        "",
        f"- 正确数：{metrics['correct_count']}",
        f"- Accuracy：{metric_text(metrics['accuracy'])}",
        f"- Macro Precision：{metric_text(metrics['macro_precision'])}",
        f"- Macro Recall：{metric_text(metrics['macro_recall'])}",
        f"- Macro F1：{metric_text(metrics['macro_f1'])}",
        f"- Balanced Accuracy：{metric_text(metrics['balanced_accuracy'])}",
        f"- 精确率无法计算的类别：{names_text(metrics['undefined_precision'])}",
        f"- 召回率无法计算的类别：{names_text(metrics['undefined_recall'])}",
        f"- F1 无法计算的类别：{names_text(metrics['undefined_f1'])}",
        "",
        "| 故障类型 | 测试样本 | 预测为该类 | Precision | Recall | F1 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in metrics["per_class"]:
        lines.append(
            f"| {item['name']} | {item['support']} | {item['predicted']} | {metric_text(item['precision'])} | {metric_text(item['recall'])} | {metric_text(item['f1'])} |"
        )
    header = "| 真实\\预测 | " + " | ".join(CLASS_NAMES) + " |"
    split = "| --- | " + " | ".join("---:" for _name in CLASS_NAMES) + " |"
    body = []
    for index, name in enumerate(CLASS_NAMES):
        body.append("| " + name + " | " + " | ".join(str(value) for value in result["confusion_matrix"][index]) + " |")
    lines.extend(["", header, split, *body, ""])
    return lines


def render_tree(result: dict) -> list[str]:
    tree = result["tree"]
    lines = [
        f"- 实际深度：{tree['actual_depth']}",
        f"- 叶节点数：{tree['leaf_count']}",
        f"- 训练时看到的行数：{tree['training_rows_seen']}",
        f"- 使用的数值特征：{names_text(tree['features_used'])}",
        "",
        "| 节点路径 | 字段 | 阈值 | 左子树 | 右子树 |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for item in tree["splits"]:
        lines.append(
            f"| {item['path']} | {item['field']} | {item['threshold']:.10g} | {item['left_rule']} | {item['right_rule']} |"
        )
    if not tree["splits"]:
        lines.append("| 根 | — | — | — | — |")
    lines.extend(["", "| 叶节点路径 | 预测类别 | 训练样本 |", "| --- | --- | ---: |"])
    for leaf in tree["leaves"]:
        lines.append(f"| {leaf['path']} | {leaf['prediction']} | {leaf['train_count']} |")
    lines.append("")
    return lines


def delta_sentence(label: str, before, after) -> str:
    if before is None or after is None:
        return f"{label}：主评估或排除冲突组后的指标无法计算，不能给出差值。"
    return f"{label}：主评估 {before:.4f}，排除冲突组后 {after:.4f}，差值 {after - before:+.4f}。"


def build_interpretation(summary: dict) -> dict[str, str]:
    within = summary["schemes"]["within_device_time"]
    holdout = summary["schemes"]["device_holdout"]
    sentences = []
    for scheme_name, scheme in (("同设备未来时间", within), ("未见设备", holdout)):
        for model_name, key in (("多数类", "majority"), ("决策树", "decision_tree")):
            primary = scheme["primary"][key]["metrics"]
            sensitive = scheme["without_conflict_groups"][key]["metrics"]
            sentences.append(delta_sentence(f"{scheme_name}{model_name} Accuracy", primary["accuracy"], sensitive["accuracy"]))
            sentences.append(
                delta_sentence(
                    f"{scheme_name}{model_name} Balanced Accuracy",
                    primary["balanced_accuracy"],
                    sensitive["balanced_accuracy"],
                )
            )
    tree_within = within["primary"]["decision_tree"]["metrics"]["accuracy"]
    tree_holdout = holdout["primary"]["decision_tree"]["metrics"]["accuracy"]
    bal_within = within["primary"]["decision_tree"]["metrics"]["balanced_accuracy"]
    bal_holdout = holdout["primary"]["decision_tree"]["metrics"]["balanced_accuracy"]
    if tree_within is None or tree_holdout is None:
        device_text = "两种划分的决策树准确率有无法计算的情况，不能比较设备隔离带来的下降。"
    else:
        gap = tree_within - tree_holdout
        device_text = f"决策树 Accuracy：同设备未来时间 {tree_within:.4f}，未见设备 {tree_holdout:.4f}，差值 {gap:+.4f}。"
        if gap >= DEVICE_DROP:
            device_text += "留出整台设备后准确率下降达到 0.05，模型可能依赖设备特定的数据模式。"
        else:
            device_text += "准确率差值小于本次描述用的 0.05。这仍然不是故障确诊。"
    if bal_within is None or bal_holdout is None:
        balance_text = "决策树 Balanced Accuracy 有无法计算的情况，不能只凭 Accuracy 判断跨设备能力。"
    else:
        gap = bal_within - bal_holdout
        balance_text = f"决策树 Balanced Accuracy：同设备未来时间 {bal_within:.4f}，未见设备 {bal_holdout:.4f}，差值 {gap:+.4f}。"
        if gap >= DEVICE_DROP:
            balance_text += "平衡准确率在未见设备上下降达到 0.05，类别均衡后的区分能力没有完整保留到新设备。"
        else:
            balance_text += "平衡准确率的下降小于 0.05。"
    research = research_sentence(within, holdout)
    conflict = "冲突样本的影响以主评估和敏感性评估的差值为准。" + "".join(sentences)
    return {
        "conflict_impact": conflict,
        "device_dependence": device_text + balance_text,
        "research_value": research,
        "not_a_diagnosis": "这些数字只是测试集分类重合程度。它们不是故障预测成绩，不是故障确诊，也不是官方报警或根因诊断。模型没有接入业务接口。",
    }


def beats(scheme: dict) -> tuple[bool, str]:
    tree = scheme["primary"]["decision_tree"]["metrics"]["balanced_accuracy"]
    majority = scheme["primary"]["majority"]["metrics"]["balanced_accuracy"]
    label = "Balanced Accuracy"
    if tree is None or majority is None:
        tree = scheme["primary"]["decision_tree"]["metrics"]["accuracy"]
        majority = scheme["primary"]["majority"]["metrics"]["accuracy"]
        label = "Accuracy"
    if tree is None or majority is None:
        return False, f"{label}无法比较"
    return tree >= majority + VISIBLE_DELTA, f"{label} {tree:.4f} 对比多数类 {majority:.4f}"


def research_sentence(within: dict, holdout: dict) -> str:
    within_better, within_detail = beats(within)
    holdout_better, holdout_detail = beats(holdout)
    if within_better and holdout_better:
        return (
            "决策树在两种严格划分上都高于多数类至少 0.02。"
            f"同设备未来时间：{within_detail}。未见设备：{holdout_detail}。"
            "数值特征值得在同样的严格划分下继续做可解释实验。这次结果仍然不是故障预测或确诊。"
        )
    if within_better and not holdout_better:
        return (
            "决策树只在同设备未来时间上明显高于多数类，留出整台设备后优势没有保持。"
            f"同设备未来时间：{within_detail}。未见设备：{holdout_detail}。"
            "暂不能据此升级为更复杂的模型。"
        )
    if holdout_better and not within_better:
        return (
            "决策树在未见设备上高于多数类，但同设备未来时间没有达到同样的增益。"
            f"同设备未来时间：{within_detail}。未见设备：{holdout_detail}。"
            "可以保留这个简单基线做对照，不宜直接增加模型复杂度。"
        )
    return (
        "决策树没有在两种严格划分上同时稳定超过多数类至少 0.02。"
        f"同设备未来时间：{within_detail}。未见设备：{holdout_detail}。"
        "这次基线没有表现出值得立刻加深模型的增益。"
    )


def render_markdown(summary: dict) -> str:
    lines = [
        "# 官方数值故障分类基线",
        "",
        "本实验只比较多数类和最大深度 3 的决策树。没有调用 DeepSeek，没有接入智能体、异常检测、业务接口或前端。",
        "成绩是测试集上的分类重合程度，不是故障预测、故障确诊、官方报警或根因诊断。",
        "Balanced Accuracy 是五类召回率的简单平均；五类都能计算时它与 Macro Recall 相同。分母为 0 时记为无法计算，不补成 0 或 100%。",
        "",
        "## 1. 数据与特征",
        "",
        f"- 记录数：{summary['record_count']}",
        f"- 故障类型：{names_text(list(CLASS_NAMES))}",
        f"- 实现：{summary['tree_implementation']}",
        "",
        "最终进入决策树的数值特征：",
        "",
    ]
    for index, name in enumerate(summary["input_features"], start=1):
        lines.append(f"{index}. {name}")
    lines.extend(["", "明确排除：", ""])
    for item in summary["excluded_inputs"]:
        lines.append(f"- {item['field']}：{item['reason']}")
    lines.extend(["", "## 2. 划分", ""])
    for key in ("within_device_time", "device_holdout"):
        scheme = summary["schemes"][key]
        primary = scheme["primary"]["split"]
        sensitive = scheme["without_conflict_groups"]
        lines.extend(
            [
                f"### {scheme['name']}",
                "",
                scheme["rule"],
                "",
                f"- 主评估训练 {primary['train_records']} 条，测试 {primary['test_records']} 条",
                f"- 训练设备 {primary['train_devices']}，测试设备 {primary['test_devices']}，两侧都出现的设备 {primary['devices_in_both']}",
                f"- 跨越两侧的同时间组：{primary['same_time_groups_crossing_split']}",
                f"- 测试时刻不晚于同设备训练时刻的记录：{primary['test_records_not_strictly_after_same_device_train']}",
                f"- 训练故障分布：{counts_text(primary['train_class_counts'])}",
                f"- 测试故障分布：{counts_text(primary['test_class_counts'])}",
                f"- 排除冲突组后训练 {sensitive['split']['train_records']} 条，测试 {sensitive['split']['test_records']} 条",
                f"- 从训练侧去掉 {sensitive['removed_train_records']} 条，从测试侧去掉 {sensitive['removed_test_records']} 条",
                "",
            ]
        )
    held = summary["schemes"]["device_holdout"]["primary"]["held_out_devices"]
    lines.extend(
        [
            "未见设备测试留出的设备：",
            "",
            "| 设备编号 | 设备类别 | 记录数 | 故障类型 |",
            "| --- | --- | ---: | --- |",
        ]
    )
    for item in held:
        fault_text = "；".join(f"{name} {count}" for name, count in item["fault_counts"].items())
        lines.append(f"| {item['device_id']} | {item['equipment_category']} | {item['record_count']} | {fault_text} |")
    lines.extend(["", "## 3. 主评估", ""])
    for key in ("within_device_time", "device_holdout"):
        scheme = summary["schemes"][key]
        lines.append(f"## {scheme['name']}")
        lines.append("")
        lines.append(f"多数类预测：{scheme['primary']['majority']['predicted_class']}。这个类别只由训练集频数决定。")
        lines.append("")
        lines.extend(render_metrics("多数类", scheme["primary"]["majority"]))
        lines.extend(render_metrics("决策树", scheme["primary"]["decision_tree"]))
        lines.extend(render_tree(scheme["primary"]["decision_tree"]))
    lines.extend(["## 4. 排除冲突时间组后的敏感性评估", "", summary["interpretation"]["conflict_impact"], ""])
    for key in ("within_device_time", "device_holdout"):
        scheme = summary["schemes"][key]
        block = scheme["without_conflict_groups"]
        lines.append(f"### {scheme['name']}")
        lines.append("")
        lines.append(block["note"])
        lines.append("")
        lines.append(f"多数类预测：{block['majority']['predicted_class']}。")
        lines.append("")
        lines.extend(render_metrics("多数类", block["majority"]))
        lines.extend(render_metrics("决策树", block["decision_tree"]))
        lines.extend(render_tree(block["decision_tree"]))
    lines.extend(
        [
            "## 5. 风险与结论",
            "",
            summary["interpretation"]["device_dependence"],
            "",
            summary["interpretation"]["research_value"],
            "",
            summary["interpretation"]["not_a_diagnosis"],
            "",
            "没有使用随机记录 80/20 划分。没有用测试集统计量做特征处理。没有把分类结果写入业务接口或 SQLite。",
            "",
        ]
    )
    return "\n".join(lines)


def validate(summary: dict, samples: list[Sample]) -> None:
    if summary["record_count"] != 24000:
        raise ExperimentError("记录数不是 24000。")
    if set(summary["input_features"]) & set(LABEL_FIELDS):
        raise ExperimentError("输入特征包含答案标签。")
    banned = set(EXCLUDED_MEASUREMENTS) | set(LABEL_FIELDS) | {SPLIT_FIELD, "设备编号", "设备类别", "采集时间", "工作表"}
    if set(summary["input_features"]) & banned:
        raise ExperimentError("输入特征包含被排除字段。")
    within = summary["schemes"]["within_device_time"]["primary"]["split"]
    holdout = summary["schemes"]["device_holdout"]["primary"]["split"]
    if within["train_records"] + within["test_records"] != 24000:
        raise ExperimentError("同设备时间划分没有覆盖全部记录。")
    if holdout["train_records"] + holdout["test_records"] != 24000:
        raise ExperimentError("设备留出划分没有覆盖全部记录。")
    if within["test_records_not_strictly_after_same_device_train"] != 0:
        raise ExperimentError("同设备未来时间划分存在时间倒流。")
    if within["same_time_groups_crossing_split"] != 0 or holdout["same_time_groups_crossing_split"] != 0:
        raise ExperimentError("同一采集时间组跨越训练和测试。")
    if holdout["devices_in_both"] != 0:
        raise ExperimentError("留出设备划分存在设备交叉。")
    for scheme in summary["schemes"].values():
        for stage in (scheme["primary"], scheme["without_conflict_groups"]):
            for model_name in ("majority", "decision_tree"):
                metrics = stage[model_name]["metrics"]
                total = sum(sum(row) for row in stage[model_name]["confusion_matrix"])
                if total != stage["split"]["test_records"]:
                    raise ExperimentError("混淆矩阵合计不等于测试样本数。")
                if metrics["accuracy"] is not None and not 0 <= metrics["accuracy"] <= 1:
                    raise ExperimentError("准确率超出 0 到 1。")
            predicted = stage["majority"]["predicted_class"]
            counts = stage["split"]["train_class_counts"]
            best_count = max(counts.values())
            tied = [name for name in CLASS_NAMES if counts[name] == best_count]
            if predicted != tied[0]:
                raise ExperimentError("多数类与训练集频数不一致。")
            expected = stage["split"]["test_class_counts"][predicted] / stage["split"]["test_records"]
            actual = stage["majority"]["metrics"]["accuracy"]
            if actual is None or abs(actual - round(expected, 6)) > 1e-6:
                raise ExperimentError("多数类准确率不等于测试集中该类别的占比。")
            if stage["decision_tree"]["tree"]["actual_depth"] > MAX_DEPTH:
                raise ExperimentError("决策树深度超过 3。")
    if len(samples) != 24000:
        raise ExperimentError("样本数不一致。")
    if file_sha256(DATASET_FILE) != EXPECTED_HASH:
        raise ExperimentError("Excel 哈希发生变化。")


def build_summary(samples: list[Sample], sheet_order: list[str], conflict: set[tuple[str, str]]) -> dict:
    train_a, test_a, not_split = within_device_split(samples)
    train_b, test_b, held_rows = device_holdout_split(samples, sheet_order)
    within = evaluate_scheme(
        "同设备未来时间测试",
        "每台设备按不同采集时刻排序，留出最后约 20% 的时刻作为测试。同一时刻的全部记录留在同一侧。时刻少于 2 个的设备全部进入训练。",
        train_a,
        test_a,
        {"devices_not_split": not_split},
    )
    holdout = evaluate_scheme(
        "未见设备测试",
        "每个设备类别按设备编号排序，留出末尾 max(1, round(20%)) 台完整设备。本次与可行性审计使用同一规则。",
        train_b,
        test_b,
        {"held_out_devices": held_rows},
    )
    excluded = [
        {"field": "转速(rpm)", "reason": "审计中主要由设备类别造成差异，并且与转频冗余。"},
        {"field": "转频(Hz)", "reason": "与转速除以 60 基本相同。"},
        {"field": "最大振动(mm/s)", "reason": "等于 X、Y 向振动的较大值，不是独立测点。"},
    ]
    for name in FREQUENCY_FIELDS:
        excluded.append({"field": name, "reason": "在每台设备内部恒定，实际区分的是设备类别。"})
    for name in LABEL_FIELDS:
        excluded.append({"field": name, "reason": "官方结果标签，禁止作为模型输入。"})
    excluded.append({"field": SPLIT_FIELD, "reason": "不是观测值；本文件中全部为开发集。"})
    for name in ("记录编号", "设备编号", "设备类别", "采集时间", "工作表", "Excel行号"):
        excluded.append({"field": name, "reason": "标识、分组或时间字段，只用于划分，不进入模型。"})
    summary = {
        "experiment": "official_numeric_fault_classification_baseline",
        "model_trained": True,
        "deepseek_called": False,
        "connected_to_business_api": False,
        "random_record_split_used": False,
        "tree_implementation": "Python 标准库，基尼系数，最大深度 3。当前环境没有 scikit-learn、numpy、pandas。",
        "record_count": len(samples),
        "class_names": list(CLASS_NAMES),
        "class_order_tie_break": "训练集频数更高者；频数相同则取类别名单中更靠前者。名单按审计报告的记录数从多到少固定，不在测试集上重新排序。",
        "input_features": list(MODEL_FEATURES),
        "excluded_inputs": excluded,
        "conflict_groups": len(conflict),
        "conflict_records": records_in_keys(samples, conflict),
        "excel_sha256": file_sha256(DATASET_FILE),
        "schemes": {"within_device_time": within, "device_holdout": holdout},
    }
    summary["interpretation"] = build_interpretation(summary)
    return summary


def main() -> dict:
    before = {path: file_sha256(path) for path in PROTECTED if path.is_file()}
    if SQLITE_PATH.is_file():
        before[SQLITE_PATH] = file_sha256(SQLITE_PATH)
    samples, sheet_order = load_samples()
    conflict = conflict_keys(samples)
    summary = build_summary(samples, sheet_order, conflict)
    validate(summary, samples)
    markdown = render_markdown(summary)
    blob = markdown + json.dumps(summary, ensure_ascii=False)
    if "REC-" in blob:
        raise ExperimentError("输出包含逐条记录编号。")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(markdown, encoding="utf-8")
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    after = {path: file_sha256(path) for path in before}
    if before != after:
        raise ExperimentError("写出报告后原始 Excel、业务代码或 SQLite 发生变化。")
    print(f"report={REPORT_PATH}")
    print(f"summary={SUMMARY_PATH}")
    for key, scheme in summary["schemes"].items():
        for stage_name in ("primary", "without_conflict_groups"):
            stage = scheme[stage_name]
            print(
                key,
                stage_name,
                "majority",
                stage["majority"]["metrics"]["accuracy"],
                "tree",
                stage["decision_tree"]["metrics"]["accuracy"],
                "balanced",
                stage["decision_tree"]["metrics"]["balanced_accuracy"],
            )
    print("PASS")
    return summary


if __name__ == "__main__":
    main()
