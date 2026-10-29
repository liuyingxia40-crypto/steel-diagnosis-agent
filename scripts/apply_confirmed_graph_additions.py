"""把审核中能够直接对应原文的参数和案例处置写入正式图谱。

不修改 Word、Markdown 和待审核候选。
不调用 DeepSeek，不合并未确认的案例，也不批准 74 条旧候选。
设备对应存在歧义时跳过。重复运行不会重复添加。
校验失败时恢复本轮开始前的 graph.json。
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
BACKUP_PATH = GRAPH_DIR / "graph.json.bak"
ORIGINAL_PATH = GRAPH_DIR / "graph.before-parameter-update.json"
CANDIDATE_PATH = GRAPH_DIR / "review_candidates.json"
PROPOSAL_PATH = GRAPH_DIR / "proposed_additions.json"
REPORT_PATH = GRAPH_DIR / "graph_update_report.md"

DOCUMENTS = [
    "KB-01_设备知识图谱.md",
    "KB-02_故障模式诊断知识库.md",
    "KB-03_操作规程与技术手册.md",
    "KB-04_专家经验与典型案例.md",
    "KB-05_设备关联关系.md",
    "KB-06_RAG检索知识库.md",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_id(prefix: str, *parts: str) -> str:
    raw = "\n".join(parts)
    return prefix + "-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def heading(line: str) -> tuple[int, str] | None:
    match = re.match(r"^(#{1,6})\s+(.*\S)\s*$", line)
    if not match:
        return None
    return len(match.group(1)), match.group(2).strip()


def section_at(lines: list[str], line_no: int) -> str:
    stack: list[tuple[int, str]] = []
    for index, line in enumerate(lines, 1):
        found = heading(line)
        if found:
            level, title = found
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
        if index == line_no:
            return " / ".join(title for _, title in stack)
    return ""


def load_markdown() -> dict[str, list[str]]:
    return {name: (MARKDOWN_DIR / name).read_text(encoding="utf-8").splitlines() for name in DOCUMENTS}


def document_name(ref: dict) -> str:
    return str(ref["document"]).replace(".docx", ".md")


def line_number(ref: dict) -> int:
    match = re.search(r"第(\d+)行", str(ref.get("locator", "")))
    if not match:
        raise ValueError("来源定位缺少行号")
    return int(match.group(1))


def source_line(markdown: dict[str, list[str]], ref: dict) -> tuple[str, int, str]:
    name = document_name(ref)
    number = line_number(ref)
    lines = markdown[name]
    if number < 1 or number > len(lines):
        raise ValueError("来源行号超出文档范围")
    return name, number, lines[number - 1].strip()


def unit_from_label(label: str) -> str:
    match = re.search(r"\(([^()]+)\)\s*$", label)
    return match.group(1).strip() if match else ""


def applicability_for(markdown: dict[str, list[str]], ref: dict) -> str:
    name = document_name(ref)
    lines = markdown[name]
    number = line_number(ref)
    section = section_at(lines, number)
    procedure = ""
    for part in section.split(" / "):
        if part.endswith("操作规程"):
            procedure = part
    if not procedure:
        return ""
    for index, line in enumerate(lines, 1):
        if not line.startswith("适用范围："):
            continue
        if procedure in section_at(lines, index):
            return line.split("：", 1)[1].strip()
    return ""


def nodes_of(graph_nodes: list[dict], node_type: str, label: str) -> list[dict]:
    return [
        node
        for node in graph_nodes
        if node["data"]["type"] == node_type and node["data"]["label"] == label
    ]


def alias_partners(candidates: list[dict], label: str) -> list[str]:
    partners = []
    for item in candidates:
        if item.get("reason_code") != "possible_device_alias":
            continue
        if item.get("from_label") == label:
            partners.append(item.get("to_label", ""))
        elif item.get("to_label") == label:
            partners.append(item.get("from_label", ""))
    return [item for item in partners if item]


def rated_type_value(proposal_nodes: list[dict], device_label: str) -> str:
    for node in proposal_nodes:
        attributes = node.get("attributes", {})
        if node.get("label") != "设备类型" or attributes.get("kind") != "rated_parameter":
            continue
        for ref in node.get("source_refs", []):
            if device_label and device_label in str(ref.get("section", "")):
                return str(attributes.get("value", ""))
    return ""


def parameter_skip_reason(
    device_label: str,
    kind: str,
    applicability: str,
    candidates: list[dict],
    proposal_nodes: list[dict],
) -> str:
    if kind != "operating_limit":
        return ""
    for partner in alias_partners(candidates, device_label):
        partner_type = rated_type_value(proposal_nodes, partner)
        if partner_type and partner_type in applicability:
            return (
                f"操作限值标题对应设备“{device_label}”，但适用范围中的“{partner_type}”"
                f"与另一台尚未确认是否相同的设备“{partner}”的设备类型一致，因此不自动挂接。"
            )
    return ""


def same_ref(left: dict, right: dict) -> bool:
    return (
        left.get("document") == right.get("document")
        and left.get("section") == right.get("section")
        and left.get("locator") == right.get("locator")
    )


def validate(graph: dict, markdown: dict[str, list[str]]) -> list[str]:
    errors = []
    nodes = graph["nodes"]
    edges = graph["edges"]
    ids = [node["id"] for node in nodes]
    if len(ids) != len(set(ids)):
        errors.append("节点编号存在重复。")
    edge_ids = [edge["id"] for edge in edges]
    if len(edge_ids) != len(set(edge_ids)):
        errors.append("关系编号存在重复。")
    node_ids = set(ids)
    seen_edges = set()
    for edge in edges:
        key = (edge["source"], edge["target"], edge["data"]["relation"])
        if key in seen_edges:
            errors.append("存在重复关系。")
            break
        seen_edges.add(key)
        if edge["source"] not in node_ids or edge["target"] not in node_ids:
            errors.append("存在找不到端点的关系。")
            break
        if not edge["data"].get("relation") or not edge["data"].get("source_refs"):
            errors.append("存在缺少关系名或来源的边。")
            break
    for node in nodes:
        data = node["data"]
        if not data.get("label") or not data.get("type") or not data.get("source_refs"):
            errors.append("存在缺少名称、类型或来源的节点。")
            break
        if data["type"] == "parameter":
            if not str(data.get("value", "")).strip():
                errors.append("参数节点出现空值。")
                break
            ref = data["source_refs"][0]
            try:
                _name, _number, text = source_line(markdown, ref)
            except (KeyError, ValueError):
                errors.append("参数来源无法定位。")
                break
            if data["label"] not in text or str(data["value"]) not in text:
                errors.append("参数名称或原始数值不在来源行中。")
                break
            if data.get("unit") and data["unit"] not in data["label"]:
                errors.append("参数单位不是从原参数名中原样取出。")
                break
    cause_count = sum(1 for node in nodes if node["data"]["type"] == "cause")
    if cause_count != 0:
        errors.append("更新意外写入了故障原因节点。")
    return errors


def main() -> None:
    markdown_hashes = {name: sha256(MARKDOWN_DIR / name) for name in DOCUMENTS}
    candidate_hash = sha256(CANDIDATE_PATH)
    original_bytes = GRAPH_PATH.read_bytes()
    BACKUP_PATH.write_bytes(original_bytes)
    if not ORIGINAL_PATH.exists():
        ORIGINAL_PATH.write_bytes(original_bytes)

    graph = json.loads(original_bytes.decode("utf-8"))
    proposal = json.loads(PROPOSAL_PATH.read_text(encoding="utf-8"))
    candidates = json.loads(CANDIDATE_PATH.read_text(encoding="utf-8"))
    markdown = load_markdown()
    before_nodes = len(graph["nodes"])
    before_edges = len(graph["edges"])
    proposal_nodes = {node["id"]: node for node in proposal["nodes"]}
    accepted: list[dict] = []
    skipped: list[dict] = []

    for edge in proposal["edges"]:
        relation = edge["proposed_relation"]
        if edge.get("review_status") != "explicit":
            skipped.append({"item": edge["id"], "relation": relation, "reason": "建议状态不是原文直接支持。"})
            continue
        if relation == "HAS_PARAMETER":
            decision = check_parameter(edge, proposal_nodes, graph["nodes"], candidates, markdown)
        elif relation == "HAS_ACTION":
            decision = check_action(edge, graph["nodes"], markdown)
        else:
            decision = {"skip": f"本次不处理关系 {relation}。"}
        if decision.get("skip"):
            skipped.append(
                {
                    "item": edge.get("to_label", edge["id"]),
                    "relation": relation,
                    "from_label": edge.get("from_label", ""),
                    "reason": decision["skip"],
                }
            )
            continue
        accepted.append(decision)

    new_nodes, new_edges, already_nodes, already_edges = merge(graph, accepted)
    errors = validate(graph, markdown)
    if errors:
        GRAPH_PATH.write_bytes(original_bytes)
        write_report(
            before_nodes,
            before_edges,
            before_nodes,
            before_edges,
            [],
            [],
            skipped,
            accepted,
            errors,
            restored=True,
        )
        raise RuntimeError("；".join(errors))

    GRAPH_PATH.write_text(json.dumps(graph, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    reread = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    errors = validate(reread, markdown)
    if errors or sha256(CANDIDATE_PATH) != candidate_hash:
        GRAPH_PATH.write_bytes(original_bytes)
        write_report(
            before_nodes,
            before_edges,
            before_nodes,
            before_edges,
            [],
            [],
            skipped,
            accepted,
            errors or ["待审核候选文件被改动。"],
            restored=True,
        )
        raise RuntimeError("写盘后的校验失败，已恢复备份。")
    for name, digest in markdown_hashes.items():
        if sha256(MARKDOWN_DIR / name) != digest:
            GRAPH_PATH.write_bytes(original_bytes)
            raise RuntimeError("Markdown 被改动，已恢复图谱备份。")
    write_report(
        before_nodes,
        before_edges,
        len(reread["nodes"]),
        len(reread["edges"]),
        new_nodes,
        new_edges,
        skipped,
        accepted,
        [],
        restored=False,
        already_nodes=already_nodes,
        already_edges=already_edges,
        graph=reread,
        markdown=markdown,
    )
    print(
        json.dumps(
            {
                "before_nodes": before_nodes,
                "before_edges": before_edges,
                "added_nodes": len(new_nodes),
                "added_edges": len(new_edges),
                "skipped": len(skipped),
                "after_nodes": len(reread["nodes"]),
                "after_edges": len(reread["edges"]),
                "already_nodes": already_nodes,
                "already_edges": already_edges,
            },
            ensure_ascii=False,
        )
    )


def check_parameter(
    edge: dict,
    proposal_nodes: dict[str, dict],
    graph_nodes: list[dict],
    candidates: list[dict],
    markdown: dict[str, list[str]],
) -> dict:
    proposal_node = proposal_nodes.get(edge["to_id"])
    if proposal_node is None:
        return {"skip": "参数关系找不到对应的建议节点。"}
    if proposal_node.get("review_status") != "explicit":
        return {"skip": "参数节点不是原文直接支持。"}
    label = str(proposal_node["label"]).strip()
    value = str(proposal_node["attributes"].get("value", ""))
    kind = str(proposal_node["attributes"].get("kind", ""))
    device_label = str(edge["from_label"]).strip()
    if not label or not value.strip():
        return {"skip": "参数名为空或原始数值为空，不写入。"}
    if kind not in {"rated_parameter", "operating_limit"}:
        return {"skip": "参数类别无法确认。"}
    refs = proposal_node.get("source_refs") or []
    if len(refs) != 1:
        return {"skip": "参数来源不是唯一的一行原文。"}
    ref = refs[0]
    try:
        _name, number, text = source_line(markdown, ref)
        actual_section = section_at(markdown[document_name(ref)], number)
    except (KeyError, ValueError) as exc:
        return {"skip": f"参数来源无法定位：{exc}"}
    if actual_section != ref.get("section"):
        return {"skip": "参数来源章节与原文标题不一致。"}
    if label not in text or value not in text:
        return {"skip": "来源行没有同时包含参数名和原始数值。"}
    devices = nodes_of(graph_nodes, "device", device_label)
    if len(devices) != 1:
        return {"skip": f"设备“{device_label}”在正式图谱中不是唯一节点。"}
    if device_label not in actual_section and device_label + "操作规程" not in actual_section:
        return {"skip": "来源章节没有明确写出该设备名称。"}
    applicability = applicability_for(markdown, ref)
    reason = parameter_skip_reason(device_label, kind, applicability, candidates, list(proposal_nodes.values()))
    if reason:
        return {"skip": reason}
    unit = unit_from_label(label)
    parameter_id = stable_id("parameter", device_label, kind, label)
    return {
        "node": {
            "id": parameter_id,
            "data": {
                "label": label,
                "type": "parameter",
                "value": value,
                "unit": unit,
                "kind": kind,
                "device_label": device_label,
                "applicability": applicability,
                "source_refs": [ref],
            },
        },
        "edge": {
            "id": stable_id("edge", "HAS_PARAMETER", devices[0]["id"], parameter_id),
            "source": devices[0]["id"],
            "target": parameter_id,
            "data": {"relation": "HAS_PARAMETER", "source_refs": [ref]},
        },
        "verified": {
            "relation": "HAS_PARAMETER",
            "label": label,
            "device": device_label,
            "value": value,
            "unit": unit,
            "applicability": applicability,
            "source": ref,
            "source_checked": True,
        },
    }


def check_action(edge: dict, graph_nodes: list[dict], markdown: dict[str, list[str]]) -> dict:
    case_label = str(edge["from_label"]).strip()
    action_label = str(edge["to_label"]).strip()
    refs = edge.get("source_refs") or []
    if len(refs) != 1:
        return {"skip": "处置来源不是唯一的一行原文。"}
    ref = refs[0]
    try:
        _name, number, text = source_line(markdown, ref)
        section = section_at(markdown[document_name(ref)], number)
    except (KeyError, ValueError) as exc:
        return {"skip": f"处置来源无法定位：{exc}"}
    if section != ref.get("section"):
        return {"skip": "处置来源章节与原文标题不一致。"}
    if case_label not in section:
        return {"skip": "处置所在章节不是该案例标题。"}
    if action_label not in text or "处理" not in text:
        return {"skip": "来源行没有原文记载的处理内容。"}
    cases = nodes_of(graph_nodes, "case", case_label)
    if len(cases) != 1:
        return {"skip": "案例标题在正式图谱中不是唯一节点，不自动合并。"}
    actions = [
        node
        for node in nodes_of(graph_nodes, "action", action_label)
        if any(same_ref(item, ref) for item in node["data"].get("source_refs", []))
    ]
    if len(actions) != 1:
        return {"skip": "不能唯一确定这条处置节点，避免把处置接到其他案例。"}
    return {
        "edge": {
            "id": stable_id("edge", "HAS_ACTION", cases[0]["id"], actions[0]["id"]),
            "source": cases[0]["id"],
            "target": actions[0]["id"],
            "data": {"relation": "HAS_ACTION", "source_refs": [ref]},
        },
        "verified": {
            "relation": "HAS_ACTION",
            "label": action_label,
            "device": case_label,
            "value": action_label,
            "unit": "",
            "applicability": "",
            "source": ref,
            "source_checked": True,
        },
    }


def merge(graph: dict, accepted: list[dict]) -> tuple[list[dict], list[dict], int, int]:
    node_ids = {node["id"] for node in graph["nodes"]}
    edge_ids = {edge["id"] for edge in graph["edges"]}
    new_nodes = []
    new_edges = []
    already_nodes = 0
    already_edges = 0
    for item in accepted:
        node = item.get("node")
        if node is not None:
            if node["id"] in node_ids:
                already_nodes += 1
            else:
                graph["nodes"].append(node)
                node_ids.add(node["id"])
                new_nodes.append(node)
        edge = item["edge"]
        if edge["id"] in edge_ids:
            already_edges += 1
        else:
            graph["edges"].append(edge)
            edge_ids.add(edge["id"])
            new_edges.append(edge)
    return new_nodes, new_edges, already_nodes, already_edges


def confirmed_records(graph: dict, markdown: dict[str, list[str]]) -> list[str]:
    by_id = {node["id"]: node for node in graph["nodes"]}
    records = []
    for node in graph["nodes"]:
        if node["data"]["type"] != "parameter":
            continue
        ref = node["data"]["source_refs"][0]
        _name, _number, text = source_line(markdown, ref)
        matched = node["data"]["label"] in text and str(node["data"]["value"]) in text
        records.append(
            f"- 参数：{node['data']['device_label']} → {node['data']['label']}。"
            f"原始值“{node['data']['value']}”。单位“{node['data']['unit'] or '保留在原始表达中'}”。"
            f"适用条件“{node['data']['applicability'] or '原文该节没有适用范围'}”。"
            f"来源 {ref['document']}，{ref['locator']}。来源行核对{'通过' if matched else '失败'}。"
        )
    for edge in graph["edges"]:
        if edge["data"]["relation"] != "HAS_ACTION":
            continue
        source = by_id[edge["source"]]
        target = by_id[edge["target"]]
        ref = edge["data"]["source_refs"][0]
        _name, _number, text = source_line(markdown, ref)
        matched = target["data"]["label"] in text and "处理" in text
        records.append(
            f"- 案例处置：{source['data']['label']} → {target['data']['label']}。"
            f"来源 {ref['document']}，{ref['locator']}。来源行核对{'通过' if matched else '失败'}。"
        )
    return records


def write_report(
    before_nodes: int,
    before_edges: int,
    after_nodes: int,
    after_edges: int,
    new_nodes: list[dict],
    new_edges: list[dict],
    skipped: list[dict],
    accepted: list[dict],
    errors: list[str],
    restored: bool,
    already_nodes: int = 0,
    already_edges: int = 0,
    graph: dict | None = None,
    markdown: dict[str, list[str]] | None = None,
) -> None:
    baseline_nodes = before_nodes
    baseline_edges = before_edges
    if ORIGINAL_PATH.exists():
        baseline = json.loads(ORIGINAL_PATH.read_text(encoding="utf-8"))
        baseline_nodes = len(baseline["nodes"])
        baseline_edges = len(baseline["edges"])
    lines = [
        "# 知识图谱补充更新报告",
        "",
        "本次只写入预检查通过的参数和案例处置。没有修改原始 Word、Markdown 和 74 条旧候选，也没有调用在线模型。",
        "",
        "## 数量",
        "",
        f"- 补充前：{baseline_nodes} 个节点，{baseline_edges} 条关系。",
        f"- 实际新增节点：{after_nodes - baseline_nodes} 个。",
        f"- 实际新增关系：{after_edges - baseline_edges} 条。",
        f"- 本轮新写入：节点 {len(new_nodes)} 个，关系 {len(new_edges)} 条。",
        f"- 重复运行时已存在、未再次添加：节点 {already_nodes} 个，关系 {already_edges} 条。",
        f"- 补充后：{after_nodes} 个节点，{after_edges} 条关系。",
        f"- 校验结果：{'失败，已恢复本轮开始前的 graph.json。' if restored else '通过。'}",
        "",
    ]
    if errors:
        lines.append("## 校验错误")
        lines.append("")
        for error in errors:
            lines.append(f"- {error}")
        lines.append("")
    lines.extend(["## 跳过的候选", ""])
    if not skipped:
        lines.append("- 没有跳过项。")
    else:
        for item in skipped:
            lines.append(
                f"- {item['relation']}：{item.get('from_label', '')} → {item['item']}。{item['reason']}"
            )
    lines.extend(["", "## 新增数据的来源核验", ""])
    if restored or graph is None or markdown is None:
        lines.append("- 本轮没有保留新增数据。")
    else:
        records = confirmed_records(graph, markdown)
        if not records:
            lines.append("- 正式图谱中没有本次规则写入的参数或案例处置。")
        lines.extend(records)
    lines.extend(
        [
            "",
            "## 仍然存在的问题",
            "",
            "- 74 条旧候选没有批准，也没有合并同名案例或同名设备。",
            "- 齿轮箱操作限值因为设备名称尚未确认，没有进入正式图谱。",
            "- 故障原因仍然是 0，本次没有从定义句或案例标题推断原因。",
            "- 齿轮箱和冷却泵的额定电压单元格仍为空，没有补写数值。",
            "",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
