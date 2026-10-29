"""本地工具：官方知识检索、知识图谱关系查询、故障特征对照、统计偏离检查和记录联动分析。

只读取现有索引、图谱、KB-02 原文和已隔离标签的官方观测。不调用 DeepSeek，也不改写知识库文件。
工具声明只描述函数名称和参数，不会因此执行查询。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

from app.services.anomaly_detection import detect_anomaly as run_detect_anomaly
from app.services.knowledge_graph import GraphFileError, load_graph
from app.services.knowledge_search import MARKDOWN_DIR, SearchIndexError, load_index, search
from app.services.official_dataset import DatasetError

RELATION_TYPES = (
    "CONNECTS_TO",
    "HAS_ACTION",
    "HAS_COMPONENT",
    "HAS_FAULT",
    "HAS_FEATURE",
    "HAS_PARAMETER",
    "OCCURS_ON",
    "SUPPORTED_BY",
)
_RELATION_TYPE_SET = set(RELATION_TYPES)
_SEARCH_PARAMS = {"query", "top_k"}
_RELATION_PARAMS = {"name", "relation_types"}
_DIAGNOSIS_PARAMS = {"observed_features", "equipment_name"}
_ANOMALY_PARAMS = {"record_id"}
_RECORD_ANALYSIS_PARAMS = {"record_id"}
_REF_KEYS = ("document", "section", "locator")
_KB02_DOCUMENT = "KB-02_故障模式诊断知识库.md"
_SPECTRUM_SECTION = "频谱特征"
_OFFICIAL_DIR = Path(__file__).resolve().parents[3] / "data" / "official"
_UNCONFIRMED_DOCUMENT = "无法确认"
_FEATURE_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_PHRASE_SPLIT = re.compile(r"[，、；。：:（）()【】\[\]<>《》\"'‘’“”+/]+")
_GENERIC_FRAGMENTS = {
    "频率",
    "振动",
    "明显",
    "上升",
    "出现",
    "一般",
    "特征",
    "主导",
    "幅值",
    "方向",
    "变化",
    "增加",
    "伴随",
    "持续",
    "显著",
    "冲击",
    "能量",
    "噪声",
    "温度",
    "轴承",
    "齿轮",
    "转子",
    "相位",
    "径向",
    "轴向",
    "负荷",
    "谐波",
    "边带",
    "调制",
    "峰值",
    "因子",
    "异音",
    "摩擦",
    "金属",
    "宽频",
    "中低",
    "频段",
}


def _error(tool: str, message: str) -> dict:
    return {"ok": False, "tool": tool, "error": message}


def _copy_refs(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        return []
    refs = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        refs.append({key: item[key] for key in _REF_KEYS if key in item})
    return refs


def _node_data(node: dict) -> dict:
    data = node.get("data")
    return data if isinstance(data, dict) else {}


def _node_brief(node: dict) -> dict:
    data = _node_data(node)
    return {
        "id": str(node.get("id") or ""),
        "name": str(data.get("label") or ""),
        "type": str(data.get("type") or ""),
        "source_refs": _copy_refs(data.get("source_refs")),
    }


def _label(node: dict) -> str:
    return str(_node_data(node).get("label") or "").strip()


def _distinct_nodes(nodes: list[dict]) -> list[dict]:
    seen: set[str] = set()
    chosen = []
    for node in nodes:
        node_id = str(node.get("id") or "")
        if node_id in seen:
            continue
        seen.add(node_id)
        chosen.append(node)
    return chosen


def _match_nodes(nodes: list[dict], name: str) -> tuple[str, list[dict]]:
    """先按完整名称精确匹配；没有精确结果时，再做部分匹配。"""
    exact = _distinct_nodes([node for node in nodes if _label(node) == name])
    if exact:
        return "exact", exact
    if len(name) < 2:
        return "none", []

    containing = _distinct_nodes([node for node in nodes if name in _label(node)])
    if containing:
        return "partial", containing

    contained = []
    for node in nodes:
        label = _label(node)
        if len(label) >= 2 and label in name:
            contained.append(node)
    contained = _distinct_nodes(contained)
    if not contained:
        return "none", []

    labels = {_label(node) for node in contained}
    specific = [
        node
        for node in contained
        if not any(other != _label(node) and _label(node) in other for other in labels)
    ]
    return "partial", _distinct_nodes(specific)


def _limit_top_k(value: object) -> int | dict:
    if value is None:
        return 5
    if isinstance(value, bool) or not isinstance(value, int):
        return _error("search_knowledge", "top_k 必须是 1 到 5 的整数。")
    if value < 1:
        return _error("search_knowledge", "top_k 必须是 1 到 5 的整数。")
    return 5 if value > 5 else value


def search_knowledge(query: str, top_k: int | None = 5) -> dict:
    """调用现有 search()。无命中时返回空列表，不生成知识内容。"""
    if not isinstance(query, str) or not query.strip():
        return _error("search_knowledge", "请提供查询内容。")
    limited = _limit_top_k(top_k)
    if isinstance(limited, dict):
        return limited
    try:
        found = search(query.strip(), limited)
    except SearchIndexError as exc:
        return _error("search_knowledge", exc.message)
    results = [
        {
            "id": item["id"],
            "text": item["text"],
            "document": item["document"],
            "section": item["section"],
            "locator": item["locator"],
            "score": item["score"],
        }
        for item in found["results"]
    ]
    return {
        "ok": True,
        "tool": "search_knowledge",
        "query": found["query"],
        "top_k": found["top_k"],
        "count": len(results),
        "message": found["message"],
        "results": results,
    }


def _checked_relation_types(value: object) -> list[str] | None | dict:
    if value is None:
        return None
    if not isinstance(value, list):
        return _error("get_equipment_relations", "relation_types 必须是关系类型列表。")
    chosen = []
    invalid = []
    for item in value:
        if not isinstance(item, str) or item not in _RELATION_TYPE_SET:
            invalid.append(str(item))
            continue
        if item not in chosen:
            chosen.append(item)
    if invalid:
        allowed = "、".join(RELATION_TYPES)
        rejected = "、".join(invalid)
        return _error(
            "get_equipment_relations",
            f"不支持的关系类型：{rejected}。只能使用：{allowed}。",
        )
    return chosen or None


def _relations_of(node_id: str, graph: dict, relation_types: list[str] | None) -> list[dict]:
    briefs = {str(node.get("id") or ""): _node_brief(node) for node in graph["nodes"]}
    allowed = set(relation_types) if relation_types else None
    relations = []
    for edge in graph["edges"]:
        if not isinstance(edge, dict):
            continue
        source_id = str(edge.get("source") or "")
        target_id = str(edge.get("target") or "")
        if node_id not in (source_id, target_id):
            continue
        data = edge.get("data")
        data = data if isinstance(data, dict) else {}
        relation = data.get("relation")
        if not isinstance(relation, str) or relation not in _RELATION_TYPE_SET:
            continue
        if allowed is not None and relation not in allowed:
            continue
        source = briefs.get(source_id, {})
        target = briefs.get(target_id, {})
        relations.append(
            {
                "edge_id": str(edge.get("id") or ""),
                "source_id": source_id,
                "source_name": source.get("name", ""),
                "source_type": source.get("type", ""),
                "relation": relation,
                "target_id": target_id,
                "target_name": target.get("name", ""),
                "target_type": target.get("type", ""),
                "source_refs": _copy_refs(data.get("source_refs")),
            }
        )
    return relations


def get_equipment_relations(name: str, relation_types: list[str] | None = None) -> dict:
    """按名称查找图谱节点。多个匹配只返回候选，唯一匹配才返回真实相邻边。"""
    if not isinstance(name, str) or not name.strip():
        return _error("get_equipment_relations", "请提供设备或部件名称。")
    checked = _checked_relation_types(relation_types)
    if isinstance(checked, dict):
        return checked
    query_name = name.strip()
    try:
        graph = load_graph()
    except GraphFileError as exc:
        return _error("get_equipment_relations", exc.message)

    match, matched = _match_nodes(graph["nodes"], query_name)
    candidates = [_node_brief(node) for node in matched]
    candidates.sort(key=lambda item: (item["name"], item["id"]))
    base = {
        "ok": True,
        "tool": "get_equipment_relations",
        "name": query_name,
        "match": match,
        "relation_types": checked,
    }
    if len(candidates) == 0:
        return {
            **base,
            "ambiguous": False,
            "node": None,
            "candidates": [],
            "count": 0,
            "message": f"知识图谱中没有找到名称「{query_name}」对应的节点。",
            "relations": [],
        }
    if len(candidates) > 1:
        return {
            **base,
            "ambiguous": True,
            "node": None,
            "candidates": candidates,
            "count": 0,
            "message": f"找到 {len(candidates)} 个可能的节点，未选择其中任何一个。请使用候选节点的完整名称再次查询。",
            "relations": [],
        }

    chosen = candidates[0]
    relations = _relations_of(chosen["id"], graph, checked)
    if relations:
        message = "已返回该节点在知识图谱中真实存在的相邻关系。"
    else:
        message = "已找到该节点，但知识图谱中没有符合条件的相邻关系。"
    if match == "partial":
        message = f"名称未完全一致，已匹配唯一节点「{chosen['name']}」。" + message
    return {
        **base,
        "ambiguous": False,
        "node": chosen,
        "candidates": [],
        "count": len(relations),
        "message": message,
        "relations": relations,
    }


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def _original_word_name(markdown_name: str) -> str:
    if not markdown_name.endswith(".md"):
        return _UNCONFIRMED_DOCUMENT
    candidate = markdown_name[: -len(".md")] + ".docx"
    path = _OFFICIAL_DIR / candidate
    if candidate.startswith("~$") or not path.is_file():
        return _UNCONFIRMED_DOCUMENT
    return candidate


def _parse_feature_bullet(line: str) -> tuple[str, str] | None:
    text = line.strip()
    if not text.startswith("•") or "：" not in text:
        return None
    body = text.lstrip("•").strip()
    code, description = body.split("：", 1)
    code = code.strip()
    description = description.strip()
    if not _FEATURE_CODE.fullmatch(code) or not description:
        return None
    return code, description


def _phrase_anchors(description: str) -> list[str]:
    anchors = []
    seen = set()
    for part in _PHRASE_SPLIT.split(description):
        phrase = part.strip()
        if len(phrase) < 4 or phrase in seen:
            continue
        seen.add(phrase)
        anchors.append(phrase)
    return anchors


def _feature_line_numbers() -> dict[str, int]:
    path = MARKDOWN_DIR / _KB02_DOCUMENT
    numbers = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if _parse_feature_bullet(stripped):
            numbers[stripped] = number
    return numbers


def _diagnosis_catalog() -> list[dict]:
    """只读取 KB-02 索引中「频谱特征」条目。故障定义不进入匹配。"""
    payload = load_index()
    line_numbers = _feature_line_numbers()
    catalog = []
    for chunk in payload["chunks"]:
        if chunk.get("document") != _KB02_DOCUMENT:
            continue
        section = str(chunk.get("section") or "")
        parts = [part.strip() for part in section.split(" / ") if part.strip()]
        if len(parts) < 2 or parts[-1] != _SPECTRUM_SECTION:
            continue
        for raw_line in str(chunk.get("text") or "").splitlines():
            parsed = _parse_feature_bullet(raw_line)
            if parsed is None:
                continue
            code, description = parsed
            official_text = raw_line.strip()
            line_number = line_numbers.get(official_text)
            catalog.append(
                {
                    "fault": parts[-2],
                    "code": code,
                    "description": description,
                    "official_text": official_text,
                    "anchors": _phrase_anchors(description),
                    "description_norm": _normalize(description),
                    "code_norm": _normalize(code),
                    "document": _KB02_DOCUMENT,
                    "section": section,
                    "line_number": line_number,
                    "locator": f"第{line_number}行" if line_number else str(chunk.get("locator") or ""),
                    "chunk_locator": str(chunk.get("locator") or ""),
                    "chunk_id": str(chunk.get("id") or ""),
                    "original_document": _original_word_name(_KB02_DOCUMENT),
                }
            )
    return catalog


def _contains_code(text: str, code: str) -> bool:
    return re.search(rf"(?<![a-z0-9_]){re.escape(code)}(?![a-z0-9_])", text) is not None


def _match_basis(observation: str, feature: dict, unique_fragment: bool) -> list[dict]:
    observed = _normalize(observation)
    basis = []
    if _contains_code(observed, feature["code_norm"]):
        basis.append({"rule": "feature_code", "text": feature["code"]})
    for anchor in feature["anchors"]:
        if _normalize(anchor) in observed:
            basis.append({"rule": "official_phrase", "text": anchor})
    if len(observed) >= 4 and (observed in feature["description_norm"] or observed == feature["code_norm"]):
        basis.append({"rule": "observed_phrase", "text": observation.strip()})
    if basis:
        return basis
    if unique_fragment:
        return [{"rule": "distinctive_fragment", "text": observation.strip()}]
    return []


def _unique_fragment_indexes(observation: str, catalog: list[dict]) -> set[int]:
    observed = _normalize(observation)
    if len(observed) < 2 or observed in _GENERIC_FRAGMENTS:
        return set()
    hits = {
        index
        for index, feature in enumerate(catalog)
        if observed in feature["description_norm"] or observed == feature["code_norm"]
    }
    if len(hits) == 1:
        return hits
    return set()


def _checked_features(value: object) -> list[str] | dict:
    if not isinstance(value, list):
        return _error("diagnose_fault", "observed_features 必须是观测特征文本列表。")
    features = []
    for item in value:
        if not isinstance(item, str):
            return _error("diagnose_fault", "观测特征必须是文本，不能是数字或其他类型。")
        text = item.strip()
        if text and text not in features:
            features.append(text)
    if len(features) > 20:
        return _error("diagnose_fault", "观测特征最多 20 条。")
    return features


def diagnose_fault(observed_features: list[str] | None = None, equipment_name: str | None = None) -> dict:
    """对照 KB-02 频谱特征。命中只表示可能对应，不表示故障已经确诊。"""
    if observed_features is None:
        return _error("diagnose_fault", "请提供已经观察到的特征列表。没有观测特征时不能调用本工具。")
    if equipment_name is not None and not isinstance(equipment_name, str):
        return _error("diagnose_fault", "equipment_name 必须是文本。")
    checked = _checked_features(observed_features)
    if isinstance(checked, dict):
        return checked
    device_name = equipment_name.strip() if isinstance(equipment_name, str) and equipment_name.strip() else None
    base = {
        "ok": True,
        "tool": "diagnose_fault",
        "confirmed": False,
        "equipment_name": device_name,
        "equipment_used_for_matching": False,
        "observed_features": checked,
    }
    if not checked:
        return {
            **base,
            "status": "insufficient",
            "count": 0,
            "fault_count": 0,
            "message": "没有提供观测特征，证据不足，不能诊断。",
            "matches": [],
            "unmatched_features": [],
        }
    try:
        catalog = _diagnosis_catalog()
    except SearchIndexError as exc:
        return _error("diagnose_fault", exc.message)
    except OSError:
        return _error("diagnose_fault", "读取 KB-02 原文失败。")
    grouped: dict[str, dict] = {}
    matched_observations = set()
    for observation in checked:
        unique_indexes = _unique_fragment_indexes(observation, catalog)
        for index, feature in enumerate(catalog):
            basis = _match_basis(observation, feature, index in unique_indexes)
            if not basis:
                continue
            matched_observations.add(observation)
            current = grouped.setdefault(
                feature["fault"],
                {"fault": feature["fault"], "confirmed": False, "matched_features": {}},
            )
            item = current["matched_features"].setdefault(
                feature["code"],
                {
                    "code": feature["code"],
                    "description": feature["description"],
                    "official_text": feature["official_text"],
                    "text": feature["official_text"],
                    "document": feature["document"],
                    "section": feature["section"],
                    "locator": feature["locator"],
                    "chunk_locator": feature["chunk_locator"],
                    "chunk_id": feature["chunk_id"],
                    "original_document": feature["original_document"],
                    "observed": [],
                    "match_basis": [],
                },
            )
            if observation not in item["observed"]:
                item["observed"].append(observation)
            for entry in basis:
                if entry not in item["match_basis"]:
                    item["match_basis"].append(entry)
    matches = []
    for group in grouped.values():
        features = list(group["matched_features"].values())
        matches.append(
            {
                "fault": group["fault"],
                "confirmed": False,
                "matched_features": features,
            }
        )
    if not matches:
        return {
            **base,
            "status": "insufficient",
            "count": 0,
            "fault_count": 0,
            "message": "这些观测特征在 KB-02 的频谱特征中没有明确对应条目，证据不足，不能诊断。",
            "matches": [],
            "unmatched_features": checked,
        }
    feature_count = sum(len(item["matched_features"]) for item in matches)
    return {
        **base,
        "status": "possible_match",
        "count": feature_count,
        "fault_count": len(matches),
        "message": "只找到 KB-02 记载中可能对应的频谱特征，不能据此确诊故障，也不提供概率、严重程度或处置建议。",
        "matches": matches,
        "unmatched_features": [item for item in checked if item not in matched_observations],
    }


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": (
                "在本地官方知识库中检索与问题相关的资料。"
                "只返回已经索引的知识块，包括文档、章节、行号和原文。"
                "没有检索到内容时结果为空，不能据此编造故障原因、参数或处置方法。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "需要检索的官方知识问题或关键词。",
                    },
                    "top_k": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 5,
                        "description": "返回条数，默认 5，最大 5。",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_equipment_relations",
            "description": (
                "在本地知识图谱中按设备或部件名称查找节点，并返回图谱里已经存在的相邻关系。"
                "先精确匹配名称；多个节点都匹配时只返回候选，不自行选定。"
                "没有节点或没有关系时返回空结果，不能补充图谱中不存在的设备、部件、故障或参数。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "设备或部件名称。多个同名节点时，应改用候选中的完整名称。",
                    },
                    "relation_types": {
                        "type": "array",
                        "description": "可选。只保留这些关系类型；不传则返回该节点的全部已有关系。",
                        "items": {
                            "type": "string",
                            "enum": list(RELATION_TYPES),
                        },
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "diagnose_fault",
            "description": (
                "仅当用户已经提供观察到的振动或运行特征，并希望对照官方故障模式时调用。"
                "把这些特征与 KB-02 中明确记载的频谱特征做文本对照，返回可能对应的故障、命中的特征和原文位置。"
                "没有观测特征时不要编造特征，也不要调用本工具。"
                "匹配结果不是确诊，不能据此给出故障概率、严重程度、故障原因或处置建议。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "observed_features": {
                        "type": "array",
                        "description": "用户已经给出的观测特征。每项是特征描述或官方特征代码，不能由模型自行补充。",
                        "items": {"type": "string"},
                    },
                    "equipment_name": {
                        "type": "string",
                        "description": "可选。用户提到的设备名称。该名称不参与特征匹配，也不会用来确认或排除故障。",
                    },
                },
                "required": ["observed_features"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "detect_anomaly",
            "description": (
                "仅当用户已经提供官方开发集中的真实记录编号，并要求检查该条观测是否偏离历史基线时调用。"
                "根据记录编号分析该设备当前观测指标相对于此前历史基线的统计偏离。"
                "返回实验性统计检测结果。Robust Z-score 大于 3.5 只是实验性统计判据，不是官方报警阈值。"
                "不要把设备名称当作记录编号，不要编造记录编号。"
                "用户没有提供记录编号时不要调用本工具，也不能自行选择最新记录或随机记录。"
                "统计偏离不是故障诊断结论，不能据此给出故障类型、概率、严重程度或处置建议。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "record_id": {
                        "type": "string",
                        "description": "官方开发集中的真实记录编号，例如 REC-MA-20260403-00346。必须由用户提供，不能用设备名称代替。",
                    },
                },
                "required": ["record_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_equipment_record",
            "description": (
                "仅当用户已经提供官方 Excel 中的真实记录编号，并要求分析该条设备运行记录时调用。"
                "对官方设备运行记录执行完整分析，包括读取观测指标、检查统计偏离、"
                "核对能够由实际数据验证的官方诊断特征，并返回诊断线索或证据不足说明。"
                "不得编造记录编号，不得使用设备编号代替记录编号。"
                "不得读取官方数据集中的答案标签。"
                "不得把统计偏离直接解释为设备故障。"
                "diagnostic_clue 不是 possible_match。X 大于 Y 不能认定转子不平衡。"
                "BPFO 幅值升高不能认定轴承磨损，温度统计偏离不能认定润滑故障。"
                "缺少完整证据时候选故障为空，不能生成故障概率、严重度或正式报警。"
                "用户没有提供记录编号时不要调用本工具，也不能自动选择最新或随机记录。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "record_id": {
                        "type": "string",
                        "description": "官方 Excel 中的真实记录编号，例如 REC-MA-20260403-00346。必须由用户提供，不能用设备编号代替。",
                    },
                },
                "required": ["record_id"],
                "additionalProperties": False,
            },
        },
    },
]


def detect_anomaly(record_id: str | None = None) -> dict:
    """按用户给出的记录编号调用已验证的统计偏离检查。"""
    if not isinstance(record_id, str) or not record_id.strip():
        return _error(
            "detect_anomaly",
            "请提供官方数据集中的记录编号。没有记录编号时不能调用本工具，也不能自动选择最新或随机记录。",
        )
    text = record_id.strip()
    try:
        outcome = run_detect_anomaly(text)
    except DatasetError as exc:
        return _error("detect_anomaly", exc.message)
    if not isinstance(outcome, dict):
        return _error("detect_anomaly", "统计偏离检查没有返回有效结果。")
    if outcome.get("device_id") is None and outcome.get("history_sample_count") == 0:
        return _error("detect_anomaly", f"没有找到记录编号「{text}」，不能编造观测记录或检测结果。")
    kept = {
        "ok": True,
        "tool": "detect_anomaly",
        "confirmed_fault": False,
        "message": "这是实验性统计偏离结果，不是故障确诊，也不是官方报警。",
    }
    for key in (
        "record_id",
        "device_id",
        "observation_time",
        "detection_status",
        "analyzed_metrics",
        "history_sample_count",
        "history_time_start",
        "history_time_end",
        "same_timestamp_excluded_count",
        "baseline_method",
        "formula",
        "experimental_threshold",
        "anomalous_metrics",
        "operating_condition",
        "warnings",
        "data_source",
        "official_record",
    ):
        if key in outcome:
            kept[key] = outcome[key]
    return kept


def analyze_equipment_record(record_id: str | None = None) -> dict:
    """按用户给出的记录编号调用已验证的观测与诊断线索联动。"""
    if not isinstance(record_id, str) or not record_id.strip():
        return _error(
            "analyze_equipment_record",
            "请提供官方数据集中的记录编号。没有记录编号时不能调用本工具，也不能把设备编号当作记录编号，或自动选择最新、随机记录。",
        )
    text = record_id.strip()
    from app.services.diagnostic_bridge import analyze_record_for_diagnosis

    try:
        outcome = analyze_record_for_diagnosis(text)
    except DatasetError as exc:
        return _error("analyze_equipment_record", exc.message)
    if not isinstance(outcome, dict):
        return _error("analyze_equipment_record", "记录分析没有返回有效结果。")
    if outcome.get("ok") is not True:
        message = outcome.get("error")
        if not isinstance(message, str) or not message.strip():
            message = f"没有找到记录编号「{text}」，不能编造观测记录或诊断结果。"
        return _error("analyze_equipment_record", message)
    observation = outcome.get("observation") if isinstance(outcome.get("observation"), dict) else {}
    detected = outcome.get("statistical_deviation") if isinstance(outcome.get("statistical_deviation"), dict) else {}
    kept = {
        "ok": True,
        "tool": "analyze_equipment_record",
        "confirmed_fault": False,
        "message": (
            "诊断等级以联动结果中的 diagnosis_level 为准。"
            "possible_match 只表示知识库特征可能对应，不是故障确诊或正式报警。"
            "X 大于 Y 且缺少轴向偏心时只是部分条件。"
            "统计偏离不能认定轴承磨损或润滑故障。"
            "完整条件未满足时，不能把候选故障当成确诊。"
        ),
    }
    for key in (
        "record_id",
        "diagnosis_level",
        "confirmed",
        "data_source",
        "observation",
        "statistical_deviation",
        "feature_checks",
        "diagnostic_clues",
        "candidate_faults",
        "diagnose_fault_called",
        "diagnose_fault_result",
        "missing_data",
        "insufficient_reasons",
        "warnings",
        "supporting_evidence",
    ):
        if key in outcome:
            kept[key] = outcome[key]
    if isinstance(observation.get("device_id"), str):
        kept["device_id"] = observation["device_id"]
    if isinstance(observation.get("collected_at"), str):
        kept["collected_at"] = observation["collected_at"]
    if isinstance(detected.get("detection_status"), str):
        kept["detection_status"] = detected["detection_status"]
    if isinstance(detected.get("anomalous_metrics"), list):
        kept["anomalous_metrics"] = detected["anomalous_metrics"]
    return kept


_DISPATCH: dict[str, Callable[..., dict]] = {
    "search_knowledge": search_knowledge,
    "get_equipment_relations": get_equipment_relations,
    "diagnose_fault": diagnose_fault,
    "detect_anomaly": detect_anomaly,
    "analyze_equipment_record": analyze_equipment_record,
}
_PARAMS = {
    "search_knowledge": _SEARCH_PARAMS,
    "get_equipment_relations": _RELATION_PARAMS,
    "diagnose_fault": _DIAGNOSIS_PARAMS,
    "detect_anomaly": _ANOMALY_PARAMS,
    "analyze_equipment_record": _RECORD_ANALYSIS_PARAMS,
}


def _parse_arguments(arguments: dict | str | None) -> tuple[dict | None, str | None]:
    if arguments is None:
        return {}, None
    if isinstance(arguments, dict):
        return arguments, None
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return None, "工具参数不是有效的 JSON。"
        if not isinstance(parsed, dict):
            return None, "工具参数必须是 JSON 对象。"
        return parsed, None
    return None, "工具参数必须是 JSON 对象。"


def execute_tool(name: str, arguments: dict | str | None = None) -> dict:
    """按函数名执行已声明的工具。未知名称只返回错误，不查找或执行其他函数。"""
    if not isinstance(name, str) or name not in _DISPATCH:
        shown = name if isinstance(name, str) else ""
        return _error(shown, f"未知工具：{shown}。只能调用已声明的工具。")
    parsed, error = _parse_arguments(arguments)
    if error is not None or parsed is None:
        return _error(name, error or "工具参数必须是 JSON 对象。")
    unknown = sorted(set(parsed) - _PARAMS[name])
    if unknown:
        return _error(name, "不支持的参数：" + "、".join(unknown))
    try:
        return _DISPATCH[name](**parsed)
    except TypeError:
        return _error(name, "工具参数与已声明的函数不一致。")
    except (SearchIndexError, GraphFileError) as exc:
        return _error(name, exc.message)
