"""验收跨知识证据检索。写入只发生在临时目录。"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

TEMP = Path(tempfile.mkdtemp(prefix="knowledge-reason-"))
os.environ["KNOWLEDGE_FILES_DB"] = str(TEMP / "files.sqlite3")
os.environ["KNOWLEDGE_UPLOAD_DIR"] = str(TEMP / "uploads")
os.environ["KNOWLEDGE_CLEANED_DIR"] = str(TEMP / "cleaned")
os.environ["KNOWLEDGE_EXTRACTED_DIR"] = str(TEMP / "extracted")
os.environ["KNOWLEDGE_IMPORT_PREVIEW_DIR"] = str(TEMP / "previews")
os.environ["KNOWLEDGE_GRAPH_IMPORTS_DB"] = str(TEMP / "imports.sqlite3")
os.environ["STARTUP_TOOL_SELFTEST"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.knowledge_reasoning_context import find_cross_knowledge_evidence  # noqa: E402

CHECKS: list[bool] = []
GRAPH = ROOT / "data" / "processed" / "graph" / "graph.json"
EXCEL = ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"
SQLITE = ROOT / "data" / "runtime" / "monitoring_events.sqlite3"
DEFAULT_IMPORTS = ROOT / "data" / "runtime" / "knowledge_graph_imports.sqlite3"
EXPECTED_XLSX = "1e9caeba1fe526f3ad1b5dd6445a9dbcc02fa22ec02273d9863ebc84e3e30f87"
ANSWER_LABELS = ("设备状态", "故障类型", "故障严重度", "故障位置", "诊断置信度", "频谱描述", "根因分析", "建议措施", "优先级", "影响程度")


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append(bool(ok))
    print(("PASS" if ok else "FAIL"), name, detail)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ask(client: TestClient, left: str, right: str, hops: int = 3):
    return client.post("/api/knowledge/research/evidence", json={"entity_a": left, "entity_b": right, "max_hops": hops})


def node(node_id: str, label: str, node_type: str = "component") -> dict:
    return {"id": node_id, "data": {"label": label, "type": node_type, "source_refs": [{"document": "测试", "section": "s", "locator": "行 1"}]}}


def edge(edge_id: str, source: str, target: str, relation: str) -> dict:
    return {
        "id": edge_id,
        "source": source,
        "target": target,
        "data": {"relation": relation, "source_refs": [{"document": "测试", "section": "s", "locator": "行 1", "quote": "测试"}]},
    }


def main() -> None:
    before_graph = file_hash(GRAPH)
    before_excel = file_hash(EXCEL)
    before_sqlite = file_hash(SQLITE) if SQLITE.is_file() else None
    before_default = DEFAULT_IMPORTS.exists()
    source = (BACKEND / "app" / "services" / "knowledge_reasoning_context.py").read_text(encoding="utf-8")
    modules = ast.parse(source)
    imported: set[str] = set()
    for item in ast.walk(modules):
        if isinstance(item, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in item.names)
        elif isinstance(item, ast.ImportFrom) and item.module:
            imported.update(item.module.split("."))
    banned = imported & {"deepseek", "requests", "httpx", "monitoring_events", "anomaly_detection", "diagnostic_bridge", "agent_tools"}
    check("检索模块没有调用 DeepSeek、监测或五个工具", not banned and "sk-" not in source and all(label not in source for label in ANSWER_LABELS), str(sorted(banned)))

    client = TestClient(app)
    linked = ask(client, "棒线主轧机电机", "定子")
    body = linked.json()
    path = body.get("connection_paths", [{}])[0]
    step = path.get("steps", [{}])[0] if path else {}
    evidence = step.get("evidence") or []
    check(
        "已有设备和部件能查到真实包含路径",
        linked.status_code == 200
        and body["entity_a_matches"][0]["node_id"] == "device-f803cb74ec718d83"
        and body["entity_b_matches"][0]["node_id"] == "component-9e4adc132ac1a86b"
        and body["summary"]["found_path"] is True
        and body["causal_conclusion"] is False
        and step.get("relation") == "HAS_COMPONENT"
        and step.get("source_id") == "device-f803cb74ec718d83"
        and step.get("target_id") == "component-9e4adc132ac1a86b"
        and step.get("traversal") == "along_arrow"
        and evidence
        and evidence[0]["document"] == "KB-01_设备知识图谱.docx"
        and evidence[0]["location_complete"] is True
        and evidence[0]["quote_available"] is False
        and "没有保存可逐字核对的原文片段" in "\n".join(body["evidence_gaps"]),
        linked.text[:600],
    )

    missing = ask(client, "不存在的试验设备甲", "不存在的试验部件乙").json()
    check(
        "图谱中没有的实体不会生成关系",
        missing["entity_a_matches"] == []
        and missing["entity_b_matches"] == []
        and missing["connection_paths"] == []
        and missing["causal_conclusion"] is False
        and "没有找到「不存在的试验设备甲」" in "\n".join(missing["evidence_gaps"])
        and "没有找到现有知识支持的关联路径" in missing["summary"]["text"],
    )

    ambiguous = ask(client, "外圈", "定子").json()
    scopes = {item["scope"] for item in ambiguous["entity_a_matches"]}
    check(
        "不同设备下的同名外圈不会合并",
        len(ambiguous["entity_a_matches"]) == 2
        and scopes == {"驱动端轴承", "非驱动端轴承"}
        and len({item["node_id"] for item in ambiguous["entity_a_matches"]}) == 2
        and ambiguous["connection_paths"] == []
        and "没有擅自合并或选择" in "\n".join(ambiguous["evidence_gaps"]),
        str(scopes),
    )

    short = ask(client, "定子", "转子", 1).json()
    longer = ask(client, "定子", "转子", 2).json()
    motor_in_path = any(
        step["from_id"] == "device-f803cb74ec718d83" or step["to_id"] == "device-f803cb74ec718d83"
        for path in longer["connection_paths"]
        for step in path["steps"]
    )
    check(
        "跳数限制生效，共同邻居不构成因果",
        short["connection_paths"] == []
        and longer["summary"]["found_path"] is True
        and motor_in_path
        and any(item["label"] == "棒线主轧机电机" and item["causal_conclusion"] is False for item in longer["shared_neighbors"])
        and "不能认定" in longer["summary"]["text"]
        and all(item["causal_conclusion"] is False for item in longer["connection_paths"]),
        longer["summary"]["text"],
    )

    citation = ask(client, "轴承磨损", "KB-02_故障模式诊断知识库.docx").json()
    relations = {step["relation"] for path in citation["connection_paths"] for step in path["steps"]}
    check(
        "来源引用没有变成工业路径",
        citation["entity_a_matches"]
        and citation["entity_b_matches"]
        and "SUPPORTED_BY" not in relations
        and citation["connection_paths"] == []
        and "SUPPORTED_BY" in "\n".join(citation["evidence_gaps"])
        and "UI_GROUP" not in relations,
    )

    gear = ask(client, "齿轮裂纹", "轴承磨损").json()
    check(
        "齿轮裂纹和轴承磨损没有被编造成交叉因果",
        gear["entity_a_matches"] == []
        and len(gear["entity_b_matches"]) == 1
        and gear["entity_b_matches"][0]["entity_type"] == "fault"
        and gear["connection_paths"] == []
        and gear["unverified_candidates"] == []
        and gear["causal_conclusion"] is False
        and "没有找到「齿轮裂纹」" in "\n".join(gear["evidence_gaps"])
        and "没有找到现有知识支持的关联路径" in gear["summary"]["text"]
        and "导致" not in gear["summary"]["text"],
        gear["summary"]["text"],
    )

    first = client.post(
        "/api/knowledge/files",
        params={"filename": "第一份护罩.txt"},
        content="棒线主轧机电机包含车间新增护罩".encode(),
        headers={"Content-Type": "application/octet-stream"},
    )
    second = client.post(
        "/api/knowledge/files",
        params={"filename": "第二份护罩.txt"},
        content="棒线主轧机电机包含车间新增护罩".encode(),
        headers={"Content-Type": "application/octet-stream"},
    )
    imported = ask(client, "棒线主轧机电机", "车间新增护罩").json()
    imported_steps = [step for path in imported["connection_paths"] for step in path["steps"] if step["relation"] == "HAS_COMPONENT"]
    imported_evidence = imported_steps[0]["evidence"] if imported_steps else []
    file_ids = {item["file_id"] for item in imported_evidence if item["file_id"]}
    check(
        "临时库中的新部件能查到，两份文件证据都保留",
        first.status_code == 200
        and second.status_code == 200
        and imported["entity_b_matches"]
        and imported["entity_b_matches"][0]["origin"] == "runtime_import"
        and imported["entity_b_matches"][0]["scope"] == "棒线主轧机电机"
        and imported_steps
        and imported_steps[0]["origin"] == "runtime_import"
        and len(file_ids) == 2
        and all(item["quote"] == "棒线主轧机电机包含车间新增护罩" for item in imported_evidence)
        and all(item["quote_available"] and item["location_complete"] for item in imported_evidence),
        json.dumps(imported_evidence, ensure_ascii=False)[:500],
    )

    candidate = {
        "schema_version": "1.0",
        "file_id": "f" * 32,
        "extraction_status": "extracted",
        "relations": [
            {
                "source_label": "齿轮裂纹",
                "target_label": "轴承磨损",
                "relation_type": "CAUSE_CANDIDATE",
                "graph_relation": None,
                "direction": "cause_to_effect",
                "certainty": "uncertain",
                "causal_status": "unverified",
                "in_graph": False,
                "evidence": [
                    {
                        "file_id": "f" * 32,
                        "original_filename": "候选因果.txt",
                        "section_id": "section-001",
                        "locator": {"type": "line", "index": 1},
                        "quote": "齿轮裂纹可能引起轴承磨损",
                    }
                ],
            }
        ],
    }
    extracted = TEMP / "extracted"
    extracted.mkdir(parents=True, exist_ok=True)
    (extracted / f"{'f' * 32}.json").write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")
    hypothesis = ask(client, "齿轮裂纹", "轴承磨损").json()
    candidate_row = hypothesis["unverified_candidates"][0] if hypothesis["unverified_candidates"] else {}
    check(
        "未验证因果只出现在待验证假设中",
        hypothesis["connection_paths"] == []
        and candidate_row.get("fact_class") == "unverified_candidate"
        and candidate_row.get("causal_status") == "unverified"
        and candidate_row.get("in_graph") is False
        and candidate_row.get("evidence", [{}])[0].get("quote") == "齿轮裂纹可能引起轴承磨损"
        and candidate_row.get("evidence", [{}])[0].get("locator") == "行 1"
        and "CAUSE_CANDIDATE" not in json.dumps(hypothesis["connection_paths"], ensure_ascii=False)
        and "未验证候选" in hypothesis["summary"]["text"],
        hypothesis["summary"]["text"],
    )

    nodes = [node("a", "甲", "device"), node("b", "乙", "fault")]
    edges = []
    for index in range(25):
        middle = f"m{index}"
        nodes.append(node(middle, f"中{index}"))
        edges.append(edge(f"e{index}a", "a", middle, "HAS_COMPONENT"))
        edges.append(edge(f"e{index}b", middle, "b", "HAS_FAULT"))
    limited = find_cross_knowledge_evidence("甲", "乙", 3, graph={"nodes": nodes, "edges": edges})
    cycle = find_cross_knowledge_evidence(
        "甲",
        "丙",
        3,
        graph={
            "nodes": [node("a", "甲", "device"), node("b", "乙"), node("c", "丙", "fault")],
            "edges": [
                edge("ab", "a", "b", "HAS_COMPONENT"),
                edge("bc", "b", "c", "HAS_FAULT"),
                edge("ca", "c", "a", "CONNECTS_TO"),
                edge("ui", "a", "c", "UI_GROUP"),
                edge("support", "a", "c", "SUPPORTED_BY"),
            ],
        },
    )
    cycle_relations = {step["relation"] for path in cycle["connection_paths"] for step in path["steps"]}
    check(
        "路径数量有上限，导航和引用边不会进事实路径",
        len(limited["connection_paths"]) == 20
        and limited["paths_truncated"] is True
        and limited["causal_conclusion"] is False
        and cycle["summary"]["found_path"] is True
        and "UI_GROUP" not in cycle_relations
        and "SUPPORTED_BY" not in cycle_relations
        and "UI_GROUP" in "\n".join(cycle["evidence_gaps"])
        and len(cycle["connection_paths"]) <= 20,
    )
    too_deep = client.post("/api/knowledge/research/evidence", json={"entity_a": "定子", "entity_b": "转子", "max_hops": 4})
    check("超过3跳被拒绝", too_deep.status_code == 422)

    response_text = json.dumps(gear, ensure_ascii=False) + json.dumps(imported, ensure_ascii=False)
    check("结果没有答案标签", all(label not in response_text for label in ANSWER_LABELS))
    check(
        "正式图谱和监测库没有被改写",
        file_hash(GRAPH) == before_graph
        and file_hash(EXCEL) == before_excel
        and (file_hash(SQLITE) if SQLITE.is_file() else None) == before_sqlite
        and DEFAULT_IMPORTS.exists() == before_default
        and len(json.loads(GRAPH.read_text(encoding="utf-8"))["nodes"]) == 252
        and len(json.loads(GRAPH.read_text(encoding="utf-8"))["edges"]) == 464,
    )
    print("PATH_RESULT")
    print(json.dumps({"relation": step.get("relation"), "document": evidence[0]["document"] if evidence else "", "locator": evidence[0]["locator"] if evidence else "", "causal": body["causal_conclusion"]}, ensure_ascii=False))
    print("GEAR_RESULT")
    print(json.dumps({"matches_a": len(gear["entity_a_matches"]), "matches_b": len(gear["entity_b_matches"]), "paths": len(gear["connection_paths"]), "summary": gear["summary"]["text"], "gaps": gear["evidence_gaps"]}, ensure_ascii=False))
    print(f"{sum(CHECKS)}/{len(CHECKS)} passed")
    if not all(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
