"""本地 Markdown 知识检索。只读本机索引，不访问网络。"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

import jieba

ROOT = Path(__file__).resolve().parents[3]
MARKDOWN_DIR = ROOT / "data" / "processed" / "markdown"
INDEX_PATH = ROOT / "data" / "processed" / "rag" / "index.json"

DOCUMENTS = [
    "KB-01_设备知识图谱.md",
    "KB-02_故障模式诊断知识库.md",
    "KB-03_操作规程与技术手册.md",
    "KB-04_专家经验与典型案例.md",
    "KB-05_设备关联关系.md",
    "KB-06_RAG检索知识库.md",
]

STOPWORDS = {
    "的",
    "了",
    "和",
    "与",
    "或",
    "及",
    "在",
    "是",
    "为",
    "对",
    "中",
    "及其",
    "以及",
    "一个",
    "如何",
    "什么",
    "哪些",
    "怎么",
}

_index_cache: dict | None = None
_index_mtime: float | None = None


class SearchIndexError(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def stable_id(*parts: str) -> str:
    raw = "\n".join(parts)
    return "chunk-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def heading(line: str) -> tuple[int, str] | None:
    match = re.match(r"^(#{1,6})\s+(.*\S)\s*$", line)
    if not match:
        return None
    return len(match.group(1)), match.group(2).strip()


def tokenize(text: str) -> list[str]:
    tokens = []
    for token in jieba.cut_for_search(text):
        word = token.strip().lower()
        if len(word) < 2 or word in STOPWORDS:
            continue
        if re.fullmatch(r"[\W_]+", word):
            continue
        tokens.append(word)
    return tokens


def build_chunks() -> list[dict]:
    chunks = []
    for name in DOCUMENTS:
        lines = (MARKDOWN_DIR / name).read_text(encoding="utf-8").splitlines()
        chunks.extend(chunk_document(name, lines))
    seen = set()
    for chunk in chunks:
        if chunk["id"] in seen:
            raise RuntimeError("知识块编号重复。")
        seen.add(chunk["id"])
        for part in chunk["section"].split(" / "):
            if len(part) >= 2:
                jieba.add_word(part)
    return chunks


def chunk_document(name: str, lines: list[str]) -> list[dict]:
    chunks = []
    stack: list[tuple[int, str]] = []
    buffer: list[tuple[int, str]] = []
    section = ""

    def flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        content = [(number, text) for number, text in buffer if text.strip() and not text.startswith(">")]
        buffer = []
        if not any(not heading(text) for _, text in content):
            return
        start = content[0][0]
        end = content[-1][0]
        body = "\n".join(text for _, text in content).strip()
        if not body:
            return
        current_section = section or "文档开头"
        chunks.append(
            {
                "id": stable_id(name, current_section, str(start), str(end)),
                "document": name,
                "section": current_section,
                "locator": f"第{start}-{end}行",
                "text": body,
            }
        )

    for number, line in enumerate(lines, 1):
        found = heading(line)
        if found:
            flush()
            level, title = found
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            section = " / ".join(item for _, item in stack)
            buffer = [(number, line)]
            continue
        if line.startswith("- ▎") and buffer:
            flush()
            label = line.removeprefix("- ▎").strip()
            parent = " / ".join(item for _, item in stack)
            section = f"{parent} / {label}" if parent else label
            buffer = [(number, line)]
            continue
        if stack:
            buffer.append((number, line))
    flush()
    return chunks


def write_index(chunks: list[dict] | None = None) -> dict:
    prepared = chunks if chunks is not None else build_chunks()
    for chunk in prepared:
        chunk["tokens"] = tokenize(chunk["section"] + "\n" + chunk["text"])
    payload = {
        "method": "bm25",
        "tokenizer": "jieba-search",
        "documents": DOCUMENTS,
        "chunk_count": len(prepared),
        "chunks": prepared,
    }
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def load_index() -> dict:
    global _index_cache, _index_mtime
    if not INDEX_PATH.is_file():
        raise SearchIndexError("未找到本地知识检索索引 data/processed/rag/index.json。", 404)
    mtime = INDEX_PATH.stat().st_mtime
    if _index_cache is not None and _index_mtime == mtime:
        return _index_cache
    try:
        payload = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise SearchIndexError("知识检索索引不是有效的 JSON。", 500) from None
    except OSError:
        raise SearchIndexError("读取知识检索索引失败。", 500) from None
    if not isinstance(payload, dict) or not isinstance(payload.get("chunks"), list):
        raise SearchIndexError("知识检索索引缺少 chunks。", 500)
    for chunk in payload["chunks"]:
        section = str(chunk.get("section", ""))
        for part in section.split(" / "):
            if len(part) >= 2:
                jieba.add_word(part)
    _index_cache = payload
    _index_mtime = mtime
    return payload


def search(query: str, top_k: int = 5) -> dict:
    text = query.strip()
    if not text:
        raise SearchIndexError("请输入检索内容。", 422)
    if top_k < 1 or top_k > 20:
        raise SearchIndexError("返回条数必须在 1 到 20 之间。", 422)
    payload = load_index()
    chunks = payload["chunks"]
    query_tokens = tokenize(text)
    scores = bm25_scores(chunks, query_tokens, text)
    ranked = sorted(
        ((score, chunk) for score, chunk in zip(scores, chunks) if score > 0),
        key=lambda item: (-item[0], item[1]["id"]),
    )[:top_k]
    results = [
        {
            "id": chunk["id"],
            "text": chunk["text"],
            "document": chunk["document"],
            "section": chunk["section"],
            "locator": chunk["locator"],
            "score": round(score, 4),
        }
        for score, chunk in ranked
    ]
    return {
        "query": text,
        "top_k": top_k,
        "count": len(results),
        "message": "检索完成。" if results else "没有检索到相关知识。",
        "results": results,
    }


def bm25_scores(chunks: list[dict], query_tokens: list[str], query_text: str) -> list[float]:
    if not chunks or not query_tokens:
        return [0.0 for _ in chunks]
    docs = [chunk.get("tokens") or tokenize(chunk["section"] + "\n" + chunk["text"]) for chunk in chunks]
    total = len(docs)
    average = sum(len(tokens) for tokens in docs) / total
    document_frequency: dict[str, int] = {}
    for tokens in docs:
        for token in set(tokens):
            document_frequency[token] = document_frequency.get(token, 0) + 1
    k1 = 1.5
    b = 0.75
    scores = []
    for chunk, tokens in zip(chunks, docs):
        counts: dict[str, int] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
        score = 0.0
        length = len(tokens) or 1
        for token in query_tokens:
            frequency = counts.get(token, 0)
            if frequency == 0:
                continue
            df = document_frequency.get(token, 0)
            idf = math.log((total - df + 0.5) / (df + 0.5) + 1)
            denom = frequency + k1 * (1 - b + b * length / average)
            score += idf * (frequency * (k1 + 1)) / denom
        haystack = chunk["section"] + "\n" + chunk["text"]
        if query_text and query_text in haystack:
            score += 3
        scores.append(score)
    return scores
