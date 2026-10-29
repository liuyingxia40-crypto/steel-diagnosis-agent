# -*- coding: utf-8 -*-
"""从本地 Markdown、正式图谱和检索索引生成知识展示树。

只写入 data/processed/graph/knowledge_view.json 和 knowledge_view_report.json。
不修改 graph.json，不调用外部接口。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_DIR = ROOT / "data" / "processed" / "markdown"
GRAPH_FILE = ROOT / "data" / "processed" / "graph" / "graph.json"
INDEX_FILE = ROOT / "data" / "processed" / "rag" / "index.json"
VIEW_FILE = ROOT / "data" / "processed" / "graph" / "knowledge_view.json"
REPORT_FILE = ROOT / "data" / "processed" / "graph" / "knowledge_view_report.json"

MODULES = (
    ("KB-01", "设备知识"),
    ("KB-02", "故障模式"),
    ("KB-03", "操作规程"),
    ("KB-04", "专家经验与典型案例"),
    ("KB-05", "设备关联关系"),
    ("KB-06", "RAG检索知识"),
)
MODULE_LABEL = {code: name for code, name in MODULES}


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def view_id(path: str) -> str:
    return "view-" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]


def heading_level(line: str) -> int:
    match = re.match(r"^(#{1,6})\s+\S", line)
    return len(match.group(1)) if match else 0


def heading_text(line: str, level: int) -> str:
    return line[level:].strip()


def after_colon(line: str) -> str:
    for separator in ("：", ":"):
        if separator in line:
            return line.split(separator, 1)[1].strip()
    return ""


def split_items(text: str) -> list[str]:
    cleaned = text.strip().strip("。").strip()
    if not cleaned:
        return []
    return [part.strip() for part in re.split(r"[、，,]", cleaned) if part.strip()]


def split_name(raw: str) -> tuple[str, list[str]]:
    match = re.match(r"^(.+?)[（(](.+)[）)]$", raw.strip())
    if not match:
        return raw.strip(), []
    return match.group(1).strip(), split_items(match.group(2))


def is_component_bullet(line: str) -> bool:
    text = line.strip()
    return text.startswith("- ▎") or text.startswith("• ▎") or text.startswith("▎")


def component_raw_name(line: str) -> str:
    text = line.strip()
    for prefix in ("- ▎", "• ▎", "▎"):
        if text.startswith(prefix):
            return text[len(prefix) :].strip()
    return text


def bullet_text(line: str) -> str | None:
    text = line.strip()
    for prefix in ("• ", "•", "- ", "* "):
        if text.startswith(prefix):
            return text[len(prefix) :].strip()
    return None


def is_separator_line(line: str) -> bool:
    if not line.strip().startswith("|"):
        return False
    cells = split_row(line)
    if not cells:
        return False
    return all(re.fullmatch(r":?-{2,}:?", cell.replace(" ", "")) or cell == "" for cell in cells)


def split_row(line: str) -> list[str]:
    raw = line.strip()
    if raw.startswith("|"):
        raw = raw[1:]
    if raw.endswith("|"):
        raw = raw[:-1]
    return [cell.strip() for cell in raw.split("|")]


def document_name(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if "原始文档名称" in line:
            name = after_colon(line)
            if name:
                return name
    return fallback


def locator(start: int, end: int | None = None) -> str:
    if end and end != start:
        return f"第{start}-{end}行"
    return f"第{start}行"


def source(document: str, section: str, start: int, end: int | None = None) -> dict[str, str]:
    return {"document": document, "section": section, "locator": locator(start, end)}


def is_treatment(text: str) -> bool:
    compact = text.replace(" ", "").replace("\u3000", "")
    return compact.startswith("处理：") or compact.startswith("处理:")


def empty_value(value: str) -> bool:
    return value.strip() in {"", "-", "—", "——", "/", "无", "暂无"}


def sections(lines: list[str], level: int, start: int = 0, end: int | None = None) -> list[tuple[int, int, str]]:
    stop = len(lines) if end is None else end
    heads = [
        (index, heading_text(lines[index], level))
        for index in range(start, stop)
        if heading_level(lines[index]) == level
    ]
    result: list[tuple[int, int, str]] = []
    for index, (pos, title) in enumerate(heads):
        next_pos = heads[index + 1][0] if index + 1 < len(heads) else stop
        result.append((pos, next_pos, title))
    return result


def intro_text(lines: list[str]) -> tuple[str, int | None, int | None]:
    h1 = next((index for index, line in enumerate(lines) if heading_level(line) == 1), None)
    h2 = next((index for index, line in enumerate(lines) if heading_level(line) == 2), None)
    if h1 is None or h2 is None or h2 <= h1:
        return "", None, None
    buffer: list[str] = []
    start = None
    end = None
    for index in range(h1 + 1, h2):
        line = lines[index].strip()
        if not line or line.startswith(">"):
            continue
        if start is None:
            start = index + 1
        buffer.append(line)
        end = index + 1
    return "".join(buffer), start, end


def read_table(lines: list[str], index: int, end: int) -> tuple[list[str], list[dict], int]:
    header = split_row(lines[index])
    index += 1
    if index < end and is_separator_line(lines[index]):
        index += 1
    rows: list[dict] = []
    while index < end and lines[index].strip().startswith("|"):
        if not is_separator_line(lines[index]):
            rows.append({"cells": split_row(lines[index]), "line": index + 1})
        index += 1
    return header, rows, index


def read_wrapped(lines: list[str], index: int, end: int, prefix: str) -> tuple[str, int, int, int]:
    start_line = index + 1
    text = after_colon(lines[index].strip())
    index += 1
    while index < end:
        nxt = lines[index].strip()
        if not nxt or nxt.startswith("#") or nxt.startswith("|") or nxt.startswith("•") or nxt.startswith("- "):
            break
        if "：" in nxt[:12] or ":" in nxt[:12]:
            break
        text += nxt
        index += 1
    return text, start_line, index, index


class GraphIndex:
    def __init__(self, payload: dict) -> None:
        self.nodes = payload["nodes"]
        self.edges = payload["edges"]
        self.label: dict[str, str] = {}
        self.ntype: dict[str, str] = {}
        self.by_type: dict[str, list[str]] = defaultdict(list)
        for node in self.nodes:
            data = node.get("data") if isinstance(node.get("data"), dict) else {}
            node_id = str(node["id"])
            self.label[node_id] = str(data.get("label") or "")
            self.ntype[node_id] = str(data.get("type") or "")
            self.by_type[self.ntype[node_id]].append(node_id)
        self.out: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        self.relations: set[str] = set()
        self.extra_keys: set[str] = set()
        for edge in self.edges:
            data = edge.get("data") if isinstance(edge.get("data"), dict) else {}
            relation = str(data.get("relation") or "")
            self.relations.add(relation)
            self.extra_keys.update(str(key) for key in data if key != "relation")
            self.out[relation][str(edge["source"])].append(str(edge["target"]))

    def ref(self, node_id: str) -> dict[str, str]:
        return {"id": node_id, "type": self.ntype[node_id], "label": self.label[node_id]}

    def hits(self, type_name: str, label: str) -> list[str]:
        return [node_id for node_id in self.by_type[type_name] if self.label[node_id] == label]

    def children(self, relation: str, source_id: str) -> list[str]:
        return list(self.out[relation].get(source_id, []))

    def subtree(self, root_id: str, relation: str = "HAS_COMPONENT") -> dict[str, list[str]]:
        found: dict[str, list[str]] = defaultdict(list)
        stack = self.children(relation, root_id)
        seen: set[str] = set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            found[self.label[current]].append(current)
            stack.extend(self.children(relation, current))
        return found


class Builder:
    def __init__(self, graph: GraphIndex) -> None:
        self.graph = graph
        self.nodes: list[dict] = []
        self.paths: dict[str, str] = {}
        self.gaps: list[dict[str, str]] = []
        self.devices: dict[str, dict] = {}
        self.cases: list[dict] = []
        self.subtree_cache: dict[str, dict[str, list[str]]] = {}
        self.stats: dict[str, int] = defaultdict(int)
        self.limit_rows: list[dict] = []
        self.case_counts: list[dict] = []
        self.headings: dict[str, list[str]] = {}

    def gap(self, module: str, item: str, reason: str) -> None:
        entry = {"module": module, "item": item, "reason": reason}
        if entry not in self.gaps:
            self.gaps.append(entry)

    def add(self, parent_id: str | None, path: str, **fields: object) -> dict:
        node_id = view_id(path)
        if node_id in self.paths:
            raise RuntimeError(f"展示节点编号冲突：{path}")
        label = str(fields["label"]).strip()
        if not label:
            raise RuntimeError(f"展示节点名称为空：{path}")
        node = {
            "id": node_id,
            "parent_id": parent_id,
            "kind": fields["kind"],
            "module": str(fields.get("module") or ""),
            "label": label,
            "depth": 0,
            "knowledge_type": str(fields["knowledge_type"]),
            "text": str(fields.get("text") or ""),
            "parameters": list(fields.get("parameters") or []),
            "source": fields.get("source"),
            "graph_refs": list(fields.get("graph_refs") or []),
            "alias_graph_refs": list(fields.get("alias_graph_refs") or []),
            "canonical_id": fields.get("canonical_id"),
            "note": str(fields.get("note") or ""),
        }
        self.nodes.append(node)
        self.paths[node_id] = path
        return node

    def finish(self) -> None:
        by_id = {node["id"]: node for node in self.nodes}
        for node in self.nodes:
            depth = 0
            current = node
            seen: set[str] = set()
            while current["parent_id"]:
                if current["id"] in seen:
                    raise RuntimeError("展示树出现循环")
                seen.add(current["id"])
                parent = by_id.get(current["parent_id"])
                if parent is None:
                    raise RuntimeError("展示树缺少父节点")
                depth += 1
                current = parent
            node["depth"] = depth

    def children_of(self, node_id: str) -> list[dict]:
        return [node for node in self.nodes if node["parent_id"] == node_id]

    def descendants(self, node_id: str) -> list[dict]:
        result: list[dict] = []
        for child in self.children_of(node_id):
            result.append(child)
            result.extend(self.descendants(child["id"]))
        return result

    def device_subtree(self, graph_id: str | None) -> dict[str, list[str]]:
        if not graph_id:
            return {}
        cached = self.subtree_cache.get(graph_id)
        if cached is None:
            cached = self.graph.subtree(graph_id)
            self.subtree_cache[graph_id] = cached
        return cached

    def one_ref(self, node_ids: list[str], module: str, item: str, reason: str) -> list[dict]:
        if len(node_ids) == 1:
            return [self.graph.ref(node_ids[0])]
        if len(node_ids) > 1:
            self.gap(module, item, reason)
        return []


def find_markdown(code: str) -> Path:
    matches = sorted(MARKDOWN_DIR.glob(f"{code}*.md"))
    if len(matches) != 1:
        raise RuntimeError(f"{code} 的 Markdown 数量不是 1")
    return matches[0]


def parse_parameters(
    builder: Builder,
    parent_id: str,
    module: str,
    document: str,
    section: str,
    path: str,
    rows: list[dict],
    group_label: str,
    device_graph_id: str | None,
) -> None:
    if not rows:
        return
    group = builder.add(
        parent_id,
        path,
        kind="nav",
        module=module,
        label=group_label,
        knowledge_type=group_label,
        source=source(document, section, rows[0]["line"]),
    )
    for row in rows:
        cells = row["cells"]
        if len(cells) < 2:
            continue
        name = cells[0].strip()
        value = " ".join(cell.strip() for cell in cells[1:] if cell.strip())
        if name in {"参数", "字段", "项目", "属性", "名称", "上游设备", "上游"}:
            continue
        if not name or empty_value(value):
            builder.gap(module, name or "空白参数", "原文没有给出数值，未生成参数节点")
            continue
        kind_name = "运行限值" if ("限值" in name or "报警" in name or "危险" in name) else group_label
        param_path = f"{path}/{name}"
        param = builder.add(
            group["id"],
            param_path,
            kind="knowledge",
            module=module,
            label=name,
            knowledge_type=kind_name,
            text=f"{name}：{value}",
            parameters=[{"name": name, "value": value}],
            source=source(document, f"{section} / {name}", row["line"]),
            graph_refs=link_parameter(builder, device_graph_id, name, module),
        )
        alarm_children = split_alarm(value)
        builder.limit_rows.append(
            {
                "module": module,
                "name": name,
                "has_alarm": "报警" in value,
                "has_danger": "危险" in value,
                "split": len(alarm_children),
            }
        )
        if ("报警" in value or "危险" in value) and not alarm_children:
            builder.gap(module, name, "原文把报警和危险写在同一句里，保留整句，没有拆成两个节点")
        for kind, clause in alarm_children:
            builder.add(
                param["id"],
                f"{param_path}/{kind}/{clause}",
                kind="knowledge",
                module=module,
                label=kind,
                knowledge_type=kind,
                text=clause,
                parameters=[{"name": kind, "value": clause}],
                source=source(document, f"{section} / {name}", row["line"]),
            )


def link_parameter(builder: Builder, device_graph_id: str | None, name: str, module: str) -> list[dict]:
    if not device_graph_id:
        builder.stats["parameters_without_graph"] += 1
        return []
    candidates = builder.graph.children("HAS_PARAMETER", device_graph_id)

    def normal(label: str) -> str:
        return re.sub(r"（.*?）|\(.*?\)", "", label).strip()

    exact = [node_id for node_id in candidates if builder.graph.label[node_id] == name]
    if len(exact) == 1:
        return [builder.graph.ref(exact[0])]
    normalized = [node_id for node_id in candidates if normal(builder.graph.label[node_id]) == normal(name)]
    if len(exact) > 1 or len(normalized) > 1:
        builder.gap(module, name, "正式图谱中有多条同名参数，未自动合并")
        builder.stats["parameters_without_graph"] += 1
        return []
    if len(normalized) == 1:
        return [builder.graph.ref(normalized[0])]
    builder.stats["parameters_without_graph"] += 1
    return []


def split_alarm(value: str) -> list[tuple[str, str]]:
    chunks = [chunk.strip() for chunk in re.split(r"[；;，,]", value) if chunk.strip()]
    if len(chunks) == 1 and "报警" in chunks[0] and "危险" in chunks[0]:
        return []
    found: list[tuple[str, str]] = []
    for chunk in chunks:
        if "危险" in chunk:
            found.append(("危险值", chunk))
        elif "报警" in chunk:
            found.append(("报警值", chunk))
    return found


def link_named(
    builder: Builder,
    parent_graph_id: str | None,
    relation: str,
    name: str,
    module: str,
) -> list[dict]:
    if not parent_graph_id:
        return []
    hits = [
        node_id
        for node_id in builder.graph.children(relation, parent_graph_id)
        if builder.graph.label[node_id] == name
    ]
    return builder.one_ref(hits, module, name, "正式图谱中有多条同名节点，未自动合并")


def parse_kb01(builder: Builder, parent_id: str, lines: list[str], document: str) -> None:
    module = "KB-01"
    intro, intro_start, intro_end = intro_text(lines)
    module_node = builder.add(
        parent_id,
        module,
        kind="nav",
        module=module,
        label=f"{MODULE_LABEL[module]} {module}",
        knowledge_type="知识模块",
        text=intro,
        source=source(document, MODULE_LABEL[module], intro_start or 1, intro_end),
    )
    titles = []
    for start, end, title in sections(lines, 2):
        titles.append(title)
        device_hits = builder.graph.hits("device", title)
        device_refs = builder.one_ref(device_hits, module, title, "正式图谱中有多条同名设备，未自动合并")
        device_graph_id = device_refs[0]["id"] if device_refs else None
        device = builder.add(
            module_node["id"],
            f"{module}/{title}",
            kind="knowledge",
            module=module,
            label=title,
            knowledge_type="设备",
            text=title,
            source=source(document, title, start + 1),
            graph_refs=device_refs,
        )
        builder.devices[title] = device
        index = start + 1
        while index < end:
            line = lines[index].strip()
            if line.startswith("|") and "参数" in line:
                _header, rows, index = read_table(lines, index, end)
                parse_parameters(
                    builder,
                    device["id"],
                    module,
                    document,
                    f"{title} / 基本参数",
                    f"{module}/{title}/基本参数",
                    rows,
                    "基本参数",
                    device_graph_id,
                )
                continue
            if heading_level(lines[index]) == 3 and "部件" in heading_text(lines[index], 3):
                heading_line = index + 1
                group = builder.add(
                    device["id"],
                    f"{module}/{title}/部件结构",
                    kind="nav",
                    module=module,
                    label="部件结构",
                    knowledge_type="部件结构",
                    source=source(document, f"{title} / 部件结构", heading_line),
                )
                parse_components(
                    builder,
                    group["id"],
                    module,
                    document,
                    title,
                    f"{module}/{title}/部件结构",
                    lines,
                    index + 1,
                    end,
                    device_graph_id,
                )
                break
            index += 1
    builder.headings[module] = titles


def parse_components(
    builder: Builder,
    parent_id: str,
    module: str,
    document: str,
    device_name: str,
    path: str,
    lines: list[str],
    start: int,
    end: int,
    device_graph_id: str | None,
) -> None:
    subtree = builder.device_subtree(device_graph_id)
    index = start
    while index < end:
        if heading_level(lines[index]) in {2, 3}:
            break
        if not is_component_bullet(lines[index]):
            index += 1
            continue
        raw_name = component_raw_name(lines[index])
        name, paren_subs = split_name(raw_name)
        line_no = index + 1
        subs: list[str] = []
        faults: list[tuple[str, int]] = []
        sub_line = line_no
        fault_line = line_no
        index += 1
        while index < end:
            nxt = lines[index].strip()
            if not nxt:
                index += 1
                continue
            if is_component_bullet(nxt) or heading_level(lines[index]) in {2, 3}:
                break
            if nxt.startswith("子部件"):
                subs = split_items(after_colon(nxt))
                sub_line = index + 1
                index += 1
                continue
            if nxt.startswith("常见故障"):
                faults = [(item, index + 1) for item in split_items(after_colon(nxt))]
                fault_line = index + 1
                index += 1
                continue
            index += 1
        if not name:
            builder.gap(module, device_name, "有一条部件名称为空，已跳过")
            continue
        if subs and paren_subs and subs != paren_subs:
            builder.gap(module, name, "部件名称括号与子部件行不一致，采用子部件行")
        if not subs:
            subs = paren_subs
        component_hits = subtree.get(name, [])
        component_refs = builder.one_ref(component_hits, module, name, "同一设备下有多条同名部件，未自动合并")
        component_graph_id = component_refs[0]["id"] if len(component_refs) == 1 else None
        component = builder.add(
            parent_id,
            f"{path}/{name}",
            kind="knowledge",
            module=module,
            label=name,
            knowledge_type="部件",
            text=name,
            source=source(document, f"{device_name} / 部件结构 / {name}", line_no),
            graph_refs=component_refs,
        )
        if subs:
            group = builder.add(
                component["id"],
                f"{path}/{name}/子部件",
                kind="nav",
                module=module,
                label="子部件",
                knowledge_type="子部件",
                source=source(document, f"{device_name} / {name} / 子部件", sub_line),
            )
            for sub_name in subs:
                sub_refs, _sub_id = link_subcomponent(builder, component_graph_id, subtree, sub_name, module)
                builder.add(
                    group["id"],
                    f"{path}/{name}/子部件/{sub_name}",
                    kind="knowledge",
                    module=module,
                    label=sub_name,
                    knowledge_type="子部件",
                    text=sub_name,
                    source=source(document, f"{device_name} / {name} / 子部件 / {sub_name}", sub_line),
                    graph_refs=sub_refs,
                )
        if faults:
            group = builder.add(
                component["id"],
                f"{path}/{name}/常见故障",
                kind="nav",
                module=module,
                label="常见故障",
                knowledge_type="常见故障",
                source=source(document, f"{device_name} / {name} / 常见故障", fault_line),
            )
            for fault_name, fault_at in faults:
                fault_refs = link_named(builder, component_graph_id, "HAS_FAULT", fault_name, module)
                if not fault_refs:
                    builder.stats["component_faults_without_graph"] += 1
                builder.add(
                    group["id"],
                    f"{path}/{name}/常见故障/{fault_name}",
                    kind="knowledge",
                    module=module,
                    label=fault_name,
                    knowledge_type="常见故障",
                    text=fault_name,
                    source=source(document, f"{device_name} / {name} / 常见故障 / {fault_name}", fault_at),
                    graph_refs=fault_refs,
                )


def link_subcomponent(
    builder: Builder,
    parent_graph_id: str | None,
    subtree: dict[str, list[str]],
    name: str,
    module: str,
) -> tuple[list[dict], str | None]:
    if parent_graph_id:
        direct = [
            node_id
            for node_id in builder.graph.children("HAS_COMPONENT", parent_graph_id)
            if builder.graph.label[node_id] == name
        ]
        refs = builder.one_ref(direct, module, name, "同一部件下有多条同名子部件，未自动合并")
        if refs:
            return refs, refs[0]["id"]
        if len(direct) > 1:
            return [], None
    hits = subtree.get(name, [])
    refs = builder.one_ref(hits, module, name, "同一设备下有多条同名子部件，未自动合并")
    if len(refs) == 1:
        return refs, refs[0]["id"]
    if not refs:
        builder.stats["subcomponents_without_graph"] += 1
    return refs, None


def parse_kb02(builder: Builder, parent_id: str, lines: list[str], document: str) -> None:
    module = "KB-02"
    intro, intro_start, intro_end = intro_text(lines)
    module_node = builder.add(
        parent_id,
        module,
        kind="nav",
        module=module,
        label=f"{MODULE_LABEL[module]} {module}",
        knowledge_type="知识模块",
        text=intro,
        source=source(document, MODULE_LABEL[module], intro_start or 1, intro_end),
        note="各故障没有独立的产生机理小节，定义只保留原文段落。",
    )
    if "产生机理" in intro:
        builder.gap(module, "产生机理", "总述提到产生机理，但各故障下没有独立小节，未单独建节点")
    titles = []
    for start, end, title in sections(lines, 2):
        titles.append(title)
        graph_id, reason = fault_mode_id(builder, title)
        if reason:
            builder.gap(module, title, reason)
        refs = [builder.graph.ref(graph_id)] if graph_id else []
        fault = builder.add(
            module_node["id"],
            f"{module}/{title}",
            kind="knowledge",
            module=module,
            label=title,
            knowledge_type="故障模式",
            text=title,
            source=source(document, title, start + 1),
            graph_refs=refs,
        )
        index = start + 1
        while index < end:
            line = lines[index].strip()
            if not line or line.startswith(">"):
                index += 1
                continue
            if line.startswith("英文名"):
                english = after_colon(line)
                if english:
                    builder.add(
                        fault["id"],
                        f"{module}/{title}/英文名称",
                        kind="knowledge",
                        module=module,
                        label=english,
                        knowledge_type="英文名称",
                        text=english,
                        source=source(document, f"{title} / 英文名称", index + 1),
                    )
                index += 1
                continue
            if heading_level(lines[index]) == 3:
                heading = heading_text(lines[index], 3)
                block_end = next(
                    (
                        cursor
                        for cursor in range(index + 1, end)
                        if heading_level(lines[cursor]) in {2, 3}
                    ),
                    end,
                )
                if "频谱" in heading:
                    group = builder.add(
                        fault["id"],
                        f"{module}/{title}/频谱特征",
                        kind="nav",
                        module=module,
                        label="频谱特征",
                        knowledge_type="频谱特征",
                        source=source(document, f"{title} / 频谱特征", index + 1),
                    )
                    parse_feature_bullets(
                        builder,
                        group["id"],
                        module,
                        document,
                        title,
                        f"{module}/{title}/频谱特征",
                        lines,
                        index + 1,
                        block_end,
                        graph_id,
                    )
                else:
                    builder.gap(module, heading, "原文出现未单独建类的小节，未改写成新的故障机理")
                index = block_end
                continue
            buffer = [line]
            start_line = index + 1
            index += 1
            while index < end:
                nxt = lines[index].strip()
                if not nxt or nxt.startswith("#") or nxt.startswith("英文名") or nxt.startswith("•"):
                    break
                buffer.append(nxt)
                index += 1
            definition = "".join(buffer)
            builder.add(
                fault["id"],
                f"{module}/{title}/故障定义",
                kind="knowledge",
                module=module,
                label="故障定义",
                knowledge_type="故障定义",
                text=definition,
                source=source(document, f"{title} / 故障定义", start_line, index),
            )
    builder.headings[module] = titles


def fault_mode_id(builder: Builder, title: str) -> tuple[str | None, str | None]:
    hits = builder.graph.hits("fault", title)
    featured = [node_id for node_id in hits if builder.graph.children("HAS_FEATURE", node_id)]
    if len(featured) == 1:
        return featured[0], None
    if len(featured) > 1:
        return None, "正式图谱中有多条带频谱特征的同名故障，未自动合并"
    if len(hits) == 1:
        return hits[0], "正式图谱中的同名故障没有频谱特征边"
    if not hits:
        return None, "正式图谱中没有同名故障模式"
    return None, "正式图谱中有多条同名故障，未自动合并"


def parse_feature_bullets(
    builder: Builder,
    parent_id: str,
    module: str,
    document: str,
    fault_name: str,
    path: str,
    lines: list[str],
    start: int,
    end: int,
    fault_graph_id: str | None,
) -> None:
    index = start
    while index < end:
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if heading_level(lines[index]) in {2, 3}:
            break
        content = bullet_text(line)
        if content is None:
            index += 1
            continue
        start_line = index + 1
        index += 1
        while index < end:
            nxt = lines[index].strip()
            if not nxt or bullet_text(nxt) is not None or heading_level(lines[index]) in {2, 3}:
                break
            content += nxt
            index += 1
        refs = link_feature(builder, fault_graph_id, content)
        if not refs:
            builder.stats["features_without_graph"] += 1
        builder.add(
            parent_id,
            f"{path}/{content}",
            kind="knowledge",
            module=module,
            label=content,
            knowledge_type="频谱特征",
            text=content,
            source=source(document, f"{fault_name} / 频谱特征", start_line, index),
            graph_refs=refs,
        )


def link_feature(builder: Builder, fault_graph_id: str | None, content: str) -> list[dict]:
    if not fault_graph_id:
        return []
    key = content.split("：", 1)[0].split(":", 1)[0].strip()
    candidates = builder.graph.children("HAS_FEATURE", fault_graph_id)
    exact = [node_id for node_id in candidates if builder.graph.label[node_id] == content]
    if len(exact) == 1:
        return [builder.graph.ref(exact[0])]
    prefix = [
        node_id
        for node_id in candidates
        if builder.graph.label[node_id].startswith(key + "：") or builder.graph.label[node_id].startswith(key + ":")
    ]
    if len(prefix) == 1:
        return [builder.graph.ref(prefix[0])]
    return []


def contained_device(builder: Builder, title: str) -> str | None:
    hits = [
        node_id
        for node_id in builder.graph.by_type["device"]
        if builder.graph.label[node_id] and builder.graph.label[node_id] in title
    ]
    hits.sort(key=lambda node_id: len(builder.graph.label[node_id]), reverse=True)
    if not hits:
        return None
    if len(hits) == 1 or len(builder.graph.label[hits[0]]) > len(builder.graph.label[hits[1]]):
        return hits[0]
    builder.gap("KB-03", title, "规程标题能对应多台正式设备，未自动合并")
    return None


def parse_kb03(builder: Builder, parent_id: str, lines: list[str], document: str) -> None:
    module = "KB-03"
    intro, intro_start, intro_end = intro_text(lines)
    module_node = builder.add(
        parent_id,
        module,
        kind="nav",
        module=module,
        label=f"{MODULE_LABEL[module]} {module}",
        knowledge_type="知识模块",
        text=intro,
        source=source(document, MODULE_LABEL[module], intro_start or 1, intro_end),
    )
    titles = []
    for start, end, title in sections(lines, 2):
        titles.append(title)
        device_graph_id = contained_device(builder, title)
        procedure = builder.add(
            module_node["id"],
            f"{module}/{title}",
            kind="knowledge",
            module=module,
            label=title,
            knowledge_type="操作规程",
            text=title,
            source=source(document, title, start + 1),
            graph_refs=[builder.graph.ref(device_graph_id)] if device_graph_id else [],
        )
        index = start + 1
        while index < end:
            line = lines[index].strip()
            if line.startswith("适用范围"):
                text, start_line, _last, index = read_wrapped(lines, index, end, "适用范围")
                if text:
                    builder.add(
                        procedure["id"],
                        f"{module}/{title}/适用范围",
                        kind="knowledge",
                        module=module,
                        label="适用范围",
                        knowledge_type="适用范围",
                        text=text,
                        source=source(document, f"{title} / 适用范围", start_line, index),
                    )
                else:
                    builder.gap(module, title, "适用范围标题下没有正文")
                continue
            if line.startswith("|"):
                header, rows, index = read_table(lines, index, end)
                if any("参数" in cell for cell in header):
                    parse_parameters(
                        builder,
                        procedure["id"],
                        module,
                        document,
                        f"{title} / 关键参数",
                        f"{module}/{title}/关键参数",
                        rows,
                        "关键参数",
                        device_graph_id,
                    )
                continue
            index += 1
    builder.headings[module] = titles
    builder.gap(module, "操作步骤", "原文没有逐步操作步骤，未编写步骤")


def parse_kb04(builder: Builder, parent_id: str, lines: list[str], document: str) -> None:
    module = "KB-04"
    intro, intro_start, intro_end = intro_text(lines)
    module_node = builder.add(
        parent_id,
        module,
        kind="nav",
        module=module,
        label=f"{MODULE_LABEL[module]} {module}",
        knowledge_type="知识模块",
        text=intro,
        source=source(document, MODULE_LABEL[module], intro_start or 1, intro_end),
        note="案例没有独立的诊断结果字段。只有以“处理：”开头的原文才记为处理记录。",
    )
    builder.gap(module, "诊断结果", "案例表没有诊断结果字段，未生成该节点")
    titles = []
    for start, end, title in sections(lines, 2):
        titles.append(title)
        case_match = re.match(r"(EXP-2024-\d+)", title)
        case_code = case_match.group(1) if case_match else title
        case_hits = [node_id for node_id in builder.graph.by_type["case"] if builder.graph.label[node_id].startswith(case_code)]
        case_refs = builder.one_ref(case_hits, module, case_code, "正式图谱中有多条同编号案例，未自动合并")
        case_graph_id = case_refs[0]["id"] if case_refs else None
        case = builder.add(
            module_node["id"],
            f"{module}/{case_code}",
            kind="knowledge",
            module=module,
            label=title,
            knowledge_type="案例",
            text=title,
            source=source(document, title, start + 1),
            graph_refs=case_refs,
        )
        builder.cases.append(case)
        diagnosis: list[tuple[str, int, int]] = []
        treatments: list[tuple[str, int, int]] = []
        index = start + 1
        while index < end:
            line = lines[index].strip()
            if line.startswith("|"):
                _header, rows, index = read_table(lines, index, end)
                for row in rows:
                    cells = row["cells"]
                    if len(cells) < 2:
                        continue
                    field = cells[0].strip()
                    value = " ".join(cell.strip() for cell in cells[1:] if cell.strip())
                    if field in {"字段", "项目", "属性"} or not value:
                        continue
                    if field == "设备":
                        refs = builder.one_ref(
                            builder.graph.hits("device", value),
                            module,
                            value,
                            "案例所属设备在正式图谱中有多条同名设备，未自动合并",
                        )
                        builder.add(
                            case["id"],
                            f"{module}/{case_code}/所属设备",
                            kind="knowledge",
                            module=module,
                            label=f"所属设备：{value}" if len(value) <= 28 else "所属设备",
                            knowledge_type="所属设备",
                            text=value,
                            source=source(document, f"{title} / 所属设备", row["line"]),
                            graph_refs=refs,
                        )
                    elif field == "概述":
                        builder.add(
                            case["id"],
                            f"{module}/{case_code}/案例概述",
                            kind="knowledge",
                            module=module,
                            label="案例概述",
                            knowledge_type="案例概述",
                            text=value,
                            source=source(document, f"{title} / 案例概述", row["line"]),
                        )
                    else:
                        builder.gap(module, f"{case_code}/{field}", "案例表出现未列入展示类别的字段，未改写")
                continue
            if heading_level(lines[index]) == 3:
                heading = heading_text(lines[index], 3)
                block_end = next(
                    (cursor for cursor in range(index + 1, end) if heading_level(lines[cursor]) in {2, 3}),
                    end,
                )
                if "诊断思路" in heading:
                    diagnosis, treatments = parse_steps(lines, index + 1, block_end)
                elif "经验教训" in heading:
                    paragraph, start_line, last_line = join_paragraph(lines, index + 1, block_end)
                    if paragraph:
                        builder.add(
                            case["id"],
                            f"{module}/{case_code}/经验教训",
                            kind="knowledge",
                            module=module,
                            label="经验教训",
                            knowledge_type="经验教训",
                            text=paragraph,
                            source=source(document, f"{title} / 经验教训", start_line, last_line),
                        )
                    else:
                        builder.gap(module, case_code, "经验教训标题下没有正文")
                else:
                    builder.gap(module, f"{case_code}/{heading}", "案例出现未列入展示类别的小节，未改写")
                index = block_end
                continue
            index += 1
        if diagnosis:
            group = builder.add(
                case["id"],
                f"{module}/{case_code}/诊断思路",
                kind="nav",
                module=module,
                label="诊断思路",
                knowledge_type="诊断思路",
                source=source(document, f"{title} / 诊断思路", diagnosis[0][1], diagnosis[-1][2]),
            )
            for step_index, (text, step_start, step_end) in enumerate(diagnosis, start=1):
                builder.add(
                    group["id"],
                    f"{module}/{case_code}/诊断思路/{step_index}",
                    kind="knowledge",
                    module=module,
                    label=text,
                    knowledge_type="诊断步骤",
                    text=text,
                    source=source(document, f"{title} / 诊断思路", step_start, step_end),
                )
        else:
            builder.gap(module, case_code, "没有可单独列出的诊断步骤")
        if treatments:
            group = builder.add(
                case["id"],
                f"{module}/{case_code}/处理记录",
                kind="nav",
                module=module,
                label="处理记录",
                knowledge_type="处理记录",
                source=source(document, f"{title} / 处理记录", treatments[0][1], treatments[-1][2]),
                note="该内容来自诊断思路中以“处理：”开头的原文，不是另写的处置。",
            )
            for step_index, (text, step_start, step_end) in enumerate(treatments, start=1):
                action_refs = []
                if case_graph_id:
                    action_refs = [
                        builder.graph.ref(node_id)
                        for node_id in builder.graph.children("HAS_ACTION", case_graph_id)
                        if builder.graph.label[node_id] and builder.graph.label[node_id] in text
                    ]
                builder.add(
                    group["id"],
                    f"{module}/{case_code}/处理记录/{step_index}",
                    kind="knowledge",
                    module=module,
                    label=text,
                    knowledge_type="处理记录",
                    text=text,
                    source=source(document, f"{title} / 处理记录", step_start, step_end),
                    graph_refs=action_refs,
                )
        elif case_code in {"EXP-2024-001", "EXP-2024-002", "EXP-2024-003"}:
            builder.gap(module, case_code, "原文没有独立处理字段，诊断建议未记成已经执行的处理")
        builder.case_counts.append(
            {
                "case": case_code,
                "diagnosis_steps": len(diagnosis),
                "treatment_steps": len(treatments),
            }
        )
    builder.headings[module] = titles


def parse_steps(lines: list[str], start: int, end: int) -> tuple[list[tuple[str, int, int]], list[tuple[str, int, int]]]:
    diagnosis: list[tuple[str, int, int]] = []
    treatments: list[tuple[str, int, int]] = []
    index = start
    step_re = re.compile(r"^\d+\s*[\.、．]\s*(.*)$")
    while index < end:
        line = lines[index].strip()
        match = step_re.match(line)
        if not match:
            index += 1
            continue
        text = match.group(1).strip()
        start_line = index + 1
        index += 1
        while index < end:
            nxt = lines[index].strip()
            if not nxt or step_re.match(nxt) or heading_level(lines[index]) in {2, 3}:
                break
            text += nxt
            index += 1
        item = (text, start_line, index)
        if is_treatment(text):
            treatments.append(item)
        else:
            diagnosis.append(item)
    return diagnosis, treatments


def join_paragraph(lines: list[str], start: int, end: int) -> tuple[str, int | None, int | None]:
    buffer: list[str] = []
    start_line = None
    last_line = None
    for index in range(start, end):
        line = lines[index].strip()
        if not line or line.startswith(">") or line.startswith("#"):
            if buffer:
                break
            continue
        if start_line is None:
            start_line = index + 1
        buffer.append(line)
        last_line = index + 1
    return "".join(buffer), start_line, last_line


def parse_kb05(builder: Builder, parent_id: str, lines: list[str], document: str) -> None:
    module = "KB-05"
    intro, intro_start, intro_end = intro_text(lines)
    module_node = builder.add(
        parent_id,
        module,
        kind="nav",
        module=module,
        label=f"{MODULE_LABEL[module]} {module}",
        knowledge_type="知识模块",
        text=intro,
        source=source(document, MODULE_LABEL[module], intro_start or 1, intro_end),
    )
    titles = []
    for start, end, title in sections(lines, 2):
        titles.append(title)
        chain = builder.add(
            module_node["id"],
            f"{module}/{title}",
            kind="knowledge",
            module=module,
            label=title,
            knowledge_type="系统链路",
            text=title,
            source=source(document, title, start + 1),
        )
        saw_faults = False
        index = start + 1
        while index < end:
            line = lines[index].strip()
            if line.startswith("流程链"):
                text, start_line, _last, index = read_wrapped(lines, index, end, "流程链")
                add_flow(builder, chain["id"], module, document, title, text, start_line)
                continue
            if heading_level(lines[index]) == 3:
                heading = heading_text(lines[index], 3)
                block_end = next(
                    (cursor for cursor in range(index + 1, end) if heading_level(lines[cursor]) in {2, 3}),
                    end,
                )
                if "故障" in heading:
                    saw_faults = True
                    add_chain_faults(builder, chain["id"], module, document, title, lines, index + 1, block_end)
                elif "关联" in heading:
                    add_chain_links(builder, chain["id"], module, document, title, lines, index + 1, block_end)
                else:
                    builder.gap(module, f"{title}/{heading}", "系统链出现未列入展示类别的小节，未改写")
                index = block_end
                continue
            index += 1
        if not saw_faults:
            builder.gap(module, title, "原文没有常见关联故障小节，未补充故障")
    builder.headings[module] = titles


def add_flow(
    builder: Builder,
    parent_id: str,
    module: str,
    document: str,
    chain_name: str,
    text: str,
    start_line: int,
) -> None:
    if not text:
        builder.gap(module, chain_name, "流程链没有正文")
        return
    group = builder.add(
        parent_id,
        f"{module}/{chain_name}/流程链",
        kind="knowledge",
        module=module,
        label="流程链",
        knowledge_type="流程链",
        text=text,
        source=source(document, f"{chain_name} / 流程链", start_line),
    )
    parts = [part.strip() for part in re.split(r"\s*(?:→|->|—>)\s*", text) if part.strip()]
    for order, part in enumerate(parts, start=1):
        refs = builder.one_ref(
            builder.graph.hits("device", part),
            module,
            part,
            "流程环节在正式图谱中有多条同名设备，未自动合并",
        )
        builder.add(
            group["id"],
            f"{module}/{chain_name}/流程链/{order}",
            kind="knowledge",
            module=module,
            label=part,
            knowledge_type="流程环节",
            text=part,
            source=source(document, f"{chain_name} / 流程链", start_line),
            graph_refs=refs,
            note="" if refs else "流程名称与正式图谱设备名称不一致时，只保留原文，不合并到别的设备",
        )


def add_chain_links(
    builder: Builder,
    parent_id: str,
    module: str,
    document: str,
    chain_name: str,
    lines: list[str],
    start: int,
    end: int,
) -> None:
    table_at = next((index for index in range(start, end) if lines[index].strip().startswith("|")), None)
    if table_at is None:
        builder.gap(module, chain_name, "关键关联标题下没有表格")
        return
    header, rows, _index = read_table(lines, table_at, end)
    if not rows:
        builder.gap(module, chain_name, "关键关联表没有数据行")
        return
    group = builder.add(
        parent_id,
        f"{module}/{chain_name}/关键关联",
        kind="nav",
        module=module,
        label="关键关联",
        knowledge_type="关键关联",
        source=source(document, f"{chain_name} / 关键关联", table_at + 1),
    )
    upstream_at = next((pos for pos, cell in enumerate(header) if "上游" in cell), 0)
    downstream_at = next((pos for pos, cell in enumerate(header) if "下游" in cell), 1)
    method_at = next((pos for pos, cell in enumerate(header) if "连接" in cell), 2)
    coupling_at = next((pos for pos, cell in enumerate(header) if "耦合" in cell), 3)
    for row_index, row in enumerate(rows, start=1):
        cells = row["cells"]

        def cell(pos: int) -> str:
            return cells[pos].strip() if pos < len(cells) else ""

        upstream = cell(upstream_at)
        downstream = cell(downstream_at)
        method = cell(method_at)
        coupling = cell(coupling_at)
        if not upstream and not downstream:
            continue
        label = f"{upstream} → {downstream}"
        link = builder.add(
            group["id"],
            f"{module}/{chain_name}/关键关联/{row_index}",
            kind="knowledge",
            module=module,
            label=label,
            knowledge_type="关联连接",
            text=label,
            source=source(document, f"{chain_name} / 关键关联", row["line"]),
            graph_refs=link_pair(builder, upstream, downstream, module),
        )
        if method and not empty_value(method):
            builder.add(
                link["id"],
                f"{module}/{chain_name}/关键关联/{row_index}/连接方式",
                kind="knowledge",
                module=module,
                label=f"连接方式：{method}",
                knowledge_type="连接方式",
                text=method,
                source=source(document, f"{chain_name} / {label} / 连接方式", row["line"]),
            )
        else:
            builder.gap(module, label, "原文未填写连接方式")
        if coupling and not empty_value(coupling):
            builder.add(
                link["id"],
                f"{module}/{chain_name}/关键关联/{row_index}/耦合件",
                kind="knowledge",
                module=module,
                label=f"耦合件：{coupling}",
                knowledge_type="耦合件",
                text=coupling,
                source=source(document, f"{chain_name} / {label} / 耦合件", row["line"]),
            )
        else:
            builder.gap(module, label, "原文未填写耦合件")


def link_pair(builder: Builder, upstream: str, downstream: str, module: str) -> list[dict]:
    refs: list[dict] = []
    for name in (upstream, downstream):
        hits = builder.graph.hits("device", name)
        if len(hits) == 1:
            refs.append(builder.graph.ref(hits[0]))
        elif len(hits) > 1:
            builder.gap(module, name, "上下游设备在正式图谱中有多条同名设备，未自动合并")
    return refs


def add_chain_faults(
    builder: Builder,
    parent_id: str,
    module: str,
    document: str,
    chain_name: str,
    lines: list[str],
    start: int,
    end: int,
) -> None:
    items: list[tuple[str, int]] = []
    for index in range(start, end):
        content = bullet_text(lines[index])
        if content:
            items.append((content, index + 1))
    if not items:
        builder.gap(module, chain_name, "常见关联故障标题下没有条目")
        return
    group = builder.add(
        parent_id,
        f"{module}/{chain_name}/常见关联故障",
        kind="nav",
        module=module,
        label="常见关联故障",
        knowledge_type="常见关联故障",
        source=source(document, f"{chain_name} / 常见关联故障", items[0][1]),
    )
    for name, line_no in items:
        hits = builder.graph.hits("fault", name)
        refs = builder.one_ref(hits, module, name, "关联故障在正式图谱中有多条同名故障，未自动合并")
        builder.add(
            group["id"],
            f"{module}/{chain_name}/常见关联故障/{name}",
            kind="knowledge",
            module=module,
            label=name,
            knowledge_type="关联故障",
            text=name,
            source=source(document, f"{chain_name} / 常见关联故障 / {name}", line_no),
            graph_refs=refs,
        )


def case_short_title(label: str) -> str:
    part = label.split("：", 1)[-1]
    for suffix in ("典型案例", "案例"):
        if part.endswith(suffix):
            part = part[: -len(suffix)]
    return part.strip()


def match_case_node(builder: Builder, heading: str) -> dict | None:
    hits = []
    for case in builder.cases:
        short = case_short_title(case["label"])
        if heading == short or heading in case["label"] or (short and short in heading):
            hits.append(case)
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        builder.gap("KB-06", heading, "检索案例对应到多个正式案例，未指定唯一目标")
    else:
        builder.gap("KB-06", heading, "检索案例没有对应到 KB-04 的编号案例")
    return None


def match_alias_case(builder: Builder, heading: str, canonical_graph_id: str | None) -> list[dict]:
    hits = []
    for node_id in builder.graph.by_type["case"]:
        if node_id == canonical_graph_id:
            continue
        label = builder.graph.label[node_id]
        if label.startswith("EXP-2024"):
            continue
        if heading == label or heading in label or label in heading:
            hits.append(builder.graph.ref(node_id))
    if len(hits) > 1:
        builder.gap("KB-06", heading, "正式图谱中有多条检索案例副本，未全部挂接")
        return []
    return hits


def parse_kb06(builder: Builder, parent_id: str, lines: list[str], document: str, chunks: list[dict]) -> None:
    module = "KB-06"
    intro, intro_start, intro_end = intro_text(lines)
    module_node = builder.add(
        parent_id,
        module,
        kind="nav",
        module=module,
        label=f"{MODULE_LABEL[module]} {module}",
        knowledge_type="知识模块",
        text=intro,
        source=source(document, MODULE_LABEL[module], intro_start or 1, intro_end),
        note="这里是已有知识的检索视图。重复出现的设备和案例不作为新的事实。",
    )
    device_group = builder.add(
        module_node["id"],
        f"{module}/设备知识摘要",
        kind="nav",
        module=module,
        label="设备知识摘要",
        knowledge_type="检索分组",
        source=source(document, "设备知识摘要", intro_start or 1),
    )
    case_group = builder.add(
        module_node["id"],
        f"{module}/典型案例摘要",
        kind="nav",
        module=module,
        label="典型案例摘要",
        knowledge_type="检索分组",
        source=source(document, "典型案例摘要", intro_start or 1),
    )
    titles = []
    for start, end, title in sections(lines, 2):
        titles.append(title)
        target_group = device_group if "设备" in title else case_group if "案例" in title else None
        if target_group is None:
            builder.gap(module, title, "出现未列入检索视图的大节，未改写成新知识")
            continue
        for sub_start, sub_end, sub_title in sections(lines, 3, start + 1, end):
            fields, field_lines = read_fields(lines, sub_start + 1, sub_end)
            summary_lines = []
            parameters = []
            for key, value in fields:
                if key in {"来源", "标签"}:
                    continue
                summary_lines.append(f"{key}：{value}")
                parameters.append({"name": key, "value": value})
            if "设备" in title:
                canonical = builder.devices.get(sub_title)
                if canonical is None:
                    for device_name, device in builder.devices.items():
                        if device_name in sub_title or sub_title in device_name:
                            canonical = device
                            break
                if canonical is None:
                    builder.gap(module, sub_title, "设备摘要没有对应到 KB-01 设备")
                canonical_id = canonical["id"] if canonical else None
                graph_refs = list(canonical["graph_refs"]) if canonical else []
                node = builder.add(
                    target_group["id"],
                    f"{module}/设备/{sub_title}",
                    kind="reference",
                    module=module,
                    label=canonical["label"] if canonical else sub_title,
                    knowledge_type="设备摘要",
                    text="\n".join(summary_lines),
                    parameters=parameters,
                    source=source(document, f"{title} / {sub_title}", sub_start + 1),
                    graph_refs=graph_refs,
                    canonical_id=canonical_id,
                    note="检索摘要，对应已有设备，不是新建设备。",
                )
            else:
                canonical = match_case_node(builder, sub_title)
                canonical_id = canonical["id"] if canonical else None
                graph_refs = list(canonical["graph_refs"]) if canonical else []
                alias_refs = match_alias_case(
                    builder,
                    sub_title,
                    graph_refs[0]["id"] if graph_refs else None,
                )
                node = builder.add(
                    target_group["id"],
                    f"{module}/案例/{sub_title}",
                    kind="reference",
                    module=module,
                    label=sub_title,
                    knowledge_type="案例引用",
                    text="\n".join(summary_lines),
                    parameters=parameters,
                    source=source(document, f"{title} / {sub_title}", sub_start + 1),
                    graph_refs=graph_refs,
                    alias_graph_refs=alias_refs,
                    canonical_id=canonical_id,
                    note="检索摘要，对应已有编号案例，不是新的案例事实。",
                )
            for key in ("来源", "标签"):
                value = next((item_value for item_key, item_value in fields if item_key == key), "")
                line_no = field_lines.get(key, sub_start + 1)
                if not value:
                    if key == "标签":
                        builder.gap(module, "标签", "各检索摘要写了来源，没有单独的标签字段，未生成标签节点")
                    else:
                        builder.gap(module, f"{sub_title}/{key}", "检索摘要没有来源")
                    continue
                builder.add(
                    node["id"],
                    f"{module}/{'设备' if '设备' in title else '案例'}/{sub_title}/{key}",
                    kind="reference",
                    module=module,
                    label=f"{key}：{value}" if len(value) <= 28 else key,
                    knowledge_type=key,
                    text=value,
                    source=source(document, f"{sub_title} / {key}", line_no),
                    canonical_id=node.get("canonical_id"),
                    note="检索来源信息，不新增知识事实。",
                )
    builder.headings[module] = titles
    add_chunks(builder, module_node["id"], document, chunks)


def read_fields(lines: list[str], start: int, end: int) -> tuple[list[tuple[str, str]], dict[str, int]]:
    fields: list[tuple[str, str]] = []
    lines_by_key: dict[str, int] = {}
    index = start
    while index < end:
        line = lines[index].strip()
        if not line or line.startswith("#") or line.startswith(">"):
            index += 1
            continue
        content = bullet_text(line) or line
        match = re.match(r"^([^\s：:]{1,16})\s*[：:](.*)$", content)
        if not match:
            if fields:
                key, value = fields[-1]
                fields[-1] = (key, value + content)
            index += 1
            continue
        key = match.group(1).strip()
        value = match.group(2).strip()
        index += 1
        while index < end:
            nxt = lines[index].strip()
            if not nxt or nxt.startswith("#"):
                break
            nxt_content = bullet_text(nxt) or nxt
            if re.match(r"^([^\s：:]{1,16})\s*[：:]", nxt_content):
                break
            value += nxt_content
            index += 1
        fields.append((key, value))
        lines_by_key[key] = index
    return fields, lines_by_key


def add_chunks(builder: Builder, parent_id: str, fallback_document: str, chunks: list[dict]) -> None:
    group = builder.add(
        parent_id,
        "KB-06/可检索知识条目",
        kind="nav",
        module="KB-06",
        label="可检索知识条目",
        knowledge_type="检索分组",
        source=source(fallback_document, "可检索知识条目", 1),
        note="条目来自本地检索索引，只作为查找入口。",
    )
    documents: dict[str, dict] = {}
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        chunk_id = str(chunk.get("id") or "")
        chunk_document = str(chunk.get("document") or "")
        section = str(chunk.get("section") or "")
        chunk_locator = str(chunk.get("locator") or "")
        text = str(chunk.get("text") or "").strip()
        if not chunk_id or not text:
            builder.gap("KB-06", chunk_id or "空白检索条目", "检索条目缺少编号或正文，未展示")
            continue
        code = next((item for item in MODULE_LABEL if item in chunk_document or item in section), "KB-06")
        doc_group = documents.get(code)
        if doc_group is None:
            doc_group = builder.add(
                group["id"],
                f"KB-06/可检索知识条目/{code}",
                kind="nav",
                module="KB-06",
                label=f"{code} 检索条目",
                knowledge_type="检索分组",
                source={"document": chunk_document or fallback_document, "section": code, "locator": chunk_locator},
            )
            documents[code] = doc_group
        tail = section.split(" / ")[-1].strip() if section else chunk_id
        tail_name, _subs = split_name(tail.lstrip("-").lstrip("▎").strip())
        canonical = match_chunk(builder, section, tail_name)
        builder.add(
            doc_group["id"],
            f"KB-06/可检索知识条目/{code}/{chunk_id}",
            kind="reference",
            module="KB-06",
            label=tail_name or "检索条目",
            knowledge_type="检索条目",
            text=text,
            source={
                "document": chunk_document or fallback_document,
                "section": section,
                "locator": chunk_locator or "未记录行号",
            },
            canonical_id=canonical,
            note="检索索引中的已有内容，不新增案例、设备或故障事实。",
        )


def walked_depth(by_id: dict[str, dict], node: dict) -> int:
    depth = 0
    current = node
    seen: set[str] = set()
    while current["parent_id"]:
        if current["id"] in seen:
            break
        seen.add(current["id"])
        parent = by_id.get(current["parent_id"])
        if parent is None:
            break
        current = parent
        depth += 1
    return depth


def match_chunk(builder: Builder, section: str, tail: str) -> str | None:
    if not tail:
        return None
    hits = [node for node in builder.nodes if node["kind"] != "reference" and node["label"] == tail]
    if not hits:
        fallback = match_chunk_fallback(builder, section, tail)
        if fallback:
            return fallback
        builder.stats["chunks_without_target"] += 1
        return None
    by_id = {node["id"]: node for node in builder.nodes}

    def supported(node: dict) -> bool:
        current = node
        seen: set[str] = set()
        while current["parent_id"]:
            if current["id"] in seen:
                return False
            seen.add(current["id"])
            parent = by_id[current["parent_id"]]
            if (
                parent["parent_id"]
                and parent["knowledge_type"] not in {"知识模块", "检索分组"}
                and parent["label"] not in section
            ):
                return False
            current = parent
        return True

    filtered = [node for node in hits if supported(node)] or hits
    ranked = sorted(((walked_depth(by_id, node), node) for node in filtered), key=lambda item: item[0], reverse=True)
    if len(ranked) == 1 or ranked[0][0] > ranked[1][0]:
        return ranked[0][1]["id"]
    builder.stats["chunks_ambiguous"] += 1
    builder.gap("KB-06", tail, "检索条目对应多个同名知识，未指定唯一目标")
    return None


def match_chunk_fallback(builder: Builder, section: str, tail: str) -> str | None:
    if " - " in tail:
        head = tail.split(" - ", 1)[0].strip()
        named = [node for node in builder.nodes if node["kind"] != "reference" and node["label"] == head]
        if len(named) == 1:
            return named[0]["id"]
    for case in builder.cases:
        short = case_short_title(case["label"])
        if short and (tail == short or tail == f"{short}典型案例" or tail == f"{short}案例" or short in tail):
            return case["id"]
    for code, _name in MODULES:
        if tail.startswith(code):
            module = next(
                (
                    node
                    for node in builder.nodes
                    if node["module"] == code and node["knowledge_type"] == "知识模块"
                ),
                None,
            )
            if module:
                return module["id"]
    return None


def build() -> dict:
    markdowns = list(MARKDOWN_DIR.glob("KB-*.md"))
    if len(markdowns) != 6:
        raise RuntimeError(f"官方 Markdown 应为 6 份，实际 {len(markdowns)} 份")
    graph_bytes = GRAPH_FILE.read_bytes()
    graph_hash = sha256_bytes(graph_bytes)
    graph_payload = json.loads(graph_bytes.decode("utf-8"))
    if not isinstance(graph_payload.get("nodes"), list) or not isinstance(graph_payload.get("edges"), list):
        raise RuntimeError("graph.json 缺少 nodes 或 edges")
    graph = GraphIndex(graph_payload)
    index_payload = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    chunks = index_payload.get("chunks")
    if not isinstance(chunks, list):
        raise RuntimeError("检索索引缺少 chunks")
    builder = Builder(graph)
    root = builder.add(
        None,
        "root",
        kind="nav",
        module="",
        label="钢铁设备故障知识库",
        knowledge_type="知识库",
        note="导航父子关系只用于逐层阅读，不是正式工业关系。",
    )
    parsers = {
        "KB-01": parse_kb01,
        "KB-02": parse_kb02,
        "KB-03": parse_kb03,
        "KB-04": parse_kb04,
        "KB-05": parse_kb05,
    }
    documents: dict[str, str] = {}
    for code, _name in MODULES:
        path = find_markdown(code)
        text = path.read_text(encoding="utf-8")
        documents[code] = document_name(text, path.name)
        lines = text.splitlines()
        if code == "KB-06":
            continue
        parsers[code](builder, root["id"], lines, documents[code])
    kb06_path = find_markdown("KB-06")
    kb06_text = kb06_path.read_text(encoding="utf-8")
    documents["KB-06"] = document_name(kb06_text, kb06_path.name)
    parse_kb06(builder, root["id"], kb06_text.splitlines(), documents["KB-06"], chunks)
    builder.finish()
    if GRAPH_FILE.read_bytes() != graph_bytes:
        raise RuntimeError("生成过程中 graph.json 被改动，已停止")
    failures = validate(builder, graph_payload, graph_hash, graph_bytes)
    payload = {
        "schema": "knowledge-view-1",
        "description": "展示导航。父子关系不是正式工业关系，正式关系仍在 graph.json。",
        "root_id": root["id"],
        "nodes": builder.nodes,
        "gaps": builder.gaps,
    }
    VIEW_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = make_report(builder, graph, graph_payload, graph_hash, failures, len(chunks))
    REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if GRAPH_FILE.read_bytes() != graph_bytes:
        raise RuntimeError("写出展示文件后 graph.json 发生变化")
    print(
        "nav={nav} knowledge={knowledge} reference={reference} total={total} "
        "graph={nodes}/{edges} unchanged=1 failures={failures}".format(
            nav=report["counts"]["nav"],
            knowledge=report["counts"]["knowledge"],
            reference=report["counts"]["reference"],
            total=report["counts"]["total"],
            nodes=len(graph_payload["nodes"]),
            edges=len(graph_payload["edges"]),
            failures=len(failures),
        )
    )
    if failures:
        for failure in failures:
            print("FAIL " + failure)
        raise SystemExit(1)
    return report


def validate(builder: Builder, graph_payload: dict, graph_hash: str, graph_bytes: bytes) -> list[str]:
    failures: list[str] = []
    if len(graph_payload["nodes"]) != 252 or len(graph_payload["edges"]) != 464:
        failures.append(
            f"正式图谱不是 252/464，而是 {len(graph_payload['nodes'])}/{len(graph_payload['edges'])}"
        )
    if sha256_bytes(graph_bytes) != graph_hash:
        failures.append("graph.json 哈希发生变化")
    ids = [node["id"] for node in builder.nodes]
    if len(ids) != len(set(ids)):
        failures.append("展示节点编号重复")
    labels = {node["label"] for node in builder.nodes}
    for name in (
        "设备知识 KB-01",
        "故障模式 KB-02",
        "操作规程 KB-03",
        "专家经验与典型案例 KB-04",
        "设备关联关系 KB-05",
        "RAG检索知识 KB-06",
        "钢铁设备故障知识库",
    ):
        if name not in labels:
            failures.append("缺少节点 " + name)
    for device in ("棒线主轧机电机", "棒线主减速机/齿轮箱", "棒线冷却水循环泵"):
        if device not in labels:
            failures.append("缺少设备 " + device)
    for fault in ("轴承磨损", "转子不平衡", "齿轮异常", "润滑故障"):
        if fault not in builder.headings.get("KB-02", []):
            failures.append("缺少故障模式 " + fault)
    for chain in ("棒材主传动链", "棒材冷却水回路", "棒材稀油润滑系统"):
        if chain not in builder.headings.get("KB-05", []):
            failures.append("缺少系统链 " + chain)
    procedures = builder.headings.get("KB-03", [])
    if len(procedures) != 3:
        failures.append("操作规程不是 3 条")
    for needle in ("电机", "齿轮", "泵"):
        if not any(needle in title for title in procedures):
            failures.append("操作规程标题没有包含 " + needle)
    case_codes = []
    for case in builder.cases:
        match = re.match(r"EXP-2024-(\d+)", case["label"])
        if match:
            case_codes.append(match.group(1))
    if sorted(case_codes) != [f"{index:03d}" for index in range(1, 8)]:
        failures.append("七个案例编号不完整")
    forbidden = {"产生机理", "诊断结果"}
    for node in builder.nodes:
        if node["knowledge_type"] in forbidden or node["label"] in forbidden:
            failures.append("生成了原文没有的节点 " + node["label"])
        if node["parent_id"] is None:
            continue
        source_data = node["source"]
        if not isinstance(source_data, dict) or not source_data.get("document") or not source_data.get("locator"):
            failures.append("节点缺少来源 " + node["label"])
    by_id = {node["id"]: node for node in builder.nodes}
    for case in builder.cases:
        code_match = re.match(r"(EXP-2024-\d+)", case["label"])
        code = code_match.group(1) if code_match else ""
        treatment = [
            node
            for node in builder.descendants(case["id"])
            if node["knowledge_type"] == "处理记录" and node["kind"] == "knowledge"
        ]
        if code in {"EXP-2024-001", "EXP-2024-002", "EXP-2024-003"} and treatment:
            failures.append(code + " 不应有处理记录")
        if code in {"EXP-2024-004", "EXP-2024-005", "EXP-2024-006", "EXP-2024-007"} and not treatment:
            failures.append(code + " 原文处理句没有被识别")
        if not case["graph_refs"]:
            failures.append(code + " 没有对应正式图谱案例")
        child_types = {node["knowledge_type"] for node in builder.children_of(case["id"])}
        for required in ("所属设备", "案例概述", "诊断思路", "经验教训"):
            if required not in child_types:
                failures.append(code + " 缺少 " + required)
    for device_name in ("棒线主轧机电机", "棒线主减速机/齿轮箱", "棒线冷却水循环泵"):
        device = builder.devices[device_name]
        child_labels = {node["label"] for node in builder.children_of(device["id"])}
        if "基本参数" not in child_labels or "部件结构" not in child_labels:
            failures.append(device_name + " 缺少参数或部件结构")
        if not device["graph_refs"]:
            failures.append(device_name + " 没有对应正式图谱设备")
    for title in ("轴承磨损", "转子不平衡", "齿轮异常", "润滑故障"):
        fault = next(
            node
            for node in builder.nodes
            if node["module"] == "KB-02" and node["label"] == title and node["knowledge_type"] == "故障模式"
        )
        features = [
            node
            for node in builder.descendants(fault["id"])
            if node["knowledge_type"] == "频谱特征" and node["kind"] == "knowledge"
        ]
        if len(features) < 3:
            failures.append(title + " 的频谱特征不足")
        if not fault["graph_refs"]:
            failures.append(title + " 没有对应正式图谱故障")
    blob = "\n".join(node["label"] + "\n" + node["text"] for node in builder.nodes)
    for token in ("BPFO", "BPFI", "BSF", "1X", "2X", "峭度", "峰值因子"):
        if token not in blob:
            failures.append("展示内容缺少原文标记 " + token)
    references = [node for node in builder.nodes if node["knowledge_type"] == "案例引用"]
    if len(references) != 7:
        failures.append(f"案例引用不是 7 条，而是 {len(references)}")
    for node in references:
        if not node["canonical_id"] or node["canonical_id"] not in by_id:
            failures.append("案例引用没有指向正式案例 " + node["label"])
        if node["kind"] != "reference":
            failures.append("案例引用种类错误 " + node["label"])
    device_refs = [node for node in builder.nodes if node["knowledge_type"] == "设备摘要"]
    if len(device_refs) != 3:
        failures.append(f"设备摘要不是 3 条，而是 {len(device_refs)}")
    chains = {
        node["label"]: node
        for node in builder.nodes
        if node["knowledge_type"] == "系统链路"
    }
    for chain_name in ("棒材冷却水回路", "棒材稀油润滑系统"):
        if any(node["knowledge_type"] == "关联故障" for node in builder.descendants(chains[chain_name]["id"])):
            failures.append(chain_name + " 被补充了原文没有的关联故障")
    if not any(node["knowledge_type"] == "关联故障" for node in builder.descendants(chains["棒材主传动链"]["id"])):
        failures.append("棒材主传动链缺少原文中的关联故障")
    root_children = [node for node in builder.nodes if node["parent_id"] == builder.nodes[0]["id"]]
    if len(root_children) != 6:
        failures.append("总入口下不是 6 个模块")
    return failures


def make_report(
    builder: Builder,
    graph: GraphIndex,
    graph_payload: dict,
    graph_hash: str,
    failures: list[str],
    chunk_count: int,
) -> dict:
    counts = {"nav": 0, "knowledge": 0, "reference": 0, "total": len(builder.nodes)}
    by_module: dict[str, dict[str, int]] = defaultdict(lambda: {"nav": 0, "knowledge": 0, "reference": 0})
    depths: dict[str, int] = {code: 1 for code, _name in MODULES}
    for node in builder.nodes:
        counts[str(node["kind"])] += 1
        if node["module"]:
            by_module[node["module"]][str(node["kind"])] += 1
            depths[node["module"]] = max(depths[node["module"]], int(node["depth"]))
    linked_ids: set[str] = set()
    for node in builder.nodes:
        for ref in node["graph_refs"]:
            linked_ids.add(ref["id"])
        for ref in node["alias_graph_refs"]:
            linked_ids.add(ref["id"])
    unlinked: dict[str, int] = defaultdict(int)
    for node_id, type_name in graph.ntype.items():
        if node_id not in linked_ids:
            unlinked[type_name] += 1
    knowledge_without_graph = [
        node["label"]
        for node in builder.nodes
        if node["kind"] == "knowledge" and node["knowledge_type"] in {"设备", "部件", "故障模式", "案例"} and not node["graph_refs"]
    ]
    outline = [
        {"depth": node["depth"], "kind": node["kind"], "module": node["module"], "type": node["knowledge_type"], "label": node["label"]}
        for node in builder.nodes
        if node["depth"] <= 2 and node["knowledge_type"] != "检索条目"
    ]
    return {
        "counts": counts,
        "by_module": by_module,
        "max_depth": {
            code: {"absolute": depths[code], "below_module": depths[code] - 1}
            for code, _name in MODULES
        },
        "graph_file": {
            "nodes": len(graph_payload["nodes"]),
            "edges": len(graph_payload["edges"]),
            "sha256": graph_hash,
            "unchanged": True,
            "relations": sorted(graph.relations),
            "edge_extra_keys": sorted(graph.extra_keys),
            "pending_mentions": graph_payload and json.dumps(graph_payload, ensure_ascii=False).count("待审核"),
        },
        "linked_graph_nodes": len(linked_ids),
        "unlinked_graph_by_type": unlinked,
        "knowledge_without_graph_ref": knowledge_without_graph,
        "stats": builder.stats,
        "headings": builder.headings,
        "limit_rows": builder.limit_rows,
        "case_counts": builder.case_counts,
        "chunk_count": chunk_count,
        "gaps": builder.gaps,
        "outline": outline,
        "failures": failures,
    }


if __name__ == "__main__":
    build()
