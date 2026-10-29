"""验收研究外发授权。真实资料保持本地，模型调用使用模拟响应。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

TEMP = Path(tempfile.mkdtemp(prefix="knowledge-release-"))
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
from app.services import knowledge_research_reasoner as reasoner  # noqa: E402
from app.services.knowledge_release_authorization import content_sha256, create_grant, load_grants  # noqa: E402
from app.services.knowledge_research_reasoner import research_knowledge_relationship  # noqa: E402

CHECKS: list[bool] = []
GRAPH = ROOT / "data" / "processed" / "graph" / "graph.json"
EXCEL = ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"
SQLITE = ROOT / "data" / "runtime" / "monitoring_events.sqlite3"
DEFAULT_IMPORTS = ROOT / "data" / "runtime" / "knowledge_graph_imports.sqlite3"
DEFAULT_GRANTS = ROOT / "data" / "runtime" / "research_release_grants.json"
SECRET = "sk-test-should-not-leak"
SNIPPET = "模拟授权片段只说明需要继续核对。"
BLOCKED = "未授权段落不可外发"
PYTHON = BACKEND / ".venv" / "Scripts" / "python.exe"


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append(bool(ok))
    print(("PASS" if ok else "FAIL"), name, detail)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bundle(snippet: str = SNIPPET, source_type: str = "server_simulation") -> dict:
    allowed = {
        "source_id": "sim-source-001",
        "source_type": source_type,
        "original_filename": "模拟授权说明.txt",
        "section": "模拟章节",
        "locator": "第1行",
        "snippet": snippet,
        "direct_mention": True,
        "match_kind": "direct_mention",
        "fact_class": "document_evidence",
        "external_model_allowed": False,
        "release_scope": "local_only",
        "authorization_basis": "",
        "matched_query": "模拟原因",
    }
    blocked = {
        "source_id": "sim-source-002",
        "source_type": "server_simulation",
        "original_filename": "未批准.txt",
        "section": "未批准章节",
        "locator": "第2行",
        "snippet": BLOCKED,
        "direct_mention": True,
        "match_kind": "direct_mention",
        "fact_class": "document_evidence",
        "external_model_allowed": False,
        "release_scope": "local_only",
        "matched_query": "模拟结果",
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


def model_json() -> str:
    return json.dumps(
        {
            "research_status": "unverified_hypotheses",
            "hypotheses": [
                {
                    "cause_label": "模拟原因",
                    "effect_label": "模拟结果",
                    "direction": "cause_to_effect",
                    "basis": "模拟授权片段只要求继续核对。",
                    "uncertainty": "还需要核对。",
                    "evidence_ids": ["E1", "E-NO-SUCH"],
                }
            ],
            "contradictory_evidence_ids": [],
            "evidence_gaps": ["缺少时间顺序记录。"],
            "verification_needed": ["需要同一设备的后续测量。"],
        },
        ensure_ascii=False,
    )


def audit_text() -> str:
    path = TEMP / "audit.jsonl"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def main() -> None:
    before_graph = file_hash(GRAPH)
    before_excel = file_hash(EXCEL)
    before_sqlite = file_hash(SQLITE) if SQLITE.is_file() else None
    before_default = DEFAULT_IMPORTS.exists()
    before_grants = DEFAULT_GRANTS.exists()
    page = (ROOT / "frontend" / "src" / "components" / "CrossResearchPanel.tsx").read_text(encoding="utf-8")
    graph_page = (ROOT / "frontend" / "src" / "pages" / "KnowledgeGraphPage.tsx").read_text(encoding="utf-8")
    check(
        "研究入口接在原图谱页，请求里没有外发开关",
        "交叉研究" in graph_page
        and "GraphCanvas" in graph_page
        and "KnowledgeFileDirectory" in graph_page
        and "onDeeper" in graph_page
        and "allow_external=" not in page
        and '"allow_external"' not in page
        and "/api/knowledge/research/reason" in page
        and "/api/knowledge/research/validate" in page,
    )
    try:
        create_grant(source_id="upload-1", content="上传正文", released_filename="a.txt", released_labels=["甲"], source_type="user_upload")
        rejected_upload = False
    except ValueError:
        rejected_upload = True
    check("用户上传正文不能创建外发授权", rejected_upload)

    calls: list[str] = []

    def mock_model(system: str, user: str) -> str:
        calls.append(user)
        return model_json()

    reasoner.complete_research_chat = mock_model
    client = TestClient(app)
    local = client.post(
        "/api/knowledge/research/reason",
        json={"entity_a": "棒线主轧机电机", "entity_b": "定子", "allow_external": True},
    ).json()
    check(
        "真实资料在没有授权时不调用模型",
        calls == []
        and local["model_usage"]["call_count"] == 0
        and local["model_usage"]["called"] is False
        and local["model_usage"]["http_allow_external_honored"] is False
        and local["causal_conclusion"] is False
        and local["graph_connections"][0]["steps"][0]["relation"] == "HAS_COMPONENT",
        local["model_usage"]["reason"],
    )
    ambiguous = client.post("/api/knowledge/research/reason", json={"entity_a": "外圈", "entity_b": "定子"}).json()
    scopes = {item.get("scope") for item in ambiguous["entity_a_matches"]}
    check(
        "同名外圈返回各自归属且没有自动选成因果",
        len(ambiguous["entity_a_matches"]) >= 2
        and len(scopes) >= 2
        and ambiguous["graph_connections"] == []
        and ambiguous["causal_conclusion"] is False
        and "没有擅自合并或选择" in "\n".join(ambiguous["evidence_gaps"])
        and ambiguous["model_usage"]["call_count"] == 0,
        str(scopes),
    )

    grant = create_grant(
        source_id="sim-source-001",
        content=SNIPPET,
        released_filename="模拟授权说明.txt",
        released_labels=["模拟原因", "模拟结果"],
        source_type="server_simulation",
    )
    sample = bundle()
    original = sample["document_evidence"]["entity_a"][0]
    generated = research_knowledge_relationship("模拟原因", "模拟结果", evidence=sample)
    hypothesis = generated["research_hypotheses"][0] if generated["research_hypotheses"] else {}
    prompt = calls[-1] if calls else ""
    check(
        "已批准的模拟片段可以走模拟研究，伪造证据编号会被去掉",
        len(calls) == 1
        and SNIPPET in prompt
        and BLOCKED not in prompt
        and SECRET not in prompt
        and "模拟原因" in prompt
        and "模拟授权说明.txt" in prompt
        and "未批准.txt" not in prompt
        and generated["model_usage"]["called"] is True
        and generated["model_usage"]["sent_evidence_ids"] == ["E1"]
        and grant["grant_id"] in generated["model_usage"]["authorized_grant_ids"]
        and hypothesis.get("validation_status") == "unverified"
        and hypothesis.get("causal_conclusion") is False
        and hypothesis.get("evidence_ids") == ["E1"]
        and any(item.get("evidence_id") == "E-NO-SUCH" for item in generated["rejected_citations"])
        and original["release_scope"] == "local_only"
        and original["external_model_allowed"] is False
        and SNIPPET not in (TEMP / "grants.json").read_text(encoding="utf-8"),
        generated["research_status"],
    )

    calls.clear()
    hidden = research_knowledge_relationship("秘密对象甲", "秘密对象乙", evidence=bundle())
    hidden_audit = audit_text()
    check(
        "未列入授权范围的研究对象名称不会进入模型和日志",
        calls == []
        and hidden["model_usage"]["call_count"] == 0
        and "秘密对象甲" not in hidden_audit
        and SNIPPET not in hidden_audit
        and BLOCKED not in hidden_audit
        and SECRET not in hidden_audit,
        hidden["model_usage"]["reason"],
    )

    grants = load_grants()
    grants[0]["content_sha256"] = "0" * 64
    (TEMP / "grants.json").write_text(json.dumps({"grants": grants}, ensure_ascii=False), encoding="utf-8")
    tampered = research_knowledge_relationship("模拟原因", "模拟结果", evidence=bundle())
    grants[0]["content_sha256"] = content_sha256(SNIPPET)
    grants[0]["expires_at"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    (TEMP / "grants.json").write_text(json.dumps({"grants": grants}, ensure_ascii=False), encoding="utf-8")
    expired = research_knowledge_relationship("模拟原因", "模拟结果", evidence=bundle())
    grants[0]["expires_at"] = None
    grants[0]["enabled"] = False
    (TEMP / "grants.json").write_text(json.dumps({"grants": grants}, ensure_ascii=False), encoding="utf-8")
    disabled = research_knowledge_relationship("模拟原因", "模拟结果", evidence=bundle())
    check(
        "摘要不符、过期或停用的授权都不能调用模型",
        calls == []
        and tampered["model_usage"]["call_count"] == 0
        and expired["model_usage"]["call_count"] == 0
        and disabled["model_usage"]["call_count"] == 0,
        " | ".join(item["model_usage"]["reason"] for item in (tampered, expired, disabled)),
    )

    upload_bundle = bundle(source_type="user_upload")
    grants[0]["enabled"] = True
    (TEMP / "grants.json").write_text(json.dumps({"grants": grants}, ensure_ascii=False), encoding="utf-8")
    uploaded = research_knowledge_relationship("模拟原因", "模拟结果", evidence=upload_bundle)
    check("对得上摘要的用户上传正文仍然不能外发", calls == [] and uploaded["model_usage"]["call_count"] == 0, uploaded["model_usage"]["reason"])

    content = TEMP / "snippet.txt"
    content.write_text(SNIPPET, encoding="utf-8")
    cli_env = os.environ.copy()
    cli_env["RESEARCH_RELEASE_GRANTS_PATH"] = str(TEMP / "cli-grants.json")
    completed = subprocess.run(
        [str(PYTHON), "-m", "app.services.knowledge_release_authorization", "add", "--source-id", "sim-source-001", "--content-file", str(content), "--filename", "模拟授权说明.txt", "--labels", "模拟原因,模拟结果", "--source-type", "server_simulation"],
        cwd=BACKEND,
        env=cli_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    cli_grants = (TEMP / "cli-grants.json").read_text(encoding="utf-8") if (TEMP / "cli-grants.json").is_file() else ""
    check(
        "管理员命令只写入授权摘要",
        completed.returncode == 0 and "已写入授权" in completed.stdout and SNIPPET not in completed.stdout and SNIPPET not in cli_grants and SECRET not in completed.stdout,
        completed.stderr[-300:],
    )
    check(
        "正式图谱、Excel、入图库和监测库没有被改写",
        file_hash(GRAPH) == before_graph
        and file_hash(EXCEL) == before_excel
        and (file_hash(SQLITE) if SQLITE.is_file() else None) == before_sqlite
        and DEFAULT_IMPORTS.exists() == before_default
        and DEFAULT_GRANTS.exists() == before_grants
        and len(json.loads(GRAPH.read_text(encoding="utf-8"))["nodes"]) == 252
        and len(json.loads(GRAPH.read_text(encoding="utf-8"))["edges"]) == 464
        and SECRET not in audit_text(),
    )
    print(f"{sum(CHECKS)}/{len(CHECKS)} passed")
    if not all(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
