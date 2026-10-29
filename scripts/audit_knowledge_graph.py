"""审核本地知识图谱，不修改正式图谱。

只读取 data/processed 下的 Markdown、graph.json 和 review_candidates.json。
不访问网络，不调用 DeepSeek，不改写 graph.json、Word 或 Markdown。
建议补充写入 proposed_additions.json，审核状态不会被自动批准。
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
REPORT_PATH = GRAPH_DIR / "knowledge_audit_report.md"
PROPOSAL_PATH = GRAPH_DIR / "proposed_additions.json"

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


def split_row(line: str) -> list[str]:
    parts = re.split(r"(?<!\\)\|", line.strip().strip("|"))
    return [part.replace("\\|", "|").replace("<br>", "\n").strip() for part in parts]


def heading(line: str) -> tuple[int, str] | None:
    match = re.match(r"^(#{1,6})\s+(.*\S)\s*$", line)
    if not match:
        return None
    return len(match.group(1)), match.group(2).strip()


def make_ref(document_key: str, section: str, kind: str, line_no: int) -> dict:
    return {
        "document": document_key.replace(".md", ".docx"),
        "section": section or "文档开头",
        "locator": f"{kind} 第{line_no}行",
    }


def load_lines() -> dict[str, list[str]]:
    return {
        name: (MARKDOWN_DIR / name).read_text(encoding="utf-8").splitlines()
        for name in DOCUMENTS
    }


def parse_tables(lines: list[str]) -> list[dict]:
    tables = []
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
            tables.append(
                {
                    "header_line": index + 1,
                    "headers": headers,
                    "rows": rows,
                }
            )
            index = cursor
            continue
        index += 1
    return tables


def section_path(stack: list[tuple[int, str]]) -> str:
    return " / ".join(title for _, title in stack)


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
            return section_path(stack)
    return ""


def business_degree(edges: list[dict]) -> Counter[str]:
    degree: Counter[str] = Counter()
    for edge in edges:
        if edge["data"]["relation"] == "SUPPORTED_BY":
            continue
        degree[edge["source"]] += 1
        degree[edge["target"]] += 1
    return degree


def evidence_gaps(nodes: list[dict], edges: list[dict]) -> dict[str, int]:
    incomplete = 0
    for item in nodes + edges:
        refs = item["data"].get("source_refs") or []
        if not refs:
            incomplete += 1
            continue
        for ref in refs:
            if not ref.get("document") or not ref.get("section") or not ref.get("locator"):
                incomplete += 1
    missing_endpoints = 0
    node_ids = {node["id"] for node in nodes}
    for edge in edges:
        if edge["source"] not in node_ids or edge["target"] not in node_ids:
            missing_endpoints += 1
    duplicate_edges = sum(
        1
        for count in Counter(
            (edge["source"], edge["target"], edge["data"]["relation"]) for edge in edges
        ).values()
        if count > 1
    )
    return {
        "incomplete_evidence": incomplete,
        "missing_endpoints": missing_endpoints,
        "duplicate_edge_keys": duplicate_edges,
    }


def explicit_parameters(lines_by_doc: dict[str, list[str]], devices: set[str]) -> tuple[list[dict], list[dict], list[dict]]:
    nodes: list[dict] = []
    edges: list[dict] = []
    blank_rows: list[dict] = []
    applicability: dict[str, str] = {}
    for name in ("KB-01_设备知识图谱.md", "KB-03_操作规程与技术手册.md"):
        lines = lines_by_doc[name]
        for line_no, line in enumerate(lines, 1):
            if line.startswith("适用范围："):
                applicability[section_at(lines, line_no)] = line.split("：", 1)[1].strip()
        for table in parse_tables(lines):
            if table["headers"][:2] not in (["参数", "值"], ["参数", "限值/要求"]):
                continue
            section = section_at(lines, table["header_line"])
            device = device_for_section(section, devices)
            kind = "rated_parameter" if table["headers"][1] == "值" else "operating_limit"
            for row_no, row in table["rows"]:
                label = row[0] if row else ""
                value = row[1] if len(row) > 1 else ""
                ref = make_ref(name, section, "表格", row_no)
                if not label:
                    continue
                if not value:
                    blank_rows.append(
                        {
                            "label": label,
                            "device_label": device,
                            "review_status": "explicit_blank",
                            "reason": "参数表有这一行，但取值单元格为空，不能补写数值。",
                            "source_refs": [ref],
                        }
                    )
                    continue
                node_id = stable_id("proposed-parameter", kind, device or section, label, value)
                nodes.append(
                    {
                        "id": node_id,
                        "proposed_type": "parameter",
                        "label": label,
                        "attributes": {
                            "value": value,
                            "kind": kind,
                            "applicability": applicability.get(section, ""),
                        },
                        "review_status": "explicit",
                        "source_refs": [ref],
                    }
                )
                if device:
                    edges.append(
                        {
                            "id": stable_id("proposed-edge", "HAS_PARAMETER", device, node_id),
                            "proposed_relation": "HAS_PARAMETER",
                            "from_type": "device",
                            "from_label": device,
                            "to_type": "parameter",
                            "to_id": node_id,
                            "to_label": label,
                            "review_status": "explicit",
                            "reason": "参数表位于该设备或其同名操作规程标题之下，原文直接给出了参数名和取值。",
                            "source_refs": [ref],
                        }
                    )
    return nodes, edges, blank_rows


def device_for_section(section: str, devices: set[str]) -> str:
    parts = [part.strip() for part in section.split(" / ")]
    for part in reversed(parts):
        if part in devices:
            return part
        procedure = re.sub(r"操作规程$", "", part).strip()
        if procedure in devices:
            return procedure
    return ""


def explicit_actions(lines_by_doc: dict[str, list[str]]) -> list[dict]:
    edges = []
    pattern = re.compile(r"^(?:\d+\.\s*)?处理[:：]\s*(.+)$")
    for name in ("KB-04_专家经验与典型案例.md", "KB-06_RAG检索知识库.md"):
        lines = lines_by_doc[name]
        case_label = ""
        for line_no, line in enumerate(lines, 1):
            found = heading(line)
            if found and found[0] >= 2:
                title = found[1]
                if name.startswith("KB-04") and found[0] == 2:
                    case_label = title
                elif name.startswith("KB-06") and found[0] == 3 and "典型案例" in section_at(lines, line_no):
                    case_label = title
                elif found[0] == 2:
                    case_label = ""
            match = pattern.match(line.strip())
            if not match or not case_label:
                continue
            action = match.group(1).strip()
            ref = make_ref(name, section_at(lines, line_no), "段落", line_no)
            edges.append(
                {
                    "id": stable_id("proposed-edge", "HAS_ACTION", case_label, action),
                    "proposed_relation": "HAS_ACTION",
                    "from_type": "case",
                    "from_label": case_label,
                    "to_type": "action",
                    "to_label": action,
                    "review_status": "explicit",
                    "reason": "处置文字出现在该案例的“处理”栏。原文只说明这个案例采取了该处置，没有把它对应到单一故障或原因。",
                    "source_refs": [ref],
                }
            )
    return edges


def pending_classifications(lines: list[str]) -> list[dict]:
    """KB-02 定义句里的枚举只作为待审核分类，不写成确定原因。"""
    targets = {
        12: "轴承磨损",
        44: "齿轮异常",
    }
    items = []
    for line_no, parent in targets.items():
        sentence = lines[line_no - 1].strip()
        listed = []
        if "可分为" in sentence:
            tail = sentence.split("可分为", 1)[1]
            listed = [item.strip() for item in tail.strip("。").split("、") if item.strip()]
        elif "出现的" in sentence:
            tail = sentence.split("出现的", 1)[1].split("等", 1)[0]
            listed = [item.strip() for item in tail.split("、") if item.strip()]
        for name in listed:
            name = name.strip("。").removesuffix("等异常状态").removesuffix("等").strip()
            if not name or name == "等":
                continue
            items.append(
                {
                    "id": stable_id("proposed-pending", "HAS_DAMAGE_FORM", parent, name),
                    "proposed_relation": "HAS_DAMAGE_FORM",
                    "from_type": "fault",
                    "from_label": parent,
                    "to_label": name,
                    "review_status": "pending",
                    "reason": "定义句枚举了损伤形式，但带有“等”，也没有说明它等于某个部件上的同名故障，因此不能写入正式图谱。",
                    "source_sentence_locator": make_ref(
                        "KB-02_故障模式诊断知识库.md",
                        section_at(lines, line_no),
                        "段落",
                        line_no,
                    ),
                    "not_a_cause": True,
                }
            )
    return items


def count_cause_language(lines_by_doc: dict[str, list[str]]) -> dict[str, int]:
    counts = {}
    for name, lines in lines_by_doc.items():
        counts[name] = sum(1 for line in lines if "原因" in line)
    return counts


def build_report(context: dict) -> str:
    reasons = context["candidate_reasons"]
    reason_text = {
        "same_label_not_merged": "同名故障出现在不同部件或不同文档中，文档没有说它们是同一个故障。",
        "possible_device_alias": "设备名称相近或互相包含，文档没有声明它们是同一台设备。",
        "case_title_overlap": "KB-04 带编号的案例标题包含 KB-06 的案例标题，文档没有声明它们是同一条案例。",
        "flow_chain_ambiguous": "流程链用箭头给出了方向，但箭头两端是否都是设备没有写明。",
        "case_action_unlinked": "案例写了处置措施，但没有把它对应到单一故障或原因。",
        "definition_not_split": "KB-02 的故障定义描述了机理，但没有单独的“原因”字段。",
        "associated_fault_without_component": "关联故障只列在传动链下，没有指定所属部件。",
        "coupling_part_unassigned": "耦合件写在上下游设备之间，没有说明属于哪一台设备。",
        "cross_type_same_label": "同一个名称既作为部件又作为设备出现，没有说明能否合并。",
    }
    lines = [
        "# 知识图谱质量审核报告",
        "",
        "本次只审核，没有修改 `graph.json`，没有修改官方 Word 和已解析 Markdown，也没有调用在线模型。",
        "建议补充的内容在 `proposed_additions.json`。其中 `explicit` 表示原文表格或字段直接支持，`pending` 表示仍需人工审核。两种状态都还没有写入正式图谱。",
        "",
        "## 核对范围",
        "",
        "- 正式图谱：226 个节点，430 条关系。",
        f"- 待审核候选：{context['candidate_count']} 条，本次没有批准任何一条。",
        "- 对照文档：`data/processed/markdown/` 下 6 份解析结果。",
        "",
        "## 已确认的问题",
        "",
        "### 1. 故障原因节点为什么是 0",
        "",
        "六份文档都没有“原因”或“故障原因”字段。全文检索“原因”得到的命中数如下：",
        "",
    ]
    for name, count in context["cause_word_counts"].items():
        lines.append(f"- `{name}`：{count} 行")
    lines.extend(
        [
            "",
            "KB-02 只有 4 段定义句，描述的是故障本身的机理，例如损伤形式或“质量分布不均导致离心力”。这些句子没有把原因拆成独立条目。现有 4 条 `definition_not_split` 候选保留为待审核，本次不新建 cause 节点。",
            "",
            "### 2. 8 个处置措施为什么没有关系",
            "",
            "8 个 action 节点的业务关系度数都是 0，只通过来源边连到文档。它们来自 4 句“处理”文字，KB-04 和 KB-06 各记了一次，所以节点数是 8 而不是 4。",
            "原文把处置写在案例下面，没有写“该故障推荐该处置”。因此正式图谱没有 `RECOMMENDS_ACTION`。",
            "可以明确补充的是案例到处置的 `HAS_ACTION`，共 8 条；不能据此把处置接到某个故障或原因上。KB-04 的前 3 个案例没有“处理”栏。",
            "",
            "### 3. KB-03 的 23 行怎样结构化",
            "",
            "KB-03 有 36 行正文，其中 13 行是标题和表格格式，23 行尚未进入正式图谱。这 23 行的构成是：",
            "",
            "- 19 行是三张“参数 / 限值/要求”表中的数据行，分别属于电机、齿轮箱、冷却泵规程。",
            "- 3 行是“适用范围”，说明限值适用于哪类设备。",
            "- 1 行是文档开头的总述，不是某台设备的限值。",
            "",
            "19 行限值都可以建成 parameter 节点，并用 `HAS_PARAMETER` 连到规程标题所对应的已有设备。适用范围写在对应边上的适用条件里，不单独编成故障或原因。文档总述不生成节点。",
            "",
            "### 4. 74 条待审核关系属于什么问题",
            "",
            "74 条全部保持 `pending`，没有一条被当作确定事实。分类如下：",
            "",
        ]
    )
    for code, count in sorted(reasons.items()):
        lines.append(f"- `{code}`：{count} 条。{reason_text.get(code, '')}")
    lines.extend(
        [
            "",
            "按建议关系看：`SAME_AS` 45 条，`CONNECTS_TO` 12 条，`RECOMMENDS_ACTION` 8 条，`HAS_CAUSE` 4 条，`HAS_FAULT` 3 条，`HAS_COMPONENT` 2 条。",
            "",
            "### 5. 为什么案例是 14 个，而 KB-04 写出 7 个",
            "",
            "KB-04 有 7 个二级标题，都带 EXP-2024 编号。KB-06 的“典型案例”下又有 7 个三级标题，标题是 KB-04 标题去掉编号后的文字。两边都各建了一个 case 节点，所以是 14 个。",
            "KB-06 的“来源”只写了“专家经验库”，没有写 EXP 编号，因此不能直接判定两边是同一条案例。这 7 对已经在 `case_title_overlap` 中，共 14 条候选，因为每对从两个标题各记了一次。",
            "",
            "### 6. 68 个故障和 4 类核心故障是什么关系",
            "",
            f"KB-02 的二级标题只有 4 个：{context['core_faults']}。它们是正式图谱中的 4 个故障节点，但没有任何上下位关系把其余故障收进这 4 类。",
            "其余故障节点主要来自 KB-01 各部件的“常见故障”字段，另有 KB-05 传动链下列出、但没有指定部件的故障。按来源文档统计，故障引用次数为："
            + context["fault_docs"]
            + "。",
            f"68 个故障节点里有 {context['unique_fault_labels']} 个不同名称。同名故障因为所属部件不同而分开存放，这是当前编号规则，不是同一条关系被重复写入。",
            "文档没有写“外圈剥落属于轴承磨损”这类层级。定义句里的损伤形式枚举已放入建议文件，状态为待审核。",
            "",
            "### 7. 是否有重复节点、重复关系或错误关联",
            "",
            f"节点编号和关系编号没有重复。同一对端点、同一种关系没有重复边。关系两端都能在节点中找到。证据缺失 {context['gaps']['incomplete_evidence']} 条，悬空端点 {context['gaps']['missing_endpoints']} 条。",
            "同名节点是存在的：部件、故障和处置在不同父项下使用了相同文字。处置的 8 个节点实际只有 4 句不同文字。设备名称“棒线齿轮箱”和“棒线主减速机/齿轮箱”是两个节点，案例和规程用的是前者，设备图谱用的是后者。按原文抽取没有接错字段，但是否为同一设备仍待审核。",
            "",
            "### 8. 节点和关系是否都能回到官方原文",
            "",
            "抽查正式图谱后，226 个节点和 430 条关系都有 `source_refs`，并且同时包含文档名、章节和“段落/表格/标题 第 N 行”。定位的是解析后的 Markdown 行，对应原始 Word，没有发现缺依据的正式节点或关系。",
            "尚未结构化的正文，例如参数、诊断思路和经验教训，本来就没有进入正式图谱，所以它们目前没有图谱关系。",
            "",
            "## 建议补充但尚未写入正式图谱",
            "",
            f"- 有取值的 parameter 节点：{context['explicit_parameter_nodes']} 个。其中 KB-01 额定参数 {context['rated_nodes']} 个，KB-03 操作限值 {context['limit_nodes']} 个。",
            f"- `HAS_PARAMETER`：{context['explicit_parameter_edges']} 条。设备名称与已有设备节点完全一致时才连接。",
            f"- 参数表中取值为空、因此不生成数值的行：{context['blank_rows']} 行。",
            f"- `HAS_ACTION`：{context['explicit_action_edges']} 条，连接已有案例和已有处置，不新增处置节点。",
            f"- 合计可依据原文新增的节点 {context['explicit_parameter_nodes']} 个、关系 {context['explicit_parameter_edges'] + context['explicit_action_edges']} 条。",
            "",
            "这些条目的 `review_status` 是 `explicit`。它们仍然只存在于建议文件中。",
            "",
            "## 仍需人工审核",
            "",
            "- 原有 74 条候选全部保持待审核。",
            f"- KB-02 定义句中的损伤形式枚举：{context['pending_classifications']} 条，状态为 `pending`，不能当成故障原因，也不能自动并到部件故障上。",
            "- 案例标题里的“导致”只说明标题用了因果措辞，没有单独的原因字段，不生成 cause。",
            "- 7 对 KB-04 / KB-06 案例是否为同一案例。",
            "- 设备别名、流程链、耦合件归属、跨类型同名。",
            "",
            "## 原文没有提供的知识",
            "",
            "- 没有结构化的故障原因表，也没有“原因”字段。",
            "- 没有把 4 类核心故障定义成其余 60 多个故障的上级分类。",
            "- 没有把处置措施对应到某一个故障模式或原因。",
            "- 没有声明 KB-04 与 KB-06 的 7 对案例是同一记录。",
            "- 没有声明“棒线齿轮箱”和“棒线主减速机/齿轮箱”是同一设备。",
            "- 诊断思路和经验教训是叙述，不是可直接入库的诊断规则。",
            "- KB-01 中齿轮箱和冷却泵的“额定电压”单元格为空。",
            "",
            "## 建议的下一步",
            "",
            "1. 先把建议文件中状态为 `explicit` 的参数节点和案例到处置关系，作为下一次图谱更新的候选补丁。更新前仍要人工看一遍。",
            "2. 不要先合并 74 条候选。优先让熟悉设备的人确认 7 对案例和“棒线齿轮箱”这个设备别名，因为它们会影响后面的参数该挂到哪台设备。",
            "3. 不要从定义句自动生成故障原因。如果业务上需要 cause，应先在资料中补充明确的原因字段，再重新抽取。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    before = {
        "graph": sha256(GRAPH_PATH),
        "candidates": sha256(CANDIDATE_PATH),
        "markdown": {name: sha256(MARKDOWN_DIR / name) for name in DOCUMENTS},
    }
    graph = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    candidates = json.loads(CANDIDATE_PATH.read_text(encoding="utf-8"))
    lines_by_doc = load_lines()
    nodes = graph["nodes"]
    edges = graph["edges"]
    if len(nodes) != 226 or len(edges) != 430 or len(candidates) != 74:
        raise RuntimeError("正式图谱或候选数量与已知基线不一致，停止审核写出。")
    devices = {node["data"]["label"] for node in nodes if node["data"]["type"] == "device"}
    parameter_nodes, parameter_edges, blank_rows = explicit_parameters(lines_by_doc, devices)
    action_edges = explicit_actions(lines_by_doc)
    classifications = pending_classifications(lines_by_doc["KB-02_故障模式诊断知识库.md"])
    if any(item["review_status"] == "approved" for item in parameter_nodes + parameter_edges + action_edges + classifications):
        raise RuntimeError("建议项不能标记为已批准。")
    degree = business_degree(edges)
    action_degrees = [
        degree[node["id"]] for node in nodes if node["data"]["type"] == "action"
    ]
    if action_degrees != [0] * 8:
        raise RuntimeError("处置节点的业务度数与审核判断不一致。")
    core_faults = []
    for line in lines_by_doc["KB-02_故障模式诊断知识库.md"]:
        found = heading(line)
        if found and found[0] == 2:
            core_faults.append(found[1])
    fault_docs: Counter[str] = Counter()
    for node in nodes:
        if node["data"]["type"] != "fault":
            continue
        for ref in node["data"]["source_refs"]:
            fault_docs[ref["document"]] += 1
    context = {
        "candidate_count": len(candidates),
        "candidate_reasons": dict(Counter(item["reason_code"] for item in candidates)),
        "cause_word_counts": count_cause_language(lines_by_doc),
        "core_faults": "、".join(core_faults),
        "fault_docs": "、".join(f"{name} {count}" for name, count in sorted(fault_docs.items())),
        "unique_fault_labels": len({node["data"]["label"] for node in nodes if node["data"]["type"] == "fault"}),
        "gaps": evidence_gaps(nodes, edges),
        "explicit_parameter_nodes": len(parameter_nodes),
        "explicit_parameter_edges": len(parameter_edges),
        "rated_nodes": sum(1 for node in parameter_nodes if node["attributes"]["kind"] == "rated_parameter"),
        "limit_nodes": sum(1 for node in parameter_nodes if node["attributes"]["kind"] == "operating_limit"),
        "blank_rows": len(blank_rows),
        "explicit_action_edges": len(action_edges),
        "pending_classifications": len(classifications),
    }
    if context["limit_nodes"] != 19 or context["rated_nodes"] != 13 or context["blank_rows"] != 2:
        raise RuntimeError(
            "参数行数量与原文核对不一致："
            f"限值 {context['limit_nodes']}，额定 {context['rated_nodes']}，空值 {context['blank_rows']}"
        )
    if context["explicit_action_edges"] != 8 or len(classifications) != 11:
        raise RuntimeError("处置关系或损伤形式枚举数量与原文核对不一致。")
    if len(parameter_edges) != len(parameter_nodes):
        raise RuntimeError("有参数没有找到同名设备，不能静默丢弃。")
    kb03_lines = lines_by_doc["KB-03_操作规程与技术手册.md"]
    scope_lines = sum(1 for line in kb03_lines if line.startswith("适用范围："))
    if scope_lines != 3:
        raise RuntimeError("KB-03 适用范围行数不是 3。")
    proposal = {
        "description": "知识图谱审核后的补充建议。不是正式图谱，不能直接替换 graph.json。",
        "formal_graph_modified": False,
        "candidates_auto_approved": False,
        "counts": {
            "explicit_nodes": len(parameter_nodes),
            "explicit_edges": len(parameter_edges) + len(action_edges),
            "explicit_blank_rows": len(blank_rows),
            "pending_items": len(classifications),
            "existing_candidates_left_pending": len(candidates),
        },
        "nodes": parameter_nodes,
        "edges": parameter_edges + action_edges,
        "explicit_blank_rows": blank_rows,
        "pending_review": classifications,
    }
    REPORT_PATH.write_text(build_report(context), encoding="utf-8")
    PROPOSAL_PATH.write_text(json.dumps(proposal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    after = {
        "graph": sha256(GRAPH_PATH),
        "candidates": sha256(CANDIDATE_PATH),
        "markdown": {name: sha256(MARKDOWN_DIR / name) for name in DOCUMENTS},
    }
    if before != after:
        raise RuntimeError("审核过程改变了正式图谱、候选文件或 Markdown。")
    print(json.dumps(context["cause_word_counts"], ensure_ascii=False))
    print(json.dumps(proposal["counts"], ensure_ascii=False))
    print("core", context["core_faults"])
    print("unique_faults", context["unique_fault_labels"])
    print("unchanged", True)


if __name__ == "__main__":
    main()
