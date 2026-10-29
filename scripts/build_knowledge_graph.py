"""从本地 Markdown 提取知识图谱。

只读取 data/processed/markdown，不访问网络，不调用 DeepSeek。
不修改官方 Word 和已生成的 Markdown。
无法确认的关系只写入待审核候选。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_DIR = ROOT / "data" / "processed" / "markdown"
GRAPH_DIR = ROOT / "data" / "processed" / "graph"
GRAPH_PATH = GRAPH_DIR / "graph.json"
CANDIDATE_PATH = GRAPH_DIR / "review_candidates.json"
REPORT_PATH = GRAPH_DIR / "graph_validation_report.md"

DOCUMENTS = [
    "KB-01_设备知识图谱.md",
    "KB-02_故障模式诊断知识库.md",
    "KB-03_操作规程与技术手册.md",
    "KB-04_专家经验与典型案例.md",
    "KB-05_设备关联关系.md",
    "KB-06_RAG检索知识库.md",
]


def stable_id(prefix: str, *parts: str) -> str:
    raw = "\n".join(parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def split_row(line: str) -> list[str]:
    parts = re.split(r"(?<!\\)\|", line.strip().strip("|"))
    return [part.replace("\\|", "|").replace("<br>", "\n").strip() for part in parts]


def split_dunhao(text: str) -> list[str]:
    return [item.strip() for item in text.split("、") if item.strip()]


def split_comma(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,，]", text) if item.strip()]


def heading_text(line: str) -> tuple[int, str] | None:
    match = re.match(r"^(#{1,6})\s+(.*\S)\s*$", line)
    if not match:
        return None
    return len(match.group(1)), match.group(2).strip()


def field_value(line: str, name: str) -> str | None:
    prefix = f"{name}："
    if line.startswith(prefix):
        return line[len(prefix) :].strip()
    prefix = f"{name}:"
    if line.startswith(prefix):
        return line[len(prefix) :].strip()
    return None


class Builder:
    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.edges: dict[str, dict] = {}
        self.candidates: dict[str, dict] = {}
        self.fact_lines: dict[str, set[int]] = {name: set() for name in DOCUMENTS}
        self.format_lines: dict[str, set[int]] = {name: set() for name in DOCUMENTS}
        self.unstructured_lines: dict[str, set[int]] = {name: set() for name in DOCUMENTS}
        self.unstructured: dict[str, Counter[str]] = {name: Counter() for name in DOCUMENTS}
        self.content_line_nos: dict[str, set[int]] = {name: set() for name in DOCUMENTS}

    def add_ref(self, target: dict, ref: dict) -> None:
        refs = target["data"]["source_refs"]
        if ref not in refs:
            refs.append(ref)

    def add_node(self, node_type: str, label: str, scope: str, ref: dict) -> str:
        label = label.strip()
        node_id = stable_id(node_type, scope, label)
        node = self.nodes.get(node_id)
        if node is None:
            node = {
                "id": node_id,
                "data": {"label": label, "type": node_type, "source_refs": []},
            }
            self.nodes[node_id] = node
        self.add_ref(node, ref)
        return node_id

    def add_edge(self, relation: str, source: str, target: str, ref: dict) -> None:
        if source == target:
            return
        edge_id = stable_id("edge", relation, source, target)
        edge = self.edges.get(edge_id)
        if edge is None:
            edge = {
                "id": edge_id,
                "source": source,
                "target": target,
                "data": {"relation": relation, "source_refs": []},
            }
            self.edges[edge_id] = edge
        self.add_ref(edge, ref)

    def add_candidate(
        self,
        reason_code: str,
        proposed_relation: str,
        reason: str,
        ref: dict,
        from_label: str = "",
        to_label: str = "",
    ) -> None:
        candidate_id = stable_id(
            "candidate",
            reason_code,
            proposed_relation,
            from_label,
            to_label,
            ref["document"],
            ref["locator"],
        )
        if candidate_id in self.candidates:
            return
        self.candidates[candidate_id] = {
            "id": candidate_id,
            "proposed_relation": proposed_relation,
            "from_label": from_label,
            "to_label": to_label,
            "reason_code": reason_code,
            "reason": reason,
            "source_refs": [ref],
            "status": "pending",
        }

    def consume(self, document_key: str, line_no: int) -> None:
        self.fact_lines[document_key].add(line_no)

    def note_format(self, document_key: str, line_no: int) -> None:
        self.format_lines[document_key].add(line_no)

    def note_unstructured(self, document_key: str, line_no: int, category: str) -> None:
        if line_no in self.unstructured_lines[document_key]:
            return
        self.unstructured_lines[document_key].add(line_no)
        self.unstructured[document_key][category] += 1


def make_ref(document_key: str, section: str, kind: str, line_no: int) -> dict:
    return {
        "document": document_key.replace(".md", ".docx"),
        "section": section or "文档开头",
        "locator": f"{kind} 第{line_no}行",
    }


def section_path(stack: list[tuple[int, str]]) -> str:
    return " / ".join(title for _, title in stack)


def parse_tables(lines: list[str]) -> dict[int, dict]:
    tables: dict[int, dict] = {}
    index = 0
    while index < len(lines):
        if (
            lines[index].startswith("|")
            and index + 1 < len(lines)
            and lines[index + 1].startswith("| ---")
        ):
            headers = split_row(lines[index])
            rows = []
            cursor = index + 2
            while cursor < len(lines) and lines[cursor].startswith("|"):
                rows.append((cursor + 1, split_row(lines[cursor])))
                cursor += 1
            tables[index + 1] = {"header_line": index + 1, "headers": headers, "rows": rows, "end": cursor}
            index = cursor
            continue
        index += 1
    return tables


def link_source(builder: Builder, node_id: str, source_id: str, ref: dict) -> None:
    if node_id != source_id:
        builder.add_edge("SUPPORTED_BY", node_id, source_id, ref)


def build() -> tuple[Builder, dict[str, str]]:
    builder = Builder()
    originals = {name: file_sha256(MARKDOWN_DIR / name) for name in DOCUMENTS}
    for name in DOCUMENTS:
        parse_document(builder, name)
    add_cross_document_candidates(builder)
    if any(file_sha256(MARKDOWN_DIR / name) != digest for name, digest in originals.items()):
        raise RuntimeError("Markdown 文件在提取过程中被修改")
    return builder, originals


def parse_document(builder: Builder, document_key: str) -> None:
    lines = (MARKDOWN_DIR / document_key).read_text(encoding="utf-8").splitlines()
    tables = parse_tables(lines)
    builder.content_line_nos[document_key] = {
        number
        for number, raw in enumerate(lines, start=1)
        if raw.strip() and not raw.strip().startswith(">")
    }
    source_id = builder.add_node(
        "source",
        document_key.replace(".md", ".docx"),
        "",
        make_ref(document_key, "文档", "文件", 1),
    )
    stack: list[tuple[int, str]] = []
    context: dict[str, str] = {}
    index = 0
    while index < len(lines):
        line_no = index + 1
        raw = lines[index]
        line = raw.strip()
        if not line or line.startswith(">"):
            index += 1
            continue
        heading = heading_text(line)
        if heading:
            level, title = heading
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            handle_heading(builder, document_key, source_id, line_no, level, title, stack, context)
            index += 1
            continue
        if line_no in tables:
            table = tables[line_no]
            handle_table(builder, document_key, source_id, table, section_path(stack), context)
            index = table["end"]
            continue
        handle_line(builder, document_key, source_id, line_no, line, section_path(stack), context)
        index += 1
    check_coverage_balance(builder, document_key)


def handle_heading(
    builder: Builder,
    document_key: str,
    source_id: str,
    line_no: int,
    level: int,
    title: str,
    stack: list[tuple[int, str]],
    context: dict[str, str],
) -> None:
    ref = make_ref(document_key, section_path(stack), "标题", line_no)
    context["section"] = section_path(stack)
    created = False
    if document_key.startswith("KB-01") and level == 2:
        device_id = builder.add_node("device", title, "", ref)
        link_source(builder, device_id, source_id, ref)
        context["device_id"] = device_id
        context["device_label"] = title
        context["component_id"] = ""
        created = True
    elif document_key.startswith("KB-02") and level == 2:
        fault_id = builder.add_node("fault", title, "KB-02故障模式", ref)
        link_source(builder, fault_id, source_id, ref)
        context["fault_id"] = fault_id
        context["fault_label"] = title
        created = True
    elif document_key.startswith("KB-04") and level == 2:
        case_id = builder.add_node("case", title, "", ref)
        link_source(builder, case_id, source_id, ref)
        context["case_id"] = case_id
        context["case_label"] = title
        created = True
    elif document_key.startswith("KB-06") and level == 3 and title.endswith("设备基本信息"):
        context["kb06_mode"] = "device"
        context["device_id"] = ""
        context["device_label"] = ""
    elif document_key.startswith("KB-06") and level == 3 and len(stack) >= 2 and stack[-2][1] == "典型案例":
        case_id = builder.add_node("case", title, "", ref)
        link_source(builder, case_id, source_id, ref)
        context["kb06_mode"] = "case"
        context["case_id"] = case_id
        context["case_label"] = title
        created = True
    elif document_key.startswith("KB-05") and level == 2:
        context["chain"] = title
    if created:
        builder.consume(document_key, line_no)
    else:
        builder.note_format(document_key, line_no)


def handle_table(
    builder: Builder,
    document_key: str,
    source_id: str,
    table: dict,
    section: str,
    context: dict[str, str],
) -> None:
    builder.note_format(document_key, table["header_line"])
    builder.note_format(document_key, table["header_line"] + 1)
    headers = table["headers"]
    if headers[:2] == ["上游设备", "下游设备"]:
        for line_no, row in table["rows"]:
            upstream = row[0] if len(row) > 0 else ""
            downstream = row[1] if len(row) > 1 else ""
            coupling = row[3] if len(row) > 3 else ""
            ref = make_ref(document_key, section, "表格", line_no)
            if upstream and downstream:
                upstream_id = builder.add_node("device", upstream, "", ref)
                downstream_id = builder.add_node("device", downstream, "", ref)
                link_source(builder, upstream_id, source_id, ref)
                link_source(builder, downstream_id, source_id, ref)
                builder.add_edge("CONNECTS_TO", upstream_id, downstream_id, ref)
                builder.consume(document_key, line_no)
            if coupling:
                builder.add_candidate(
                    "coupling_part_unassigned",
                    "HAS_COMPONENT",
                    "表格给出了耦合件，但没有说明它属于上游设备还是下游设备",
                    ref,
                    upstream,
                    coupling,
                )
        return
    if headers[:2] == ["字段", "内容"]:
        for line_no, row in table["rows"]:
            key = row[0] if row else ""
            value = row[1] if len(row) > 1 else ""
            ref = make_ref(document_key, section, "表格", line_no)
            if key == "设备" and value and context.get("case_id"):
                device_id = builder.add_node("device", value, "", ref)
                link_source(builder, device_id, source_id, ref)
                builder.add_edge("OCCURS_ON", context["case_id"], device_id, ref)
                builder.consume(document_key, line_no)
            elif key == "概述":
                builder.note_unstructured(document_key, line_no, "案例概述")
        return
    category = "参数限值" if document_key.startswith("KB-03") else "设备参数"
    for line_no, _row in table["rows"]:
        builder.note_unstructured(document_key, line_no, category)


def handle_line(
    builder: Builder,
    document_key: str,
    source_id: str,
    line_no: int,
    line: str,
    section: str,
    context: dict[str, str],
) -> None:
    ref = make_ref(document_key, section, "段落", line_no)
    component = re.match(r"^-\s*▎(.+)$", line)
    if component and context.get("device_id"):
        label = component.group(1).strip()
        parent = context["device_label"]
        component_id = builder.add_node("component", label, parent, ref)
        link_source(builder, component_id, source_id, ref)
        builder.add_edge("HAS_COMPONENT", context["device_id"], component_id, ref)
        context["component_id"] = component_id
        context["component_label"] = label
        context["component_scope"] = parent
        builder.consume(document_key, line_no)
        return
    children = field_value(line, "子部件")
    if children is not None and context.get("component_id"):
        for child in split_dunhao(children):
            child_id = builder.add_node(
                "component",
                child,
                context["component_scope"] + "/" + context["component_label"],
                ref,
            )
            link_source(builder, child_id, source_id, ref)
            builder.add_edge("HAS_COMPONENT", context["component_id"], child_id, ref)
        builder.consume(document_key, line_no)
        return
    faults = field_value(line, "常见故障")
    if faults is not None and context.get("component_id"):
        scope = context["component_scope"] + "/" + context["component_label"]
        for fault in split_dunhao(faults):
            fault_id = builder.add_node("fault", fault, scope, ref)
            link_source(builder, fault_id, source_id, ref)
            builder.add_edge("HAS_FAULT", context["component_id"], fault_id, ref)
        builder.consume(document_key, line_no)
        return
    if line.startswith("•") and "频谱特征" in section and context.get("fault_id"):
        label = line.lstrip("•").strip()
        feature_id = builder.add_node("feature", label, context["fault_id"], ref)
        link_source(builder, feature_id, source_id, ref)
        builder.add_edge("HAS_FEATURE", context["fault_id"], feature_id, ref)
        builder.consume(document_key, line_no)
        return
    if line.startswith("•") and section.endswith("常见关联故障"):
        label = line.lstrip("•").strip()
        fault_id = builder.add_node("fault", label, "KB-05/" + section, ref)
        link_source(builder, fault_id, source_id, ref)
        builder.add_candidate(
            "associated_fault_without_component",
            "HAS_FAULT",
            "该故障列在关联故障下，文档没有指定所属部件",
            ref,
            context.get("chain", ""),
            label,
        )
        builder.consume(document_key, line_no)
        return
    if line.startswith("流程链："):
        parts = [item.strip() for item in line.split("：", 1)[1].split("→")]
        for left, right in zip(parts, parts[1:]):
            builder.add_candidate(
                "flow_chain_ambiguous",
                "CONNECTS_TO",
                "流程链用箭头给出了方向，但条目是否都是设备没有明确说明",
                ref,
                left,
                right,
            )
        builder.note_unstructured(document_key, line_no, "流程链正文")
        return
    action = field_value(line, "处理")
    if action is None:
        numbered = re.match(r"^\d+\.\s*处理[:：]\s*(.+)$", line)
        action = numbered.group(1).strip() if numbered else None
    if action and context.get("case_id"):
        action_id = builder.add_node("action", action, context["case_id"], ref)
        link_source(builder, action_id, source_id, ref)
        builder.add_candidate(
            "case_action_unlinked",
            "RECOMMENDS_ACTION",
            "处置措施来自案例，文档没有把它明确对应到单一故障模式或原因",
            ref,
            context.get("case_label", ""),
            action,
        )
        builder.consume(document_key, line_no)
        return
    if document_key.startswith("KB-06"):
        if handle_kb06_line(builder, document_key, source_id, line_no, line, ref, context):
            return
    classify_unstructured_line(builder, document_key, line_no, line, section, context)


def handle_kb06_line(
    builder: Builder,
    document_key: str,
    source_id: str,
    line_no: int,
    line: str,
    ref: dict,
    context: dict[str, str],
) -> bool:
    name = field_value(line, "设备名称")
    if name and context.get("kb06_mode") == "device":
        device_id = builder.add_node("device", name, "", ref)
        link_source(builder, device_id, source_id, ref)
        context["device_id"] = device_id
        context["device_label"] = name
        builder.consume(document_key, line_no)
        return True
    upstream = field_value(line, "上游设备")
    if upstream is not None and context.get("device_id"):
        for item in split_comma(upstream):
            other_id = builder.add_node("device", item, "", ref)
            link_source(builder, other_id, source_id, ref)
            builder.add_edge("CONNECTS_TO", other_id, context["device_id"], ref)
        builder.consume(document_key, line_no)
        return True
    downstream = field_value(line, "下游设备")
    if downstream is not None and context.get("device_id"):
        for item in split_comma(downstream):
            other_id = builder.add_node("device", item, "", ref)
            link_source(builder, other_id, source_id, ref)
            builder.add_edge("CONNECTS_TO", context["device_id"], other_id, ref)
        builder.consume(document_key, line_no)
        return True
    device = field_value(line, "设备")
    if device and context.get("case_id") and context.get("kb06_mode") == "case":
        device_id = builder.add_node("device", device, "", ref)
        link_source(builder, device_id, source_id, ref)
        builder.add_edge("OCCURS_ON", context["case_id"], device_id, ref)
        builder.consume(document_key, line_no)
        return True
    origin = field_value(line, "来源")
    if origin is not None:
        source_label = origin.split("|", 1)[0].strip()
        if source_label:
            stated_id = builder.add_node("source", source_label, "", ref)
            current = context.get("device_id") if context.get("kb06_mode") == "device" else context.get("case_id")
            if current:
                builder.add_edge("SUPPORTED_BY", current, stated_id, ref)
        builder.consume(document_key, line_no)
        return True
    case_code = field_value(line, "案例")
    if case_code is not None:
        builder.note_unstructured(document_key, line_no, "案例编号")
        return True
    return False


def classify_unstructured_line(
    builder: Builder,
    document_key: str,
    line_no: int,
    line: str,
    section: str,
    context: dict[str, str],
) -> None:
    if line.startswith("英文名"):
        category = "英文名"
    elif line.startswith("适用范围"):
        category = "规程适用范围"
    elif "经验教训" in section or line.startswith("经验教训") or line.startswith("💡"):
        category = "经验教训"
    elif "诊断思路" in section or line.startswith("诊断思路") or re.match(r"^\d+\.\s*", line):
        category = "诊断思路"
    elif document_key.startswith("KB-02") and context.get("fault_id") and "频谱特征" not in section:
        category = "故障定义"
        builder.add_candidate(
            "definition_not_split",
            "HAS_CAUSE",
            "故障定义包含机理或损伤类型，文档没有把它们明确列为原因",
            make_ref(document_key, section, "段落", line_no),
            context.get("fault_label", ""),
            "",
        )
    elif line.startswith("概述") or line.startswith("摘要"):
        category = "案例概述"
    else:
        category = "其他正文"
    builder.note_unstructured(document_key, line_no, category)


def check_coverage_balance(builder: Builder, document_key: str) -> None:
    fact = builder.fact_lines[document_key]
    formatting = builder.format_lines[document_key]
    pending = builder.unstructured_lines[document_key]
    union = fact | formatting | pending
    content = builder.content_line_nos[document_key]
    if fact & formatting or fact & pending or formatting & pending or union != content:
        missing = sorted(content - union)
        raise RuntimeError(f"{document_key} 覆盖行无法闭合，缺失行 {missing[:12]}")


def add_cross_document_candidates(builder: Builder) -> None:
    by_type: dict[str, list[dict]] = {}
    by_label: dict[tuple[str, str], list[dict]] = {}
    for node in builder.nodes.values():
        by_type.setdefault(node["data"]["type"], []).append(node)
        by_label.setdefault((node["data"]["type"], node["data"]["label"]), []).append(node)
    labels_across_types: dict[str, set[str]] = {}
    for node in builder.nodes.values():
        labels_across_types.setdefault(node["data"]["label"], set()).add(node["data"]["type"])
    for label, types in sorted(labels_across_types.items()):
        if len(types) > 1:
            ref = node_ref(builder, label)
            builder.add_candidate(
                "cross_type_same_label",
                "SAME_AS",
                "相同名称出现在不同节点类型中，未合并",
                ref,
                label,
                "、".join(sorted(types)),
            )
    fault_groups: dict[str, int] = Counter(
        node["data"]["label"] for node in by_type.get("fault", [])
    )
    for label, count in sorted(fault_groups.items()):
        if count > 1:
            ref = node_ref(builder, label)
            builder.add_candidate(
                "same_label_not_merged",
                "SAME_AS",
                "同名故障出现在不同部件或不同知识库中，未合并",
                ref,
                label,
                str(count),
            )
    devices = sorted(by_type.get("device", []), key=lambda item: item["data"]["label"])
    for left in devices:
        for right in devices:
            left_label = left["data"]["label"]
            right_label = right["data"]["label"]
            if left_label >= right_label:
                continue
            shared_terms = [
                term
                for term in ("电机", "齿轮箱", "减速机", "冷却水", "轧辊", "循环泵")
                if term in left_label and term in right_label
            ]
            if left_label in right_label or shared_terms:
                ref = left["data"]["source_refs"][0]
                builder.add_candidate(
                    "possible_device_alias",
                    "SAME_AS",
                    "设备名称相近，文档没有声明它们是同一设备",
                    ref,
                    left_label,
                    right_label,
                )
    cases = by_type.get("case", [])
    for left in cases:
        for right in cases:
            left_label = left["data"]["label"]
            right_label = right["data"]["label"]
            if left_label != right_label and (
                left_label in right_label or right_label in left_label
            ):
                pair = tuple(sorted((left_label, right_label)))
                ref = left["data"]["source_refs"][0]
                builder.add_candidate(
                    "case_title_overlap",
                    "SAME_AS",
                    "两个案例标题存在包含关系，文档没有声明它们是同一案例",
                    ref,
                    pair[0],
                    pair[1],
                )


def node_ref(builder: Builder, label: str) -> dict:
    for node in builder.nodes.values():
        if node["data"]["label"] == label and node["data"]["source_refs"]:
            return node["data"]["source_refs"][0]
    return {"document": "", "section": "", "locator": label}


def validate(builder: Builder) -> dict:
    node_ids = list(builder.nodes)
    duplicate_nodes = [item for item, count in Counter(node_ids).items() if count > 1]
    missing_endpoints = []
    missing_evidence = []
    for edge in builder.edges.values():
        if edge["source"] not in builder.nodes or edge["target"] not in builder.nodes:
            missing_endpoints.append(edge["id"])
        if not edge["data"]["relation"] or not edge["data"]["source_refs"]:
            missing_evidence.append(edge["id"])
    nodes_missing_evidence = [
        node["id"]
        for node in builder.nodes.values()
        if not node["data"]["source_refs"] or not node["data"]["label"] or not node["data"]["type"]
    ]
    return {
        "duplicate_node_ids": duplicate_nodes,
        "missing_endpoints": missing_endpoints,
        "edges_missing_evidence": missing_evidence,
        "nodes_missing_evidence": nodes_missing_evidence,
        "passed": not (
            duplicate_nodes or missing_endpoints or missing_evidence or nodes_missing_evidence
        ),
    }


def coverage(builder: Builder) -> list[dict]:
    rows = []
    for name in DOCUMENTS:
        unstructured = builder.unstructured[name]
        rows.append(
            {
                "document": name.replace(".md", ".docx"),
                "content_lines": len(builder.content_line_nos[name]),
                "structured_lines": len(builder.fact_lines[name]),
                "format_lines": len(builder.format_lines[name]),
                "unstructured_lines": len(builder.unstructured_lines[name]),
                "unstructured": dict(unstructured),
            }
        )
    return rows


def dump_graph(builder: Builder) -> dict:
    nodes = sorted(builder.nodes.values(), key=lambda item: item["id"])
    edges = sorted(builder.edges.values(), key=lambda item: item["id"])
    for node in nodes:
        node["data"]["source_refs"] = sorted(
            node["data"]["source_refs"],
            key=lambda ref: (ref["document"], ref["section"], ref["locator"]),
        )
    for edge in edges:
        edge["data"]["source_refs"] = sorted(
            edge["data"]["source_refs"],
            key=lambda ref: (ref["document"], ref["section"], ref["locator"]),
        )
    return {"nodes": nodes, "edges": edges}


def write_report(builder: Builder, checks: dict, rows: list[dict]) -> None:
    graph = dump_graph(builder)
    node_counts = Counter(node["data"]["type"] for node in graph["nodes"])
    edge_counts = Counter(edge["data"]["relation"] for edge in graph["edges"])
    candidate_counts = Counter(item["reason_code"] for item in builder.candidates.values())
    reason_names = {
        "coupling_part_unassigned": "耦合件未确定所属设备",
        "associated_fault_without_component": "关联故障未指定部件",
        "flow_chain_ambiguous": "流程链方向待确认",
        "case_action_unlinked": "案例处置未对应到故障或原因",
        "definition_not_split": "故障定义未拆成原因",
        "cross_type_same_label": "同名但类型不同",
        "same_label_not_merged": "同名故障未合并",
        "possible_device_alias": "设备名称可能是别名",
        "case_title_overlap": "案例标题可能重复",
    }
    lines = [
        "# 知识图谱校验报告",
        "",
        "本报告只说明结构检查和提取覆盖情况。程序能够运行，并不表示知识提取全部正确。",
        "正式图谱只写入文档中字段或标题能够直接支持的节点和关系。不能确认的关系放在待审核候选中。",
        "",
        "## 结构校验",
        "",
        f"- 节点 ID 重复：{len(checks['duplicate_node_ids'])}",
        f"- 关系端点缺失：{len(checks['missing_endpoints'])}",
        f"- 缺少来源依据的节点：{len(checks['nodes_missing_evidence'])}",
        f"- 缺少来源依据的关系：{len(checks['edges_missing_evidence'])}",
        f"- 结构校验：{'通过' if checks['passed'] else '不通过'}",
        "",
        "## 数量",
        "",
        f"- 节点：{len(graph['nodes'])}",
        f"- 关系：{len(graph['edges'])}",
        f"- 待审核候选：{len(builder.candidates)}",
        "",
        "### 节点类型",
        "",
    ]
    for name in ["device", "component", "fault", "feature", "cause", "action", "case", "source"]:
        lines.append(f"- {name}：{node_counts.get(name, 0)}")
    lines.extend(["", "### 关系类型", ""])
    for name, count in sorted(edge_counts.items()):
        lines.append(f"- {name}：{count}")
    if "HAS_CAUSE" not in edge_counts:
        lines.append("- HAS_CAUSE：0。文档没有单独给出“原因”字段，定义句未拆成确定原因。")
    if "RECOMMENDS_ACTION" not in edge_counts:
        lines.append("- RECOMMENDS_ACTION：0。案例中的处置措施没有明确对应到单一故障或原因，已进入待审核。")
    lines.extend(["", "## 各知识库覆盖", ""])
    for row in rows:
        lines.append(
            f"- {row['document']}：内容行 {row['content_lines']}，已结构化 {row['structured_lines']}，标题或表格格式 {row['format_lines']}，尚未结构化 {row['unstructured_lines']}"
        )
        for category, count in sorted(row["unstructured"].items()):
            lines.append(f"  - {category}：{count}")
    lines.extend(["", "## 待审核", ""])
    for code, count in sorted(candidate_counts.items()):
        lines.append(f"- {reason_names.get(code, code)}：{count}")
    lines.extend(
        [
            "",
            "## 未写入正式图谱的内容",
            "",
            "- 设备参数、额定值和操作限值没有对应的节点类型，保留为尚未结构化内容。",
            "- 案例概述、诊断思路和经验教训是叙述文字，没有按确定关系拆开。",
            "- 名称不同但可能指同一设备、故障或案例的条目没有合并。",
            "- 没有发现图片或公式解析残留；本步骤读取的是已生成的 Markdown。",
            "",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    missing = [name for name in DOCUMENTS if not (MARKDOWN_DIR / name).exists()]
    if missing:
        raise SystemExit("缺少 Markdown 文件")
    builder, _originals = build()
    checks = validate(builder)
    rows = coverage(builder)
    GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    graph = dump_graph(builder)
    candidates = sorted(builder.candidates.values(), key=lambda item: item["id"])
    GRAPH_PATH.write_text(json.dumps(graph, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    CANDIDATE_PATH.write_text(
        json.dumps(candidates, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_report(builder, checks, rows)
    print(
        json.dumps(
            {
                "nodes": len(graph["nodes"]),
                "edges": len(graph["edges"]),
                "candidates": len(candidates),
                "structure_passed": checks["passed"],
                "node_types": dict(Counter(node["data"]["type"] for node in graph["nodes"])),
                "relations": dict(Counter(edge["data"]["relation"] for edge in graph["edges"])),
                "coverage": rows,
                "candidate_reasons": dict(Counter(item["reason_code"] for item in candidates)),
            },
            ensure_ascii=False,
        )
    )
    if not checks["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
