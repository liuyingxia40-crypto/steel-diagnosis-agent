"""验收同名节点按编号定位。写入只在临时目录。"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

TEMP = Path(tempfile.mkdtemp(prefix="knowledge-identity-"))
os.environ["KNOWLEDGE_FILES_DB"] = str(TEMP / "files.sqlite3")
os.environ["KNOWLEDGE_UPLOAD_DIR"] = str(TEMP / "uploads")
os.environ["KNOWLEDGE_CLEANED_DIR"] = str(TEMP / "cleaned")
os.environ["KNOWLEDGE_EXTRACTED_DIR"] = str(TEMP / "extracted")
os.environ["KNOWLEDGE_IMPORT_PREVIEW_DIR"] = str(TEMP / "previews")
os.environ["KNOWLEDGE_GRAPH_IMPORTS_DB"] = str(TEMP / "imports.sqlite3")
os.environ["RESEARCH_RELEASE_GRANTS_PATH"] = str(TEMP / "grants.json")
os.environ["RESEARCH_MODEL_AUDIT_PATH"] = str(TEMP / "audit.jsonl")
os.environ["STARTUP_TOOL_SELFTEST"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import knowledge_research_reasoner as reasoner  # noqa: E402

CHECKS: list[bool] = []
GRAPH = ROOT / "data" / "processed" / "graph" / "graph.json"
EXCEL = ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"
SQLITE = ROOT / "data" / "runtime" / "monitoring_events.sqlite3"
DEFAULT_IMPORTS = ROOT / "data" / "runtime" / "knowledge_graph_imports.sqlite3"
DEFAULT_GRANTS = ROOT / "data" / "runtime" / "research_release_grants.json"


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append(bool(ok))
    print(("PASS" if ok else "FAIL"), name, detail)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def step_ids(paths: list[dict]) -> set[str]:
    found = set()
    for path in paths:
        for step in path.get("steps") or []:
            for key in ("from_id", "to_id", "source_id", "target_id"):
                if step.get(key):
                    found.add(str(step[key]))
    return found


def main() -> None:
    before_graph = file_hash(GRAPH)
    before_excel = file_hash(EXCEL)
    before_sqlite = file_hash(SQLITE) if SQLITE.is_file() else None
    before_default = DEFAULT_IMPORTS.exists()
    before_grants = DEFAULT_GRANTS.exists()
    calls: list[str] = []

    def refuse(system: str, user: str) -> str:
        calls.append(user)
        raise AssertionError("同名消歧不应调用 DeepSeek")

    reasoner.complete_research_chat = refuse
    client = TestClient(app)
    named = client.post("/api/knowledge/research/evidence", json={"entity_a": "外圈", "entity_b": "定子"}).json()
    rings = {item["scope"]: item for item in named["entity_a_matches"]}
    check(
        "只给同名外圈时不自动选择",
        set(rings) == {"驱动端轴承", "非驱动端轴承"}
        and named["entity_a_identity"] is None
        and named["connection_paths"] == []
        and "没有擅自合并或选择" in "\n".join(named["evidence_gaps"]),
        str(set(rings)),
    )
    drive = rings["驱动端轴承"]
    free = rings["非驱动端轴承"]
    identities = []
    for item in (drive, free):
        payload = {
            "entity_a": "外圈",
            "entity_b": "定子",
            "entity_a_id": item["node_id"],
            "entity_a_scope": item["scope"],
        }
        evidence = client.post("/api/knowledge/research/evidence", json=payload).json()
        reason = client.post("/api/knowledge/research/reason", json={**payload, "allow_external": True}).json()
        validation = client.post("/api/knowledge/research/validate", json=payload).json()
        evidence_ids = step_ids(evidence["connection_paths"])
        reason_ids = step_ids(reason["graph_connections"])
        other = free["node_id"] if item is drive else drive["node_id"]
        identities.append(evidence["entity_a_identity"]["node_id"])
        check(
            f"{item['scope']}的外圈按编号定位",
            evidence["entity_a_identity"]["node_id"] == item["node_id"]
            and evidence["entity_a_identity"]["scope"] == item["scope"]
            and evidence["entity_a_identity"]["label"] == "外圈"
            and reason["entity_a_identity"]["node_id"] == item["node_id"]
            and validation["entity_a_identity"]["node_id"] == item["node_id"]
            and evidence["entity_b_identity"]["label"] == "定子"
            and {row["node_id"] for row in evidence["entity_a_other_matches"]} == {other}
            and other not in evidence_ids
            and other not in reason_ids
            and "没有合并" in "\n".join(evidence["evidence_gaps"])
            and reason["causal_conclusion"] is False
            and reason["model_usage"]["call_count"] == 0
            and validation["causal_conclusion"] is False,
            json.dumps({"id": item["node_id"], "paths": len(evidence["connection_paths"])}, ensure_ascii=False),
        )
    check("两个外圈没有被定位成同一个节点", len(set(identities)) == 2, str(identities))

    mismatch = client.post(
        "/api/knowledge/research/evidence",
        json={"entity_a": "定子", "entity_b": "外圈", "entity_a_id": drive["node_id"], "entity_a_scope": drive["scope"]},
    )
    wrong_scope = client.post(
        "/api/knowledge/research/reason",
        json={"entity_a": "外圈", "entity_b": "定子", "entity_a_id": drive["node_id"], "entity_a_scope": "非驱动端轴承"},
    )
    missing = client.post(
        "/api/knowledge/research/validate",
        json={"entity_a": "外圈", "entity_b": "定子", "entity_a_id": "component-missing-node"},
    )
    check(
        "名称、归属或编号不一致时拒绝定位",
        mismatch.status_code == 400
        and "不一致" in mismatch.json()["message"]
        and wrong_scope.status_code == 400
        and "不一致" in wrong_scope.json()["message"]
        and missing.status_code == 400
        and "没有找到节点" in missing.json()["message"],
        " | ".join(item.json()["message"] for item in (mismatch, wrong_scope, missing)),
    )

    linked = client.post("/api/knowledge/research/reason", json={"entity_a": "棒线主轧机电机", "entity_b": "定子"}).json()
    check(
        "原有名称查询仍返回真实包含关系且不外发",
        linked["graph_connections"][0]["steps"][0]["relation"] == "HAS_COMPONENT"
        and linked["entity_a_identity"]["node_id"] == "device-f803cb74ec718d83"
        and linked["model_usage"]["call_count"] == 0
        and calls == [],
        linked["entity_a_identity"]["node_id"],
    )
    check(
        "正式图谱、Excel、入图库和授权文件没有被改写",
        file_hash(GRAPH) == before_graph
        and file_hash(EXCEL) == before_excel
        and (file_hash(SQLITE) if SQLITE.is_file() else None) == before_sqlite
        and DEFAULT_IMPORTS.exists() == before_default
        and DEFAULT_GRANTS.exists() == before_grants
        and len(json.loads(GRAPH.read_text(encoding="utf-8"))["nodes"]) == 252
        and len(json.loads(GRAPH.read_text(encoding="utf-8"))["edges"]) == 464,
    )
    print(f"{sum(CHECKS)}/{len(CHECKS)} passed")
    if not all(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
