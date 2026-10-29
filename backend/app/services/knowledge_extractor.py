"""从已清洗 JSON 中按明确规则抽取候选实体和关系。

只读取该 file_id 对应的清洗结果。不调用 DeepSeek，不修改清洗 JSON，
也不写入正式图谱。正文只作为待抽取的数据，不会被当作命令执行。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime

from app.services.knowledge_file_store import (
    CLEANED_STATUS,
    KnowledgeFileError,
    cleaned_dir,
    extracted_dir,
    get_uploaded_record,
    valid_file_id,
)

SCHEMA_VERSION = "1.0"
NOTICE = "候选知识尚未加入知识图谱。"
PREVIEW_LIMIT = 20
PREVIEW_QUOTE = 200
_STALE_SECONDS = 120
# 以下类型和关系来自 data/processed/graph/graph.json 的实际统计，不是假设。
GRAPH_ENTITY_TYPES = frozenset(
    {"action", "case", "component", "device", "fault", "feature", "parameter", "source"}
)
GRAPH_RELATIONS = frozenset(
    {
        "CONNECTS_TO",
        "HAS_ACTION",
        "HAS_COMPONENT",
        "HAS_FAULT",
        "HAS_FEATURE",
        "HAS_PARAMETER",
        "OCCURS_ON",
        "SUPPORTED_BY",
    }
)
# 正式图谱里这些关系的端点类型是固定的：不能因为名称相近就套用。
GRAPH_RELATION_ENDPOINTS = {
    "HAS_COMPONENT": {("device", "component"), ("component", "component")},
    "HAS_FAULT": {("component", "fault")},
    "HAS_FEATURE": {("fault", "feature")},
    "HAS_PARAMETER": {("device", "parameter")},
    "HAS_ACTION": {("case", "action")},
}
HEADER_ROLES = {
    "设备": "device",
    "部件": "component",
    "故障": "fault",
    "特征": "feature",
    "故障特征": "feature",
    "原因": "cause",
    "处置": "action",
    "处置措施": "action",
    "措施": "action",
    "参数": "parameter",
    "参数名称": "parameter",
    "数值": "value",
    "单位": "unit",
}
_UNCERTAIN_WORDS = ("可能", "疑似", "待排查", "怀疑", "不排除", "或许")
_SENTENCE_SPLIT = re.compile(r"[。；;\n]+")
_SPACE = re.compile(r"[ \t\u00a0]+")
_POSSIBLE_CAUSE = re.compile(r"^(?P<effect>.+?)可能由(?P<cause>.+?)引起$")
_BARE_POSSIBLE_CAUSE = re.compile(r"^可能由(?P<cause>.+?)引起$")
_EXPLICIT_CAUSE = re.compile(r"^(?P<effect>.+?)由(?P<cause>.+?)引起$")
_POSSIBLE_LEADS = re.compile(r"^(?P<cause>.+?)可能导致(?P<effect>.+)$")
_EXPLICIT_LEADS = re.compile(r"^(?P<cause>.+?)导致(?P<effect>.+)$")
_FEATURE = re.compile(r"^(?P<fault>.+?)具有(?P<feature>.+?)特征$")
_ACTION = re.compile(r"^(?P<fault>.+?)的处置措施(?:为|是)(?P<action>.+)$")
_PARAMETER = re.compile(
    r"^(?P<kind>设备|部件)(?P<owner>.+?)的(?P<name>[\u4e00-\u9fffA-Za-z]{1,20})为(?P<value>\d+(?:\.\d+)?)(?P<unit>[\u4e00-\u9fffA-Za-z°℃/%μ·.]{0,12})$"
)
_CONTAINS = re.compile(r"^(?P<left>.+?)包含(?P<right>.+)$")
_NUMBER = re.compile(r"^\d+(?:\.\d+)?$")
_LOCKS: dict[str, threading.Lock] = {}
_LOCK_GUARD = threading.Lock()


class ExtractFailure(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _stable_id(prefix: str, *parts: str) -> str:
    raw = "\n".join(parts)
    return prefix + "-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _normalize(text: str) -> str:
    return _SPACE.sub(" ", text.replace("\r\n", "\n").replace("\r", "\n")).strip()


def _is_uncertain(text: str) -> bool:
    return any(word in text for word in _UNCERTAIN_WORDS)


def _label(value: str, limit: int) -> str:
    text = _normalize(value).strip(" ，,、:：;；")
    if not text or len(text) > limit or any(char in text for char in "\n\r\t"):
        return ""
    if _is_uncertain(text) or "引起" in text or "导致" in text:
        return ""
    return text


def _thread_lock(file_id: str) -> threading.Lock:
    with _LOCK_GUARD:
        if file_id not in _LOCKS:
            _LOCKS[file_id] = threading.Lock()
        return _LOCKS[file_id]


def _result_path(file_id: str):
    directory = extracted_dir().resolve()
    target = (directory / f"{file_id}.json").resolve()
    if target.parent != directory or not target.is_file():
        return None
    return target


def _cleaned_path(file_id: str):
    directory = cleaned_dir().resolve()
    target = (directory / f"{file_id}.json").resolve()
    if target.parent != directory or not target.is_file():
        return None
    return target


def _lock_path(file_id: str):
    return extracted_dir().resolve() / f".{file_id}.lock"


def _acquire(file_id: str) -> bool:
    lock = _thread_lock(file_id)
    if not lock.acquire(blocking=False):
        return False
    path = _lock_path(file_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and time.time() - path.stat().st_mtime < _STALE_SECONDS:
            lock.release()
            return False
        path.write_text(file_id, encoding="utf-8")
    except OSError:
        lock.release()
        raise
    return True


def _release(file_id: str) -> None:
    path = _lock_path(file_id)
    try:
        if path.is_file():
            path.unlink()
    finally:
        _thread_lock(file_id).release()


def _record(file_id: str) -> dict:
    if not valid_file_id(file_id):
        raise KnowledgeFileError("没有这个上传文件。", 404)
    record = get_uploaded_record(file_id)
    if record is None:
        raise KnowledgeFileError("没有这个上传文件。", 404)
    record.pop("stored_name", None)
    return record


def _load_json(path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExtractFailure("候选知识或清洗结果无法读取。") from exc
    if not isinstance(payload, dict):
        raise ExtractFailure("结果不是 JSON 对象。")
    return payload


def _write(file_id: str, document: dict) -> None:
    directory = extracted_dir().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    target = (directory / f"{file_id}.json").resolve()
    if target.parent != directory:
        raise KnowledgeFileError("候选知识不能写到抽取目录之外。", 500)
    temporary = directory / f".{file_id}.{uuid.uuid4().hex}.partial"
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)


def _evidence(
    record: dict,
    section: dict,
    quote: str,
    method: str,
    extra: dict | None = None,
) -> dict:
    item = {
        "file_id": record["file_id"],
        "original_filename": record["original_filename"],
        "sha256": record["sha256"],
        "section_id": section.get("section_id"),
        "heading": section.get("heading") or "",
        "locator": section.get("locator"),
        "quote": quote,
        "method": method,
        "candidate_status": "candidate",
    }
    if extra:
        item.update(extra)
    return item


class _Catalog:
    def __init__(self, record: dict) -> None:
        self.record = record
        self.entities: dict[tuple, dict] = {}
        self.relations: dict[tuple, dict] = {}
        self.warnings: list[str] = []

    def warn(self, message: str) -> None:
        if message not in self.warnings and len(self.warnings) < 40:
            self.warnings.append(message)
        elif len(self.warnings) == 40 and message not in self.warnings:
            self.warnings.append("其余未识别内容已省略。这些内容需要后续语义抽取。")

    def add_entity(
        self,
        entity_type: str,
        label: str,
        *,
        certainty: str,
        evidence: dict,
        attributes: dict | None = None,
    ) -> str | None:
        name = _label(label, 80 if entity_type == "action" else 40)
        if not name:
            return None
        attrs = attributes or {}
        graph_type = entity_type if entity_type in GRAPH_ENTITY_TYPES else None
        mapped = graph_type is not None
        review = "explicit" if mapped and certainty == "explicit" else ("uncertain" if certainty == "uncertain" and mapped else "needs_review")
        if not mapped:
            review = "needs_review"
        value = str(attrs.get("value", ""))
        unit = str(attrs.get("unit", ""))
        scope = str(attrs.get("scope", ""))
        key = (entity_type, name, value, unit, scope)
        entity_id = _stable_id("candidate", self.record["file_id"], entity_type, name, value, unit, scope)
        current = self.entities.get(key)
        if current is None:
            current = {
                "entity_id": entity_id,
                "label": name,
                "entity_type": entity_type,
                "graph_type": graph_type,
                "mapped_to_graph": mapped,
                "review_status": review,
                "certainty": certainty,
                "candidate_status": "candidate",
                "in_graph": False,
                "attributes": attrs,
                "evidence": [],
            }
            self.entities[key] = current
        else:
            if certainty == "uncertain":
                current["certainty"] = "uncertain"
                current["review_status"] = "needs_review" if not current["mapped_to_graph"] else "uncertain"
        self._add_evidence(current["evidence"], evidence)
        return current["entity_id"]

    def add_relation(
        self,
        source_id: str | None,
        target_id: str | None,
        relation_type: str,
        graph_relation: str | None,
        *,
        certainty: str,
        evidence: dict,
    ) -> None:
        if not source_id or not target_id or source_id == target_id:
            return
        source = self._by_id(source_id)
        target = self._by_id(target_id)
        if source is None or target is None:
            return
        if source["entity_type"] == "fault" and target["entity_type"] == "fault":
            self.warn("同一处出现的两个故障名称没有建立因果关系。")
            return
        quote = str(evidence.get("quote") or "")
        mapped = False
        official = None
        if certainty == "explicit" and not _is_uncertain(quote) and graph_relation in GRAPH_RELATIONS:
            endpoints = GRAPH_RELATION_ENDPOINTS.get(graph_relation, set())
            pair = (source["entity_type"], target["entity_type"])
            if pair in endpoints:
                mapped = True
                official = graph_relation
        if not mapped:
            graph_relation = None
            if relation_type in GRAPH_RELATIONS:
                relation_type = "UNMAPPED_" + relation_type
        review = "explicit" if mapped else "needs_review"
        if certainty == "uncertain":
            certainty = "uncertain"
            if mapped:
                review = "uncertain"
        key = (source_id, target_id, relation_type, certainty)
        relation_id = _stable_id("candidate-relation", self.record["file_id"], *key)
        current = self.relations.get(key)
        if current is None:
            current = {
                "relation_id": relation_id,
                "source": source_id,
                "target": target_id,
                "source_label": source["label"],
                "target_label": target["label"],
                "source_entity_type": source["entity_type"],
                "target_entity_type": target["entity_type"],
                "relation_type": relation_type,
                "graph_relation": official,
                "mapped_to_graph": mapped,
                "review_status": review,
                "certainty": certainty,
                "candidate_status": "candidate",
                "in_graph": False,
                "direction": "cause_to_effect" if relation_type == "CAUSE_CANDIDATE" else "",
                "causal_status": "unverified" if relation_type == "CAUSE_CANDIDATE" else "",
                "evidence": [],
            }
            self.relations[key] = current
        self._add_evidence(current["evidence"], evidence)

    def _by_id(self, entity_id: str) -> dict | None:
        for item in self.entities.values():
            if item["entity_id"] == entity_id:
                return item
        return None

    def _add_evidence(self, bucket: list[dict], evidence: dict) -> None:
        marker = (
            evidence.get("section_id"),
            evidence.get("quote"),
            evidence.get("method"),
            json.dumps(evidence.get("locator"), ensure_ascii=False, sort_keys=True),
            evidence.get("table_row"),
        )
        for item in bucket:
            old = (
                item.get("section_id"),
                item.get("quote"),
                item.get("method"),
                json.dumps(item.get("locator"), ensure_ascii=False, sort_keys=True),
                item.get("table_row"),
            )
            if old == marker:
                return
        bucket.append(evidence)

    def document(self) -> dict:
        entities = sorted(self.entities.values(), key=lambda item: item["entity_id"])
        relations = sorted(self.relations.values(), key=lambda item: item["relation_id"])
        return {
            "schema_version": SCHEMA_VERSION,
            "file_id": self.record["file_id"],
            "source_sha256": self.record["sha256"],
            "source": {
                "original_filename": self.record["original_filename"],
                "source_type": self.record["source_type"],
                "sha256": self.record["sha256"],
            },
            "entities": entities,
            "relations": relations,
            "warnings": list(self.warnings),
            "extraction_status": "extracted",
            "error_message": "",
            "extracted_at": _now(),
            "in_graph": False,
            "notice": NOTICE,
        }


def _sentences(text: str) -> list[str]:
    return [_normalize(part) for part in _SENTENCE_SPLIT.split(text) if _normalize(part)]


def _split_items(text: str) -> list[str]:
    parts = re.split(r"[、，,]", text)
    labels = [_label(part, 40) for part in parts]
    if not labels or any(not item for item in labels):
        return []
    return labels


def _link_cause(catalog: _Catalog, section: dict, sentence: str, cause_text: str, effect_text: str, certainty: str, method: str) -> bool:
    evidence = _evidence(catalog.record, section, sentence, method)
    cause = catalog.add_entity("cause", cause_text, certainty=certainty, evidence=evidence)
    effect = catalog.add_entity("fault", effect_text, certainty=certainty, evidence=evidence)
    catalog.add_relation(cause, effect, "CAUSE_CANDIDATE", None, certainty=certainty, evidence=evidence)
    return cause is not None and effect is not None


def _apply_sentence(catalog: _Catalog, section: dict, sentence: str) -> bool:
    record = catalog.record
    possible = _POSSIBLE_CAUSE.fullmatch(sentence)
    if possible:
        return _link_cause(catalog, section, sentence, possible.group("cause"), possible.group("effect"), "uncertain", "rule:possible_cause")
    bare = _BARE_POSSIBLE_CAUSE.fullmatch(sentence)
    if bare:
        created = catalog.add_entity(
            "cause",
            bare.group("cause"),
            certainty="uncertain",
            evidence=_evidence(record, section, sentence, "rule:possible_cause"),
        )
        return created is not None
    if "可能由" not in sentence:
        explicit = _EXPLICIT_CAUSE.fullmatch(sentence)
        if explicit:
            return _link_cause(catalog, section, sentence, explicit.group("cause"), explicit.group("effect"), "explicit", "rule:explicit_cause")
    possible_leads = _POSSIBLE_LEADS.fullmatch(sentence)
    if possible_leads:
        return _link_cause(catalog, section, sentence, possible_leads.group("cause"), possible_leads.group("effect"), "uncertain", "rule:possible_cause")
    if not _is_uncertain(sentence):
        leads = _EXPLICIT_LEADS.fullmatch(sentence)
        if leads:
            return _link_cause(catalog, section, sentence, leads.group("cause"), leads.group("effect"), "explicit", "rule:explicit_cause")
    feature = _FEATURE.fullmatch(sentence)
    if feature:
        certainty = "uncertain" if _is_uncertain(sentence) else "explicit"
        fault = catalog.add_entity("fault", feature.group("fault"), certainty=certainty, evidence=_evidence(record, section, sentence, "rule:fault_has_feature"))
        trait = catalog.add_entity("feature", feature.group("feature"), certainty=certainty, evidence=_evidence(record, section, sentence, "rule:fault_has_feature"))
        catalog.add_relation(
            fault,
            trait,
            "HAS_FEATURE",
            "HAS_FEATURE",
            certainty=certainty,
            evidence=_evidence(record, section, sentence, "rule:fault_has_feature"),
        )
        return fault is not None and trait is not None
    action = _ACTION.fullmatch(sentence)
    if action:
        certainty = "uncertain" if _is_uncertain(sentence) else "explicit"
        fault = catalog.add_entity("fault", action.group("fault"), certainty=certainty, evidence=_evidence(record, section, sentence, "rule:fault_action"))
        step = catalog.add_entity("action", action.group("action"), certainty=certainty, evidence=_evidence(record, section, sentence, "rule:fault_action"))
        catalog.add_relation(
            fault,
            step,
            "FAULT_ACTION_CANDIDATE",
            None,
            certainty=certainty,
            evidence=_evidence(record, section, sentence, "rule:fault_action"),
        )
        return fault is not None and step is not None
    parameter = _PARAMETER.fullmatch(sentence)
    if parameter:
        certainty = "uncertain" if _is_uncertain(sentence) else "explicit"
        kind = "device" if parameter.group("kind") == "设备" else "component"
        method = "rule:device_parameter" if kind == "device" else "rule:component_parameter"
        relation = "HAS_PARAMETER" if kind == "device" else "COMPONENT_PARAMETER_CANDIDATE"
        official = "HAS_PARAMETER" if kind == "device" else None
        owner_name = _label(parameter.group("owner"), 40)
        owner = catalog.add_entity(kind, owner_name, certainty=certainty, evidence=_evidence(record, section, sentence, method))
        param = catalog.add_entity(
            "parameter",
            parameter.group("name"),
            certainty=certainty,
            evidence=_evidence(record, section, sentence, method),
            attributes={"value": parameter.group("value"), "unit": parameter.group("unit"), "scope": owner_name},
        )
        catalog.add_relation(owner, param, relation, official, certainty=certainty, evidence=_evidence(record, section, sentence, method))
        return owner is not None and param is not None
    contains = _CONTAINS.fullmatch(sentence)
    if contains and not any(word in sentence for word in ("故障", "原因", "特征", "处置", "引起")):
        certainty = "uncertain" if _is_uncertain(sentence) else "explicit"
        left_text = contains.group("left")
        left_type = "component" if left_text.startswith("部件") else "device"
        left_label = left_text[2:] if left_text.startswith(("设备", "部件")) else left_text
        children = _split_items(contains.group("right"))
        parent_name = _label(left_label, 40)
        parent = catalog.add_entity(left_type, parent_name, certainty=certainty, evidence=_evidence(record, section, sentence, "rule:contains"))
        created = False
        for child in children:
            component = catalog.add_entity(
                "component",
                child,
                certainty=certainty,
                evidence=_evidence(record, section, sentence, "rule:contains"),
                attributes={"scope": parent_name},
            )
            catalog.add_relation(
                parent,
                component,
                "HAS_COMPONENT",
                "HAS_COMPONENT",
                certainty=certainty,
                evidence=_evidence(record, section, sentence, "rule:contains"),
            )
            created = created or component is not None
        return parent is not None and created
    return False


def _section_text(section: dict) -> str:
    return _normalize(str(section.get("text") or ""))


def _header_roles(cells: list[str]) -> list[str] | None:
    if not cells or any(not cell for cell in cells):
        return None
    roles = []
    for cell in cells:
        role = HEADER_ROLES.get(cell)
        if role is None or role in roles:
            return None
        roles.append(role)
    if not roles:
        return None
    return roles


def _cell_label(text: str, uncertain: bool) -> str:
    cleaned = text
    if uncertain:
        for word in ("可能由", "待排查", "不排除", "疑似", "怀疑", "或许", "可能", "引起"):
            cleaned = cleaned.replace(word, "")
    return _label(cleaned, 40)


def _apply_table(
    catalog: _Catalog,
    section: dict,
    rows: list[list[str]],
    *,
    row_sections: list[dict] | None = None,
) -> bool:
    if len(rows) < 2:
        return False
    roles = _header_roles(rows[0])
    if roles is None:
        return False
    record = catalog.record
    header_quote = "\t".join(rows[0])
    for offset, row in enumerate(rows[1:], start=1):
        padded = list(row) + [""] * (len(roles) - len(row))
        values: dict[str, str] = {}
        raw_cells = padded[: len(roles)]
        uncertain = any(_is_uncertain(cell) for cell in raw_cells)
        for role, cell in zip(roles, raw_cells):
            label = _cell_label(cell, uncertain) if role not in {"value", "unit"} else _normalize(cell)
            if role in {"value", "unit"}:
                values[role] = label
            elif label:
                values[role] = label
        quote = f"表头：{header_quote}；本行：{chr(9).join(raw_cells)}"
        current = section
        if row_sections and offset < len(row_sections):
            current = row_sections[offset]
        header_locator = row_sections[0].get("locator") if row_sections else section.get("locator")
        evidence = _evidence(
            record,
            current,
            quote,
            "rule:table",
            {"table_row": offset + 1, "header_locator": header_locator},
        )
        certainty = "uncertain" if uncertain else "explicit"
        if "device" in values and "component" in values:
            parent = catalog.add_entity("device", values["device"], certainty=certainty, evidence=evidence)
            child = catalog.add_entity(
                "component",
                values["component"],
                certainty=certainty,
                evidence=evidence,
                attributes={"scope": values["device"]},
            )
            catalog.add_relation(parent, child, "HAS_COMPONENT", "HAS_COMPONENT", certainty=certainty, evidence=evidence)
        if "component" in values and "fault" in values:
            component = catalog.add_entity("component", values["component"], certainty=certainty, evidence=evidence)
            fault = catalog.add_entity("fault", values["fault"], certainty=certainty, evidence=evidence)
            catalog.add_relation(component, fault, "HAS_FAULT", "HAS_FAULT", certainty=certainty, evidence=evidence)
        elif "device" in values and "fault" in values:
            device = catalog.add_entity("device", values["device"], certainty=certainty, evidence=evidence)
            fault = catalog.add_entity("fault", values["fault"], certainty=certainty, evidence=evidence)
            catalog.add_relation(device, fault, "DEVICE_FAULT_CANDIDATE", None, certainty=certainty, evidence=evidence)
        if "fault" in values and "feature" in values:
            fault = catalog.add_entity("fault", values["fault"], certainty=certainty, evidence=evidence)
            feature = catalog.add_entity("feature", values["feature"], certainty=certainty, evidence=evidence)
            catalog.add_relation(fault, feature, "HAS_FEATURE", "HAS_FEATURE", certainty=certainty, evidence=evidence)
        if "fault" in values and "cause" in values:
            cause = catalog.add_entity("cause", values["cause"], certainty=certainty, evidence=evidence)
            fault = catalog.add_entity("fault", values["fault"], certainty=certainty, evidence=evidence)
            catalog.add_relation(cause, fault, "CAUSE_CANDIDATE", None, certainty=certainty, evidence=evidence)
        if "fault" in values and "action" in values:
            fault = catalog.add_entity("fault", values["fault"], certainty=certainty, evidence=evidence)
            action = catalog.add_entity("action", values["action"], certainty=certainty, evidence=evidence)
            catalog.add_relation(fault, action, "FAULT_ACTION_CANDIDATE", None, certainty=certainty, evidence=evidence)
        if "parameter" in values and _NUMBER.fullmatch(values.get("value", "")):
            owner_type = "device" if "device" in values else "component" if "component" in values else ""
            if owner_type:
                official = "HAS_PARAMETER" if owner_type == "device" else None
                relation = "HAS_PARAMETER" if owner_type == "device" else "COMPONENT_PARAMETER_CANDIDATE"
                owner = catalog.add_entity(owner_type, values[owner_type], certainty=certainty, evidence=evidence)
                param = catalog.add_entity(
                    "parameter",
                    values["parameter"],
                    certainty=certainty,
                    evidence=evidence,
                    attributes={"value": values["value"], "unit": values.get("unit", ""), "scope": values[owner_type]},
                )
                catalog.add_relation(owner, param, relation, official, certainty=certainty, evidence=evidence)
    return True


def _loose_cells(catalog: _Catalog, section: dict, cells: list[str]) -> None:
    matched = False
    for cell in cells:
        for sentence in _sentences(cell):
            matched = _apply_sentence(catalog, section, sentence) or matched
    if not matched:
        catalog.warn(f"{section.get('section_id')} 没有可识别表头，未跨行建立关系，需要后续语义抽取。")


def _consume_csv(catalog: _Catalog, sections: list[dict], index: int) -> int:
    first = sections[index]
    rows = []
    cursor = index
    while cursor < len(sections) and sections[cursor].get("kind") == "csv_row":
        cells = sections[cursor].get("cells")
        if not isinstance(cells, list):
            break
        rows.append([_normalize(str(cell)) for cell in cells])
        cursor += 1
    if len(rows) >= 2 and _header_roles(rows[0]) is not None:
        _apply_table(catalog, first, rows, row_sections=sections[index:cursor])
        return cursor
    _loose_cells(catalog, first, rows[0] if rows else [])
    return index + 1


def _build(record: dict) -> dict:
    path = _cleaned_path(record["file_id"])
    if path is None:
        raise ExtractFailure("已清洗的文件没有找到清洗结果，没有生成候选知识。")
    document = _load_json(path)
    if document.get("file_id") != record["file_id"]:
        raise ExtractFailure("清洗结果不属于这个文件，没有生成候选知识。")
    source = document.get("source") if isinstance(document.get("source"), dict) else {}
    if source.get("sha256") != record["sha256"]:
        raise ExtractFailure("清洗结果和上传文件的哈希不一致，没有覆盖已有抽取结果。")
    sections = document.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ExtractFailure("清洗结果没有正文，没有生成候选知识。")
    catalog = _Catalog(record)
    index = 0
    while index < len(sections):
        section = sections[index]
        if not isinstance(section, dict):
            index += 1
            continue
        kind = section.get("kind")
        if kind == "csv_row":
            index = _consume_csv(catalog, sections, index)
            continue
        if kind == "table":
            table = section.get("table") if isinstance(section.get("table"), dict) else {}
            rows = table.get("rows") if isinstance(table.get("rows"), list) else []
            normalized = []
            for row in rows:
                if isinstance(row, list):
                    normalized.append([_normalize(str(cell)) for cell in row])
            if normalized and _apply_table(catalog, section, normalized):
                index += 1
                continue
            if normalized:
                for row in normalized:
                    _loose_cells(catalog, section, row)
                index += 1
                continue
        text = _section_text(section)
        matched = False
        for sentence in _sentences(text):
            matched = _apply_sentence(catalog, section, sentence) or matched
        if text and not matched:
            catalog.warn(f"{section.get('section_id')} 未按规则识别，需要后续语义抽取。")
        index += 1
    return catalog.document()


def _failure_document(record: dict, message: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "file_id": record["file_id"],
        "source_sha256": record["sha256"],
        "source": {
            "original_filename": record["original_filename"],
            "source_type": record["source_type"],
            "sha256": record["sha256"],
        },
        "entities": [],
        "relations": [],
        "warnings": [],
        "extraction_status": "extraction_failed",
        "error_message": message,
        "extracted_at": _now(),
        "in_graph": False,
        "notice": NOTICE,
    }


def _clip_quote(evidence: dict) -> dict:
    item = dict(evidence)
    quote = str(item.get("quote") or "")
    if len(quote) > PREVIEW_QUOTE:
        item["quote"] = quote[:PREVIEW_QUOTE]
        item["quote_truncated"] = True
    else:
        item["quote_truncated"] = False
    return item


def _clip_item(item: dict) -> dict:
    copied = dict(item)
    evidence = copied.get("evidence")
    if isinstance(evidence, list):
        copied["evidence"] = [_clip_quote(entry) for entry in evidence[:3] if isinstance(entry, dict)]
        copied["evidence_truncated"] = len(evidence) > 3
    return copied


def extracted_preview(file_id: str) -> dict:
    record = _record(file_id)
    body = {
        "file_id": record["file_id"],
        "original_filename": record["original_filename"],
        "processing_status": record["processing_status"],
        "extraction_status": "not_extracted",
        "error_message": "",
        "entity_count": 0,
        "relation_count": 0,
        "preview": None,
        "preview_truncated": False,
        "in_graph": False,
        "notice": NOTICE,
    }
    if record["processing_status"] != CLEANED_STATUS:
        body["error_message"] = "只有已清洗的文件才能抽取候选知识。"
        return body
    path = _result_path(file_id)
    if path is None:
        return body
    try:
        document = _load_json(path)
    except ExtractFailure as exc:
        raise KnowledgeFileError(exc.message, 500) from exc
    status = document.get("extraction_status")
    entities = document.get("entities") if isinstance(document.get("entities"), list) else []
    relations = document.get("relations") if isinstance(document.get("relations"), list) else []
    body["extraction_status"] = status if isinstance(status, str) else "extraction_failed"
    body["error_message"] = str(document.get("error_message") or "")
    body["entity_count"] = len(entities)
    body["relation_count"] = len(relations)
    if body["extraction_status"] != "extracted":
        return body
    preview_entities = [_clip_item(item) for item in entities[:PREVIEW_LIMIT] if isinstance(item, dict)]
    preview_relations = [_clip_item(item) for item in relations[:PREVIEW_LIMIT] if isinstance(item, dict)]
    body["preview_truncated"] = len(entities) > PREVIEW_LIMIT or len(relations) > PREVIEW_LIMIT
    body["preview"] = {
        "schema_version": document.get("schema_version"),
        "file_id": document.get("file_id"),
        "source_sha256": document.get("source_sha256"),
        "source": document.get("source") if isinstance(document.get("source"), dict) else {},
        "entities": preview_entities,
        "relations": preview_relations,
        "warnings": document.get("warnings") if isinstance(document.get("warnings"), list) else [],
        "extraction_status": "extracted",
        "in_graph": False,
        "notice": NOTICE,
    }
    return body


def extract_knowledge(file_id: str) -> dict:
    record = _record(file_id)
    if record["processing_status"] != CLEANED_STATUS:
        raise KnowledgeFileError("只有已清洗的文件才能抽取候选知识。", 409)
    if not _acquire(file_id):
        current = extracted_preview(file_id)
        if current["extraction_status"] == "not_extracted":
            current["extraction_status"] = "extracting"
            current["error_message"] = "正在抽取，尚未生成结果。"
        return current
    try:
        previous = None
        path = _result_path(file_id)
        if path is not None:
            try:
                loaded = _load_json(path)
            except ExtractFailure:
                loaded = None
            if isinstance(loaded, dict) and loaded.get("extraction_status") == "extracted":
                previous = loaded
        try:
            document = _build(record)
        except ExtractFailure as exc:
            if previous is not None:
                raise KnowledgeFileError(f"重新抽取没有成功，已保留上一次结果。{exc.message}", 422) from exc
            _write(file_id, _failure_document(record, exc.message))
            raise KnowledgeFileError(exc.message, 422) from exc
        _write(file_id, document)
        return extracted_preview(file_id)
    finally:
        _release(file_id)
