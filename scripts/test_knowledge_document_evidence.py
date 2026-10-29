"""验收原始文档证据检索。写入只发生在临时目录。"""

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

TEMP = Path(tempfile.mkdtemp(prefix="knowledge-docs-"))
os.environ["KNOWLEDGE_FILES_DB"] = str(TEMP / "files.sqlite3")
os.environ["KNOWLEDGE_UPLOAD_DIR"] = str(TEMP / "uploads")
os.environ["KNOWLEDGE_CLEANED_DIR"] = str(TEMP / "cleaned")
os.environ["KNOWLEDGE_EXTRACTED_DIR"] = str(TEMP / "extracted")
os.environ["KNOWLEDGE_IMPORT_PREVIEW_DIR"] = str(TEMP / "previews")
os.environ["KNOWLEDGE_GRAPH_IMPORTS_DB"] = str(TEMP / "imports.sqlite3")
os.environ["STARTUP_TOOL_SELFTEST"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

CHECKS: list[bool] = []
GRAPH = ROOT / "data" / "processed" / "graph" / "graph.json"
EXCEL = ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"
SQLITE = ROOT / "data" / "runtime" / "monitoring_events.sqlite3"
DEFAULT_IMPORTS = ROOT / "data" / "runtime" / "knowledge_graph_imports.sqlite3"
ANSWER_LABELS = ("设备状态", "故障类型", "故障严重度", "故障位置", "诊断置信度", "频谱描述", "根因分析", "建议措施", "优先级", "影响程度")
REQUIRED_KEYS = {
    "entity_a_matches",
    "entity_b_matches",
    "shared_neighbors",
    "connection_paths",
    "supporting_evidence",
    "unverified_candidates",
    "evidence_gaps",
    "summary",
    "document_evidence",
    "cross_document_evidence",
}


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


def upload(client: TestClient, filename: str, content: bytes):
    return client.post(
        "/api/knowledge/files",
        params={"filename": filename},
        content=content,
        headers={"Content-Type": "application/octet-stream"},
    )


def main() -> None:
    before_graph = file_hash(GRAPH)
    before_excel = file_hash(EXCEL)
    before_sqlite = file_hash(SQLITE) if SQLITE.is_file() else None
    before_default = DEFAULT_IMPORTS.exists()
    modules = [
        BACKEND / "app" / "services" / "knowledge_document_evidence.py",
        BACKEND / "app" / "services" / "knowledge_reasoning_context.py",
    ]
    imported: set[str] = set()
    for path in modules:
        for item in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(item, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in item.names)
            elif isinstance(item, ast.ImportFrom) and item.module:
                imported.update(item.module.split("."))
    banned = imported & {"deepseek", "requests", "httpx", "monitoring_events", "agent_tools"}
    check("文档检索没有调用 DeepSeek 或五个工具", not banned, str(sorted(banned)))

    client = TestClient(app)
    linked = ask(client, "棒线主轧机电机", "定子").json()
    step = linked["connection_paths"][0]["steps"][0]
    check(
        "原有设备与部件路径保持不变",
        step["relation"] == "HAS_COMPONENT"
        and step["source_id"] == "device-f803cb74ec718d83"
        and step["target_id"] == "component-9e4adc132ac1a86b"
        and linked["causal_conclusion"] is False
        and linked["summary"]["found_path"] is True
        and linked["document_evidence"]["external_model_allowed"] is False,
    )

    gear = ask(client, "齿轮裂纹", "轴承磨损").json()
    gear_direct = [item for item in gear["document_evidence"]["entity_a"] if item["direct_mention"]]
    bearing_direct = [item for item in gear["document_evidence"]["entity_b"] if item["direct_mention"] and item["retrieval_source"] == "official_rag"]
    bearing = bearing_direct[0] if bearing_direct else {}
    check(
        "齿轮裂纹没有图谱节点时仍返回关键词文档，但不建立关系",
        gear["entity_a_matches"] == []
        and len(gear["entity_b_matches"]) == 1
        and gear["connection_paths"] == []
        and gear["cross_document_evidence"] == []
        and gear_direct == []
        and gear["document_evidence"]["entity_a"]
        and all(item["match_kind"] == "keyword_related" and item["graph_relation_established"] is False for item in gear["document_evidence"]["entity_a"])
        and "未找到已入图的关联路径，但检索到可供进一步研究的文档证据。" in gear["summary"]["text"]
        and "没有图谱节点" in "\n".join(gear["evidence_gaps"]),
        gear["summary"]["text"],
    )
    check(
        "轴承磨损的官方证据保留文档、章节和行号",
        bearing.get("source_type") == "existing_knowledge"
        and bearing.get("original_filename") == "KB-02_故障模式诊断知识库.docx"
        and bearing.get("indexed_document") == "KB-02_故障模式诊断知识库.md"
        and bearing.get("official_document_id") == "KB-02_故障模式诊断知识库.md"
        and "轴承磨损" in bearing.get("section", "")
        and bearing.get("locator") == "第8-12行"
        and bearing.get("retrieval_method") == "bm25"
        and bearing.get("direct_mention") is True
        and bearing.get("in_graph") is True
        and bearing.get("graph_relation_established") is False
        and "轴承磨损" in bearing.get("snippet", "")
        and bearing.get("file_id") == ""
        and bearing.get("release_scope") == "local_only",
        json.dumps(bearing, ensure_ascii=False)[:500],
    )

    missing_name = "QX91ZZ-only-in-failed-clean"
    failed = upload(client, "失败样本.pdf", f"%PDF-1.4\n{missing_name}".encode())
    failed_body = failed.json()
    absent = ask(client, missing_name, "QY92ZZ-not-anywhere").json()
    snippets = "\n".join(item.get("snippet", "") for item in absent["document_evidence"]["entity_a"] + absent["document_evidence"]["entity_b"] + absent["cross_document_evidence"])
    cleaned_names = sorted(path.name for path in (TEMP / "cleaned").glob("*.json")) if (TEMP / "cleaned").is_dir() else []
    check(
        "清洗失败的文件和完全没有的名称都不产生证据",
        failed.status_code == 200
        and failed_body.get("processing_status") == "cleaning_failed"
        and absent["connection_paths"] == []
        and absent["document_evidence"]["entity_a"] == []
        and absent["document_evidence"]["entity_b"] == []
        and absent["cross_document_evidence"] == []
        and "文档中也没有检索到可用证据。" in absent["summary"]["text"]
        and missing_name not in snippets
        and all(not name.startswith(failed_body.get("file_id", "missing")) for name in cleaned_names),
        json.dumps({"status": failed.status_code, "processing_status": failed_body.get("processing_status"), "cleaned": cleaned_names, "summary": absent["summary"]["text"]}, ensure_ascii=False),
    )

    seal = upload(client, "密封圈记录.txt", "仓库备件记录试验密封圈渗油。".encode())
    seal_body = ask(client, "试验密封圈渗油", "QY92ZZ-not-anywhere").json()
    seal_hit = next((item for item in seal_body["document_evidence"]["entity_a"] if item["direct_mention"]), {})
    check(
        "图谱中没有的上传实体能追溯清洗原文",
        seal.status_code == 200
        and seal_body["entity_a_matches"] == []
        and seal_body["connection_paths"] == []
        and seal_hit.get("retrieval_source") == "cleaned_json"
        and seal_hit.get("source_type") == "user_upload"
        and seal_hit.get("file_id") == seal.json()["file_id"]
        and seal_hit.get("section_id") == "section-001"
        and seal_hit.get("locator") == "行 1"
        and seal_hit.get("original_filename") == "密封圈记录.txt"
        and "试验密封圈渗油" in seal_hit.get("snippet", "")
        and seal_hit.get("in_graph") is False
        and seal_hit.get("graph_relation_established") is False
        and ":\\" not in json.dumps(seal_hit, ensure_ascii=False),
        json.dumps(seal_hit, ensure_ascii=False),
    )

    same = upload(client, "同段观察.txt", "齿轮裂纹、轴承磨损同时出现，现场未说明先后。".encode())
    same_body = ask(client, "齿轮裂纹", "轴承磨损").json()
    cross = next((item for item in same_body["cross_document_evidence"] if item["retrieval_source"] == "cleaned_json"), {})
    check(
        "同段共现不生成因果链",
        same.status_code == 200
        and same_body["connection_paths"] == []
        and same_body["causal_conclusion"] is False
        and cross.get("file_id") == same.json()["file_id"]
        and "齿轮裂纹" in cross.get("snippet", "")
        and "轴承磨损" in cross.get("snippet", "")
        and cross.get("causal_conclusion") is False
        and cross.get("graph_relation_established") is False
        and "同时出现不代表因果关系" in cross.get("note", "")
        and same_body["unverified_candidates"] == []
        and "CAUSES" not in json.dumps(same_body["connection_paths"], ensure_ascii=False),
        cross.get("snippet", ""),
    )

    north = upload(client, "北区记录.txt", "北区巡检记录试验齿轮裂纹甲。".encode())
    south = upload(client, "南区记录.txt", "南区巡检记录试验轴承磨损乙。".encode())
    split = ask(client, "试验齿轮裂纹甲", "试验轴承磨损乙").json()
    north_hit = next((item for item in split["document_evidence"]["entity_a"] if item["file_id"] == north.json()["file_id"]), {})
    south_hit = next((item for item in split["document_evidence"]["entity_b"] if item["file_id"] == south.json()["file_id"]), {})
    check(
        "两个实体分别来自不同文件时保留各自来源",
        north_hit.get("original_filename") == "北区记录.txt"
        and south_hit.get("original_filename") == "南区记录.txt"
        and north_hit.get("file_id") != south_hit.get("file_id")
        and north_hit.get("direct_mention") is True
        and south_hit.get("direct_mention") is True
        and split["cross_document_evidence"] == []
        and split["connection_paths"] == [],
        json.dumps({"north": north_hit.get("file_id"), "south": south_hit.get("file_id")}, ensure_ascii=False),
    )

    ambiguous = ask(client, "外圈", "定子").json()
    scopes = {item["scope"] for item in ambiguous["entity_a_matches"]}
    check(
        "文档命中没有合并同名外圈",
        len(ambiguous["entity_a_matches"]) == 2
        and scopes == {"驱动端轴承", "非驱动端轴承"}
        and ambiguous["connection_paths"] == []
        and "没有擅自合并或选择" in "\n".join(ambiguous["evidence_gaps"]),
        str(scopes),
    )

    citation = ask(client, "轴承磨损", "KB-02_故障模式诊断知识库.docx").json()
    relations = {step["relation"] for path in citation["connection_paths"] for step in path["steps"]}
    check(
        "来源引用和界面导航没有变成工业路径",
        "SUPPORTED_BY" not in relations
        and "UI_GROUP" not in relations
        and citation["connection_paths"] == []
        and "SUPPORTED_BY" in "\n".join(citation["evidence_gaps"]),
    )

    candidate = {
        "schema_version": "1.0",
        "file_id": "a" * 32,
        "extraction_status": "extracted",
        "source": {"original_filename": "候选因果.txt", "source_type": "user_upload"},
        "entities": [],
        "relations": [
            {
                "source_label": "齿轮裂纹",
                "target_label": "轴承磨损",
                "relation_type": "CAUSE_CANDIDATE",
                "direction": "cause_to_effect",
                "certainty": "uncertain",
                "causal_status": "unverified",
                "in_graph": False,
                "evidence": [
                    {
                        "file_id": "a" * 32,
                        "original_filename": "候选因果.txt",
                        "section_id": "section-001",
                        "locator": {"type": "line", "index": 3},
                        "quote": "齿轮裂纹可能引起轴承磨损",
                        "method": "rule:possible_cause",
                    }
                ],
            }
        ],
    }
    extracted = TEMP / "extracted"
    extracted.mkdir(parents=True, exist_ok=True)
    (extracted / f"{'a' * 32}.json").write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")
    hypothesis = ask(client, "齿轮裂纹", "轴承磨损").json()
    row = hypothesis["unverified_candidates"][0] if hypothesis["unverified_candidates"] else {}
    candidate_docs = [item for item in hypothesis["document_evidence"]["entity_a"] if item["retrieval_source"] == "candidate_evidence"]
    check(
        "未验证候选仍单独返回，文档引用不升格为图谱事实",
        hypothesis["connection_paths"] == []
        and row.get("fact_class") == "unverified_candidate"
        and row.get("causal_status") == "unverified"
        and row.get("in_graph") is False
        and candidate_docs
        and candidate_docs[0]["fact_class"] == "document_evidence"
        and candidate_docs[0]["graph_relation_established"] is False
        and candidate_docs[0]["retrieval_method"] == "rule:possible_cause"
        and candidate_docs[0]["locator"] == "行 3"
        and "未验证候选" in hypothesis["summary"]["text"],
        hypothesis["summary"]["text"],
    )

    lines = [("长度限制试验词" + ("测" * 500)) for _ in range(8)]
    limited = upload(client, "长度限制.txt", "\n".join(lines).encode())
    limited_body = ask(client, "长度限制试验词", "QY92ZZ-not-anywhere").json()
    limited_items = limited_body["document_evidence"]["entity_a"]
    check(
        "每个对象最多5条且片段不超过400字",
        limited.status_code == 200
        and len(limited_items) == 5
        and limited_body["document_evidence"]["truncated"]["entity_a"] is True
        and limited_body["document_evidence"]["limit_per_entity"] == 5
        and all(len(item["snippet"]) <= 400 and item["snippet_truncated"] is True for item in limited_items)
        and all(item["file_id"] == limited.json()["file_id"] for item in limited_items),
        str(len(limited_items)),
    )

    preserved = ask(client, "棒线主轧机电机", "定子").json()
    preserved_step = preserved["connection_paths"][0]["steps"][0]
    check(
        "增加文档字段后原有研究字段仍在",
        REQUIRED_KEYS <= set(preserved)
        and preserved_step["relation"] == "HAS_COMPONENT"
        and preserved["entity_a_matches"][0]["node_id"] == "device-f803cb74ec718d83"
        and preserved["causal_conclusion"] is False,
    )
    response_text = json.dumps(gear, ensure_ascii=False) + json.dumps(preserved, ensure_ascii=False)
    check("结果没有答案标签，也没有服务器绝对路径", all(label not in response_text for label in ANSWER_LABELS) and str(TEMP) not in response_text and "stored_name" not in response_text)
    check(
        "正式图谱、入图库、Excel 和监测库没有被改写",
        file_hash(GRAPH) == before_graph
        and file_hash(EXCEL) == before_excel
        and (file_hash(SQLITE) if SQLITE.is_file() else None) == before_sqlite
        and DEFAULT_IMPORTS.exists() == before_default
        and len(json.loads(GRAPH.read_text(encoding="utf-8"))["nodes"]) == 252
        and len(json.loads(GRAPH.read_text(encoding="utf-8"))["edges"]) == 464,
    )
    print("GEAR_DOCS")
    print(
        json.dumps(
            {
                "paths": len(gear["connection_paths"]),
                "cross": len(gear["cross_document_evidence"]),
                "summary": gear["summary"]["text"],
                "gear_hits": [
                    {"kind": item["match_kind"], "document": item["indexed_document"], "section": item["section"], "locator": item["locator"]}
                    for item in gear["document_evidence"]["entity_a"]
                ],
                "bearing_direct": {"document": bearing.get("original_filename"), "section": bearing.get("section"), "locator": bearing.get("locator")},
            },
            ensure_ascii=False,
        )
    )
    print(f"{sum(CHECKS)}/{len(CHECKS)} passed")
    if not all(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
