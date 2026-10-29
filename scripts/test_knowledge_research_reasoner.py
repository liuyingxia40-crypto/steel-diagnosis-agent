"""验收交叉研究推理。模型调用使用模拟响应，写入只发生在临时目录。"""

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

TEMP = Path(tempfile.mkdtemp(prefix="knowledge-reasoner-"))
os.environ["KNOWLEDGE_FILES_DB"] = str(TEMP / "files.sqlite3")
os.environ["KNOWLEDGE_UPLOAD_DIR"] = str(TEMP / "uploads")
os.environ["KNOWLEDGE_CLEANED_DIR"] = str(TEMP / "cleaned")
os.environ["KNOWLEDGE_EXTRACTED_DIR"] = str(TEMP / "extracted")
os.environ["KNOWLEDGE_IMPORT_PREVIEW_DIR"] = str(TEMP / "previews")
os.environ["KNOWLEDGE_GRAPH_IMPORTS_DB"] = str(TEMP / "imports.sqlite3")
os.environ["RESEARCH_RELEASE_GRANTS_PATH"] = str(TEMP / "grants.json")
os.environ["RESEARCH_MODEL_AUDIT_PATH"] = str(TEMP / "audit.jsonl")
os.environ["STARTUP_TOOL_SELFTEST"] = "0"
os.environ["DEEPSEEK_API_KEY"] = "sk-test-should-not-leak"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.deepseek import DeepSeekError  # noqa: E402
from app.services.knowledge_research_reasoner import (  # noqa: E402
    SERVER_AUTHORIZATION_BASIS,
    SERVER_AUTHORIZATION_SCOPE,
    research_knowledge_relationship,
)
from app.services import knowledge_research_reasoner as reasoner  # noqa: E402

CHECKS: list[bool] = []
GRAPH = ROOT / "data" / "processed" / "graph" / "graph.json"
EXCEL = ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"
SQLITE = ROOT / "data" / "runtime" / "monitoring_events.sqlite3"
DEFAULT_IMPORTS = ROOT / "data" / "runtime" / "knowledge_graph_imports.sqlite3"
ANSWER_LABELS = ("设备状态", "故障类型", "故障严重度", "故障位置", "诊断置信度", "频谱描述", "根因分析", "建议措施", "优先级", "影响程度")
SECRET = "sk-test-should-not-leak"
UPLOAD_MARKER = "上传正文不可外发标记"


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append(bool(ok))
    print(("PASS" if ok else "FAIL"), name, detail)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def authorized_bundle() -> dict:
    allowed = {
        "source_type": "server_simulation",
        "original_filename": "模拟授权资料.md",
        "section": "模拟章节",
        "locator": "第1行",
        "snippet": "模拟资料只说明齿轮裂纹与轴承磨损需要继续核对。",
        "direct_mention": True,
        "match_kind": "direct_mention",
        "fact_class": "document_evidence",
        "external_model_allowed": True,
        "release_scope": SERVER_AUTHORIZATION_SCOPE,
        "authorization_basis": SERVER_AUTHORIZATION_BASIS,
        "matched_query": "齿轮裂纹",
    }
    blocked = {
        "source_type": "user_upload",
        "original_filename": "内部文件.txt",
        "section": "内部章节",
        "locator": "行 9",
        "snippet": UPLOAD_MARKER,
        "direct_mention": False,
        "match_kind": "keyword_related",
        "fact_class": "document_evidence",
        "external_model_allowed": True,
        "release_scope": SERVER_AUTHORIZATION_SCOPE,
        "authorization_basis": SERVER_AUTHORIZATION_BASIS,
        "matched_query": "轴承磨损",
    }
    return {
        "entity_a_matches": [],
        "entity_b_matches": [],
        "shared_neighbors": [],
        "connection_paths": [],
        "supporting_evidence": [],
        "unverified_candidates": [],
        "document_evidence": {"entity_a": [allowed], "entity_b": [blocked]},
        "cross_document_evidence": [],
        "evidence_gaps": [],
    }


def main() -> None:
    before_graph = file_hash(GRAPH)
    before_excel = file_hash(EXCEL)
    before_sqlite = file_hash(SQLITE) if SQLITE.is_file() else None
    before_default = DEFAULT_IMPORTS.exists()
    source = (BACKEND / "app" / "services" / "knowledge_research_reasoner.py").read_text(encoding="utf-8")
    modules = set()
    for item in ast.walk(ast.parse(source)):
        if isinstance(item, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in item.names)
        elif isinstance(item, ast.ImportFrom) and item.module:
            modules.update(item.module.split("."))
    banned = modules & {"agent_tools", "monitoring_events", "diagnostic_bridge"}
    check("研究模块没有改接五个工具或监测诊断", not banned and "openai" not in modules, str(sorted(banned)))

    calls: list[str] = []
    original = reasoner.complete_research_chat

    def spy(system: str, user: str) -> str:
        calls.append(user)
        return "{}"

    reasoner.complete_research_chat = spy
    client = TestClient(app)
    try:
        linked = client.post("/api/knowledge/research/reason", json={"entity_a": "棒线主轧机电机", "entity_b": "定子", "max_hops": 3}).json()
        relation_facts = [item for item in linked["known_facts"] if item.get("relation") == "HAS_COMPONENT"]
        check(
            "已有图谱关联返回真实已知事实",
            relation_facts
            and relation_facts[0]["causal_conclusion"] is False
            and "棒线主轧机电机" in relation_facts[0]["statement"]
            and "定子" in relation_facts[0]["statement"]
            and linked["graph_connections"]
            and linked["graph_connections"][0]["steps"][0]["source_id"] == "device-f803cb74ec718d83"
            and linked["graph_connections"][0]["steps"][0]["target_id"] == "component-9e4adc132ac1a86b"
            and linked["written_to_graph"] is False
            and linked["model_usage"]["call_count"] == 0,
            relation_facts[0]["statement"] if relation_facts else "",
        )

        pair = client.post("/api/knowledge/research/reason", json={"entity_a": "定子", "entity_b": "转子"}).json()
        check(
            "共同邻居不会生成确定因果链",
            bool(pair["graph_connections"])
            and pair["research_hypotheses"] == []
            and all(item["causal_conclusion"] is False for item in pair["graph_connections"])
            and "不能据此认定因果关系" in "\n".join(pair["evidence_gaps"])
            and "棒线主轧机电机" in json.dumps(pair["graph_connections"], ensure_ascii=False),
            "\n".join(pair["evidence_gaps"]),
        )

        gear = client.post(
            "/api/knowledge/research/reason",
            json={"entity_a": "齿轮裂纹", "entity_b": "轴承磨损", "max_hops": 3, "allow_external": True, "evidence": {"snippet": UPLOAD_MARKER}},
        ).json()
        gear_text = json.dumps(gear, ensure_ascii=False)
        check(
            "齿轮裂纹与轴承磨损没有已知路径，齿根裂纹也不能冒充直接命中",
            gear["graph_connections"] == []
            and gear["research_status"] == "local_only"
            and gear["model_usage"]["model_status"] == "blocked_by_source_policy"
            and gear["model_usage"]["call_count"] == 0
            and gear["model_usage"]["http_allow_external_honored"] is False
            and "没有找到现有知识支持的关联路径" in "\n".join(gear["evidence_gaps"])
            and "齿根裂纹" in "\n".join(gear["evidence_gaps"])
            and "不能互相代替" in "\n".join(gear["evidence_gaps"])
            and not any(item.get("kind") == "direct_mention" and item.get("matched_query") == "齿轮裂纹" for item in gear["known_facts"])
            and all(item.get("validation_status") == "unverified" for item in gear["research_hypotheses"])
            and gear["causal_conclusion"] is False
            and gear["l2_physical_validation"] == "not_performed"
            and UPLOAD_MARKER not in gear_text,
            gear["research_status"] + " " + gear["model_usage"]["reason"],
        )
        check("local_only 证据没有触发 DeepSeek", calls == [] and gear["model_usage"]["sent_evidence_ids"] == [])

        evidence_api = client.post("/api/knowledge/research/evidence", json={"entity_a": "棒线主轧机电机", "entity_b": "定子"}).json()
        check(
            "原有证据接口仍然返回真实路径",
            evidence_api["connection_paths"][0]["steps"][0]["relation"] == "HAS_COMPONENT"
            and evidence_api["causal_conclusion"] is False
            and "document_evidence" in evidence_api,
        )
    finally:
        reasoner.complete_research_chat = original

    candidate = {
        "schema_version": "1.0",
        "file_id": "b" * 32,
        "extraction_status": "extracted",
        "source": {"original_filename": "候选因果.txt", "source_type": "user_upload"},
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
                        "file_id": "b" * 32,
                        "original_filename": "候选因果.txt",
                        "source_type": "user_upload",
                        "section_id": "section-001",
                        "locator": {"type": "line", "index": 2},
                        "quote": "齿轮裂纹可能引起轴承磨损",
                        "method": "rule:possible_cause",
                        "release_scope": "local_only",
                        "external_model_allowed": False,
                    }
                ],
            }
        ],
    }
    extracted = TEMP / "extracted"
    extracted.mkdir(parents=True, exist_ok=True)
    (extracted / f"{'b' * 32}.json").write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")
    def refuse_model(system: str, user: str) -> str:
        raise AssertionError("local_only 证据不应调用模型")

    reasoner.complete_research_chat = refuse_model
    try:
        with_candidate = client.post("/api/knowledge/research/reason", json={"entity_a": "齿轮裂纹", "entity_b": "轴承磨损"}).json()
    finally:
        reasoner.complete_research_chat = original
    candidate_rows = [item for item in with_candidate["research_hypotheses"] if item.get("origin") == "unverified_candidate"]
    check(
        "未验证候选只进入研究假设",
        with_candidate["graph_connections"] == []
        and candidate_rows
        and candidate_rows[0]["validation_status"] == "unverified"
        and candidate_rows[0]["cause_label"] == "齿轮裂纹"
        and candidate_rows[0]["effect_label"] == "轴承磨损"
        and candidate_rows[0]["in_graph"] is False
        and candidate_rows[0]["causal_conclusion"] is False
        and "齿轮裂纹可能引起轴承磨损" in candidate_rows[0]["basis"]
        and not any(item.get("kind") == "graph_relation" and item.get("relation") not in {"HAS_COMPONENT", "HAS_FAULT", "HAS_FEATURE", "HAS_PARAMETER", "HAS_ACTION", "CONNECTS_TO", "OCCURS_ON"} for item in with_candidate["known_facts"])
        and with_candidate["model_usage"]["call_count"] == 0
        and with_candidate["written_to_graph"] is False,
        candidate_rows[0]["basis"] if candidate_rows else "",
    )

    prompts: list[str] = []

    def mock_model(system: str, user: str) -> str:
        prompts.append(user)
        return json.dumps(
            {
                "research_status": "unverified_hypotheses",
                "hypotheses": [
                    {
                        "cause_label": "齿轮裂纹",
                        "effect_label": "轴承磨损",
                        "direction": "cause_to_effect",
                        "basis": "模拟授权资料只要求继续核对，不能当成已经成立的关系。",
                        "uncertainty": "这只是待验证假设。",
                        "evidence_ids": ["E1", "E99"],
                    }
                ],
                "contradictory_evidence_ids": ["E404"],
                "evidence_gaps": ["缺少同一设备的测量记录。"],
                "verification_needed": ["需要检修记录核对时间顺序。"],
            },
            ensure_ascii=False,
        )

    reasoner.complete_research_chat = mock_model
    try:
        generated = research_knowledge_relationship("齿轮裂纹", "轴承磨损", evidence=authorized_bundle())
    finally:
        reasoner.complete_research_chat = original
    generated_hypothesis = generated["research_hypotheses"][0] if generated["research_hypotheses"] else {}
    check(
        "服务器端授权的模拟证据才会调用模型，伪造证据编号会被去掉",
        len(prompts) == 1
        and "模拟资料只说明齿轮裂纹与轴承磨损需要继续核对。" in prompts[0]
        and UPLOAD_MARKER not in prompts[0]
        and SECRET not in prompts[0]
        and generated["model_usage"]["called"] is True
        and generated["model_usage"]["call_count"] == 1
        and generated["model_usage"]["sent_evidence_ids"] == ["E1"]
        and generated_hypothesis.get("validation_status") == "unverified"
        and generated_hypothesis.get("evidence_ids") == ["E1"]
        and generated_hypothesis.get("causal_conclusion") is False
        and any(item.get("evidence_id") == "E99" for item in generated["rejected_citations"])
        and any(item.get("evidence_id") == "E404" for item in generated["rejected_citations"])
        and generated["written_to_graph"] is False,
        json.dumps(generated_hypothesis, ensure_ascii=False),
    )

    def fake_source(system: str, user: str) -> str:
        return json.dumps(
            {
                "research_status": "unverified_hypotheses",
                "hypotheses": [
                    {
                        "cause_label": "齿根裂纹",
                        "effect_label": "轴承磨损",
                        "direction": "cause_to_effect",
                        "basis": "见虚构文档.docx",
                        "uncertainty": "待核对",
                        "evidence_ids": ["E999"],
                    }
                ],
            },
            ensure_ascii=False,
        )

    reasoner.complete_research_chat = fake_source
    try:
        blocked_model = research_knowledge_relationship("齿轮裂纹", "轴承磨损", evidence=authorized_bundle())
    finally:
        reasoner.complete_research_chat = original
    blocked_text = json.dumps(blocked_model, ensure_ascii=False)
    check(
        "不存在的节点、文档和证据编号不能留在假设中",
        blocked_model["research_status"] == "insufficient_evidence"
        and blocked_model["research_hypotheses"] == []
        and any(item.get("evidence_id") == "E999" for item in blocked_model["rejected_citations"])
        and "虚构文档.docx" not in blocked_text
        and "齿根裂纹" not in blocked_text,
    )

    def raise_timeout(system: str, user: str) -> str:
        raise DeepSeekError("连接 DeepSeek 超时，请稍后重试。", status_code=504)

    failures = []
    for responder in (raise_timeout, lambda _system, _user: "这不是json", lambda _system, _user: "{"):
        reasoner.complete_research_chat = responder
        try:
            failures.append(research_knowledge_relationship("齿轮裂纹", "轴承磨损", evidence=authorized_bundle()))
        finally:
            reasoner.complete_research_chat = original
    check(
        "模型错误、超时或无效 JSON 不会被写成研究成功",
        len(failures) == 3
        and all(item["research_status"] == "model_unavailable" for item in failures)
        and all(item["model_usage"]["model_status"] == "failed" and item["model_usage"]["call_count"] == 1 for item in failures)
        and all(item["research_hypotheses"] == [] for item in failures)
        and all(item["research_status"] != "unverified_hypotheses" for item in failures),
        " | ".join(item["model_usage"]["reason"] for item in failures),
    )

    report_text = json.dumps(gear, ensure_ascii=False) + json.dumps(with_candidate, ensure_ascii=False) + json.dumps(generated, ensure_ascii=False)
    check(
        "结果没有密钥、绝对路径、答案标签和未外发正文",
        SECRET not in report_text
        and UPLOAD_MARKER not in report_text
        and str(TEMP) not in report_text
        and ":\\" not in report_text
        and all(label not in report_text for label in ANSWER_LABELS),
    )
    check(
        "正式图谱、入图库、Excel 和监测库没有被改写",
        file_hash(GRAPH) == before_graph
        and file_hash(EXCEL) == before_excel
        and (file_hash(SQLITE) if SQLITE.is_file() else None) == before_sqlite
        and DEFAULT_IMPORTS.exists() == before_default
        and len(json.loads(GRAPH.read_text(encoding="utf-8"))["nodes"]) == 252
        and len(json.loads(GRAPH.read_text(encoding="utf-8"))["edges"]) == 464,
    )
    print("GEAR_RESEARCH")
    print(
        json.dumps(
            {
                "status": gear["research_status"],
                "model_status": gear["model_usage"]["model_status"],
                "paths": len(gear["graph_connections"]),
                "hypotheses": len(gear["research_hypotheses"]),
                "facts": [item.get("kind") for item in gear["known_facts"]],
                "gaps": gear["evidence_gaps"],
            },
            ensure_ascii=False,
        )
    )
    print(f"{sum(CHECKS)}/{len(CHECKS)} passed")
    if not all(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
