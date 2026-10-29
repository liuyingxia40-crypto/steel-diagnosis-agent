"""管理员在本机批准单条研究资料外发。

没有整库开关。用户上传正文不能授权。授权只保存来源编号和正文 SHA-256，
原文一变就对不上。普通 HTTP 和页面按钮不能调用这里的写入函数。

管理员在 backend 目录执行：

    python -m app.services.knowledge_release_authorization add --source-id 来源编号 --content-file 已审核片段.txt --filename 可外发文件名.txt --labels 研究对象A,研究对象B --source-type server_simulation

确认该片段可以外发后再执行。本模块不会自动写入任何已生效授权。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SERVER_AUTHORIZATION_SCOPE = "server_authorized"
SERVER_AUTHORIZATION_BASIS = "server_policy"


def grants_path() -> Path:
    override = os.environ.get("RESEARCH_RELEASE_GRANTS_PATH", "").strip()
    if override:
        return Path(override)
    return ROOT / "data" / "runtime" / "research_release_grants.json"


def audit_path() -> Path:
    override = os.environ.get("RESEARCH_MODEL_AUDIT_PATH", "").strip()
    if override:
        return Path(override)
    return ROOT / "data" / "runtime" / "research_model_audit.jsonl"


def content_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def create_grant(
    *,
    source_id: str,
    content: str,
    released_filename: str,
    released_labels: list[str],
    source_type: str = "server_simulation",
    expires_at: str | None = None,
) -> dict:
    """把一条已审核片段写入本机授权文件。不保存正文。"""
    if source_type == "user_upload":
        raise ValueError("用户上传正文不能授权外发。")
    identity = source_id.strip()
    filename = released_filename.strip()
    labels = [str(label).strip() for label in released_labels if str(label).strip()]
    if not identity or not content or not filename or not labels:
        raise ValueError("授权必须同时绑定来源、正文、文件名和研究对象名称。")
    if expires_at and not _parsed_expiry(expires_at):
        raise ValueError("到期时间不是有效时间。")
    grant = {
        "grant_id": f"grant-{uuid.uuid4().hex[:16]}",
        "source_id": identity,
        "content_sha256": content_sha256(content),
        "released_filename": filename,
        "released_labels": labels,
        "source_type": source_type,
        "enabled": True,
        "expires_at": expires_at,
    }
    grants = load_grants()
    grants.append(grant)
    _write_grants(grants)
    return grant


def load_grants() -> list[dict]:
    path = grants_path()
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("grants") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    return [item for item in rows if isinstance(item, dict)]


def attach_authorized_copies(bundle: dict) -> dict:
    """为对得上授权的证据追加副本。原始对象的 local_only 标记保持不变。"""
    copied = dict(bundle)
    copies: list[dict] = []
    for item in _evidence_items(bundle):
        if item.get("source_type") == "user_upload":
            continue
        grant = _matching_grant(item)
        if grant is None:
            continue
        clone = dict(item)
        clone["external_model_allowed"] = True
        clone["release_scope"] = SERVER_AUTHORIZATION_SCOPE
        clone["authorization_basis"] = SERVER_AUTHORIZATION_BASIS
        clone["grant_id"] = grant["grant_id"]
        clone["authorization_copy"] = True
        clone["original_filename"] = grant["released_filename"]
        copies.append(clone)
    copied["authorized_evidence_copies"] = copies
    return copied


def release_for_prompt(left: str, right: str, items: list[dict]) -> tuple[list[dict], str]:
    """名称、文件名和正文都要落在同一条服务器授权里，才能进入模型提示词。"""
    accepted: list[dict] = []
    saw_grant = False
    blocked_names = False
    for item in items:
        grant_id = str(item.get("grant_id") or "")
        if not grant_id:
            accepted.append(item)
            continue
        saw_grant = True
        grant = _grant_by_id(grant_id)
        if grant is None or not _grant_matches(grant, item):
            continue
        labels = grant.get("released_labels") if isinstance(grant.get("released_labels"), list) else []
        if left not in labels or right not in labels:
            blocked_names = True
            continue
        filename = str(item.get("original_filename") or item.get("document") or "")
        if filename != str(grant.get("released_filename") or ""):
            continue
        accepted.append(item)
    if accepted:
        return accepted, ""
    if blocked_names:
        return [], "研究对象名称或文件名没有包含在服务器已批准的外发范围中，没有调用模型。"
    if saw_grant:
        return [], "授权与当前证据的来源或内容摘要不一致，没有调用模型。"
    return [], ""


def record_model_audit(
    *,
    research_id: str,
    called: bool,
    call_count: int,
    model_status: str,
    reason: str,
    sent_evidence_ids: list[str],
    grant_ids: list[str],
) -> None:
    """只记录调用结果和授权编号。不记录正文、未授权名称或密钥。"""
    safe_reason = "已省略不安全的记录。" if _sensitive(reason) else reason
    row = {
        "research_id": research_id,
        "called": called is True,
        "call_count": call_count,
        "model_status": model_status,
        "reason": safe_reason,
        "sent_evidence_ids": [str(item) for item in sent_evidence_ids],
        "grant_ids": [str(item) for item in grant_ids if item],
    }
    text = json.dumps(row, ensure_ascii=False)
    if _sensitive(text):
        return
    path = audit_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    except OSError:
        return


def _matching_grant(item: dict) -> dict | None:
    for grant in load_grants():
        if _grant_matches(grant, item):
            filename = str(item.get("original_filename") or item.get("document") or "")
            if filename != str(grant.get("released_filename") or ""):
                continue
            return grant
    return None


def _grant_by_id(grant_id: str) -> dict | None:
    for grant in load_grants():
        if str(grant.get("grant_id") or "") == grant_id:
            return grant
    return None


def _grant_matches(grant: dict, item: dict) -> bool:
    if item.get("source_type") == "user_upload" or grant.get("source_type") == "user_upload":
        return False
    if grant.get("enabled") is not True or not _unexpired(grant.get("expires_at")):
        return False
    source_id = str(item.get("source_id") or item.get("file_id") or "")
    if not source_id or source_id != str(grant.get("source_id") or ""):
        return False
    expected = str(grant.get("content_sha256") or "")
    text = str(item.get("snippet") or item.get("quote") or "")
    if len(expected) != 64 or content_sha256(text) != expected:
        return False
    return True


def _unexpired(value: object) -> bool:
    if value is None or value == "":
        return True
    parsed = _parsed_expiry(value)
    if parsed is None:
        return False
    return parsed > datetime.now(timezone.utc)


def _parsed_expiry(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _evidence_items(bundle: dict) -> list[dict]:
    rows: list[dict] = []
    documents = bundle.get("document_evidence") if isinstance(bundle.get("document_evidence"), dict) else {}
    for key in ("entity_a", "entity_b"):
        rows.extend(item for item in documents.get(key) or [] if isinstance(item, dict))
    rows.extend(item for item in bundle.get("cross_document_evidence") or [] if isinstance(item, dict))
    rows.extend(item for item in bundle.get("supporting_evidence") or [] if isinstance(item, dict))
    for candidate in bundle.get("unverified_candidates") or []:
        if isinstance(candidate, dict):
            rows.extend(item for item in candidate.get("evidence") or [] if isinstance(item, dict))
    return rows


def _write_grants(grants: list[dict]) -> None:
    path = grants_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": "1.0", "grants": grants}, ensure_ascii=False, indent=2), encoding="utf-8")


def _sensitive(text: str) -> bool:
    lowered = text.lower()
    return "sk-" in lowered or "api_key" in lowered or "deepseek_api_key" in lowered


def main() -> int:
    parser = argparse.ArgumentParser(description="管理员本地批准单条研究资料外发。不会授权整库或用户上传正文。")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add")
    add.add_argument("--source-id", required=True)
    add.add_argument("--content-file", required=True)
    add.add_argument("--filename", required=True)
    add.add_argument("--labels", required=True, help="允许外发的研究对象名称，用英文逗号分隔")
    add.add_argument("--source-type", default="server_simulation")
    add.add_argument("--expires-at", default="")
    args = parser.parse_args()
    if args.command != "add":
        return 2
    content = Path(args.content_file).read_text(encoding="utf-8")
    grant = create_grant(
        source_id=args.source_id,
        content=content,
        released_filename=args.filename,
        released_labels=[item for item in args.labels.split(",")],
        source_type=args.source_type,
        expires_at=args.expires_at or None,
    )
    print(f"已写入授权 {grant['grant_id']}，内容摘要 {grant['content_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
