"""由模型自行选择已注册工具，并保存调用轨迹。

不写入正式知识图谱，也不把模型原文当成已验证事实。
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path

from app.services.agent_tools import TOOLS, execute_tool

MAX_SUPERVISOR_ROUNDS = 3
REGISTERED_TOOLS = tuple(item["function"]["name"] for item in TOOLS)
DEFAULT_TRACE_DATABASE = Path(__file__).resolve().parents[3] / "data" / "runtime" / "supervisor_traces.sqlite3"

_CREATE_RESEARCH = """
CREATE TABLE IF NOT EXISTS supervisor_research (
    research_id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    created_at TEXT NOT NULL,
    final_status TEXT NOT NULL,
    answer TEXT NOT NULL,
    rounds INTEGER NOT NULL,
    evidence_json TEXT NOT NULL
)
"""
_CREATE_STEPS = """
CREATE TABLE IF NOT EXISTS supervisor_steps (
    research_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    round_number INTEGER NOT NULL,
    tool_name TEXT NOT NULL,
    arguments_text TEXT NOT NULL,
    result_json TEXT NOT NULL,
    elapsed_ms INTEGER NOT NULL,
    status TEXT NOT NULL,
    PRIMARY KEY (research_id, step_index)
)
"""


class SupervisorTraceError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def run_supervisor(question: str, complete, *, execute=execute_tool, max_rounds: int = MAX_SUPERVISOR_ROUNDS, system_prompt: str, limit_notice: str) -> dict:
    """按模型返回的工具调用逐轮执行。complete(messages, use_tools) 返回带 content 和 tool_calls 的消息。"""
    from app.services.deepseek import DeepSeekError

    text = question.strip() if isinstance(question, str) else ""
    if not text:
        raise DeepSeekError("请输入问题。", status_code=422)
    if max_rounds < 1:
        raise DeepSeekError("工具调用轮数必须至少为 1。", status_code=500)
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text},
    ]
    performed: list[dict] = []
    outputs: list[dict] = []
    steps: list[dict] = []
    rounds = 0
    while rounds < max_rounds:
        message = complete(messages, True)
        calls = _calls(message)
        if not calls:
            return _finish(text, message, performed, outputs, steps, rounds, "未调用工具" if rounds == 0 else "已完成")
        messages.append(_assistant_message(message, calls))
        _run_calls(calls, rounds + 1, messages, performed, outputs, steps, execute)
        rounds += 1

    message = complete(messages, True)
    calls = _calls(message)
    if not calls:
        return _finish(text, message, performed, outputs, steps, rounds, "已完成")
    for call in calls:
        call_id, name, arguments = _parts(call)
        steps.append(
            {
                "round": rounds + 1,
                "tool_name": name,
                "arguments": arguments,
                "result": {"ok": False, "tool": name, "error": "已达到最大工具调用轮数，本次调用未执行。"},
                "elapsed_ms": 0,
                "status": "未执行",
                "id": call_id,
            }
        )
    messages.append({"role": "system", "content": limit_notice})
    message = complete(messages, False)
    return _finish(text, message, performed, outputs, steps, rounds, "达到最大轮数")


def classify_evidence(tool_results: list[dict]) -> dict:
    """只根据工具返回值划分证据。模型生成的句子不会进入这三类。"""
    verified: list[str] = []
    hypotheses: list[str] = []
    insufficient: list[str] = []
    for item in tool_results:
        result = item.get("result") if isinstance(item, dict) else None
        if not isinstance(result, dict):
            insufficient.append("工具没有返回可读取的结果。")
            continue
        name = str(item.get("name") or result.get("tool") or "")
        if result.get("ok") is not True:
            error = str(result.get("error") or "工具执行失败。")
            insufficient.append(f"{name or '工具'}：{error}")
            continue
        if name == "search_knowledge":
            _classify_search(result, verified, insufficient)
        elif name == "get_equipment_relations":
            _classify_relations(result, verified, insufficient)
        elif name == "diagnose_fault":
            _classify_diagnosis(result, hypotheses, insufficient)
        elif name == "detect_anomaly":
            _classify_anomaly(result, verified, insufficient)
        elif name == "analyze_equipment_record":
            _classify_analysis(result, verified, hypotheses, insufficient)
        else:
            insufficient.append(f"{name or '工具'}：返回内容不能当作已验证事实。")
    return {
        "verified_facts": verified,
        "unverified_hypotheses": hypotheses,
        "insufficient_evidence": insufficient,
    }


def trace_database_path() -> Path:
    override = os.environ.get("SUPERVISOR_TRACE_DB", "").strip()
    path = Path(override) if override else DEFAULT_TRACE_DATABASE
    return _assert_runtime_location(path)


def save_supervisor_trace(outcome: dict) -> dict:
    """把本次调度轨迹写入运行时数据库。不打开正式知识图谱。"""
    path = trace_database_path()
    research_id = str(outcome.get("research_id") or "")
    if not research_id:
        raise SupervisorTraceError("缺少 research_id，没有保存调度轨迹。")
    evidence = outcome.get("evidence") if isinstance(outcome.get("evidence"), dict) else {}
    steps = outcome.get("steps") if isinstance(outcome.get("steps"), list) else []
    connection = _connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO supervisor_research (research_id, question, created_at, final_status, answer, rounds, evidence_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                research_id,
                str(outcome.get("question") or ""),
                datetime.now().astimezone().isoformat(timespec="seconds"),
                str(outcome.get("final_status") or ""),
                str(outcome.get("answer") or ""),
                int(outcome.get("rounds") or 0),
                json.dumps(evidence, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            connection.execute(
                "INSERT INTO supervisor_steps (research_id, step_index, round_number, tool_name, arguments_text, result_json, elapsed_ms, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    research_id,
                    index,
                    int(step.get("round") or 0),
                    str(step.get("tool_name") or ""),
                    str(step.get("arguments") or ""),
                    json.dumps(step.get("result"), ensure_ascii=False, separators=(",", ":")),
                    int(step.get("elapsed_ms") or 0),
                    str(step.get("status") or ""),
                ),
            )
        connection.execute("COMMIT")
    except sqlite3.Error as exc:
        _rollback(connection)
        raise SupervisorTraceError(f"调度轨迹没有写入数据库：{exc}") from exc
    finally:
        connection.close()
    return {"research_id": research_id, "database": str(path), "steps": len(steps)}


def load_supervisor_trace(research_id: str, path: Path | None = None) -> dict:
    database = path or trace_database_path()
    connection = _connect(database)
    try:
        research = connection.execute(
            "SELECT research_id, question, final_status, answer, rounds, evidence_json FROM supervisor_research WHERE research_id = ?",
            (research_id,),
        ).fetchone()
        if research is None:
            raise SupervisorTraceError("没有找到这次调度轨迹。")
        rows = connection.execute(
            "SELECT round_number, tool_name, arguments_text, result_json, elapsed_ms, status FROM supervisor_steps WHERE research_id = ? ORDER BY step_index",
            (research_id,),
        ).fetchall()
    finally:
        connection.close()
    return {
        "research_id": research["research_id"],
        "question": research["question"],
        "final_status": research["final_status"],
        "answer": research["answer"],
        "rounds": research["rounds"],
        "evidence": json.loads(research["evidence_json"]),
        "steps": [
            {
                "round": row["round_number"],
                "tool_name": row["tool_name"],
                "arguments": row["arguments_text"],
                "result": json.loads(row["result_json"]),
                "elapsed_ms": row["elapsed_ms"],
                "status": row["status"],
            }
            for row in rows
        ],
    }


def _finish(question: str, message, performed: list[dict], outputs: list[dict], steps: list[dict], rounds: int, final_status: str) -> dict:
    from app.services.deepseek import DeepSeekError

    content = _content(message).strip()
    if not content:
        raise DeepSeekError("DeepSeek 没有返回有效回答，请稍后重试。", status_code=502)
    evidence = classify_evidence(outputs)
    return {
        "research_id": uuid.uuid4().hex,
        "question": question,
        "answer": content,
        "tool_calls": performed,
        "tool_results": outputs,
        "steps": steps,
        "rounds": rounds,
        "final_status": final_status,
        "evidence": evidence,
    }


def _run_calls(calls, round_number: int, messages: list[dict], performed: list[dict], outputs: list[dict], steps: list[dict], execute) -> None:
    for call in calls:
        call_id, name, arguments = _parts(call)
        if not name or name not in REGISTERED_TOOLS:
            result = {
                "ok": False,
                "tool": name,
                "error": "未知工具：%s。只能调用已注册的五个工具。" % name,
            }
            status = "已拒绝"
            elapsed = 0
        else:
            started = time.perf_counter()
            try:
                result = execute(name, arguments)
            except Exception as exc:
                result = {"ok": False, "tool": name, "error": "工具执行失败：%s" % exc}
            elapsed = int((time.perf_counter() - started) * 1000)
            if not isinstance(result, dict):
                result = {"ok": False, "tool": name, "error": "工具没有返回结果对象。"}
            status = _step_status(result)
        step = {
            "round": round_number,
            "tool_name": name,
            "arguments": arguments,
            "result": result,
            "elapsed_ms": elapsed,
            "status": status,
            "id": call_id,
        }
        steps.append(step)
        performed.append({"id": call_id, "name": name, "arguments": arguments, "round": round_number})
        outputs.append({"id": call_id, "name": name, "result": result})
        if call_id:
            messages.append({"role": "tool", "tool_call_id": call_id, "content": json.dumps(result, ensure_ascii=False)})


def _step_status(result: dict) -> str:
    if result.get("ok") is True:
        return "成功"
    error = str(result.get("error") or "")
    if error.startswith("未知工具") or error.startswith("不支持的参数") or error.startswith("工具参数"):
        return "已拒绝"
    return "失败"


def _content(message) -> str:
    if isinstance(message, dict):
        return str(message.get("content") or "")
    return str(getattr(message, "content", "") or "")


def _calls(message) -> list:
    raw = getattr(message, "tool_calls", None)
    if raw is None and isinstance(message, dict):
        raw = message.get("tool_calls")
    return list(raw or [])


def _parts(call) -> tuple[str, str, str]:
    if isinstance(call, dict):
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        name = str(function.get("name") or "")
        arguments = function.get("arguments")
        call_id = str(call.get("id") or "")
    else:
        function = getattr(call, "function", None)
        name = str(getattr(function, "name", "") or "") if function is not None else ""
        arguments = getattr(function, "arguments", None) if function is not None else None
        call_id = str(getattr(call, "id", "") or "")
    if not isinstance(arguments, str):
        arguments = "" if arguments is None else json.dumps(arguments, ensure_ascii=False)
    return call_id, name, arguments


def _assistant_message(message, calls) -> dict:
    content = _content(message) or None
    encoded = []
    for call in calls:
        call_id, name, arguments = _parts(call)
        encoded.append({"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}})
    return {"role": "assistant", "content": content, "tool_calls": encoded}


def _classify_search(result: dict, verified: list[str], insufficient: list[str]) -> None:
    rows = result.get("results") if isinstance(result.get("results"), list) else []
    if not rows:
        insufficient.append("知识检索没有返回资料。")
        return
    kept = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        document = str(row.get("document") or "")
        locator = str(row.get("locator") or "")
        if document:
            verified.append(f"{document} {locator}".strip())
            kept = True
    if not kept:
        insufficient.append("知识检索没有返回可核对的资料来源。")


def _classify_relations(result: dict, verified: list[str], insufficient: list[str]) -> None:
    relations = result.get("relations") if isinstance(result.get("relations"), list) else []
    if not relations:
        insufficient.append(str(result.get("message") or "知识图谱没有返回可用关系。"))
        return
    for item in relations:
        if not isinstance(item, dict):
            continue
        verified.append(
            f"{item.get('source_name') or ''} {item.get('relation') or ''} {item.get('target_name') or ''}".strip()
        )


def _classify_diagnosis(result: dict, hypotheses: list[str], insufficient: list[str]) -> None:
    if result.get("status") != "possible_match":
        insufficient.append(str(result.get("message") or "故障特征对照证据不足。"))
        return
    matches = result.get("matches") if isinstance(result.get("matches"), list) else []
    if not matches:
        insufficient.append("故障特征对照没有返回可核对的匹配。")
        return
    for match in matches:
        if not isinstance(match, dict):
            continue
        fault = str(match.get("fault") or "").strip()
        if fault:
            hypotheses.append(f"{fault}：工具返回的是可能匹配，不是确诊。")


def _classify_anomaly(result: dict, verified: list[str], insufficient: list[str]) -> None:
    status = str(result.get("detection_status") or "")
    record_id = str(result.get("record_id") or "")
    if status in {"基线不足", "无法判定"}:
        insufficient.append(f"{record_id} 的统计检测状态为{status}。".strip())
        return
    if status:
        verified.append(f"{record_id} 的统计检测状态为{status}。这是统计结果，不是故障确诊。".strip())
        return
    insufficient.append("统计偏离工具没有返回检测状态。")


def _classify_analysis(result: dict, verified: list[str], hypotheses: list[str], insufficient: list[str]) -> None:
    level = str(result.get("diagnosis_level") or "")
    record_id = str(result.get("record_id") or "")
    deviation = result.get("statistical_deviation") if isinstance(result.get("statistical_deviation"), dict) else {}
    detection = str(deviation.get("detection_status") or result.get("detection_status") or "")
    if detection:
        verified.append(f"{record_id} 的统计检测状态为{detection}。这是统计结果，不是故障确诊。".strip())
    if level == "possible_match":
        hypotheses.append(f"{record_id} 存在可能匹配的知识特征，不是故障确诊。".strip())
    elif level == "diagnostic_clue":
        hypotheses.append(f"{record_id} 只有部分诊断线索，不是故障确诊。".strip())
    elif level in {"insufficient_evidence", "unable_to_evaluate", "statistical_deviation"}:
        insufficient.append(f"{record_id} 的诊断级别为{level}，证据不足以确定具体故障。".strip())
    elif not detection:
        insufficient.append("记录分析没有返回可核对的诊断级别。")


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute(_CREATE_RESEARCH)
    connection.execute(_CREATE_STEPS)
    return connection


def _rollback(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("ROLLBACK")
    except sqlite3.Error:
        return


def _assert_runtime_location(path: Path) -> Path:
    root = Path(__file__).resolve().parents[3]
    resolved = path if path.is_absolute() else root / path
    try:
        resolved = resolved.resolve()
    except OSError as exc:
        raise SupervisorTraceError(f"调度轨迹数据库无法打开：{exc}") from exc
    forbidden = (
        (root / "data" / "official").resolve(),
        (root / "data" / "official_dataset").resolve(),
        (root / "data" / "processed").resolve(),
    )
    if any(resolved == base or base in resolved.parents for base in forbidden):
        raise SupervisorTraceError("调度轨迹不能写入官方数据或正式知识图谱目录。")
    graph = (root / "data" / "processed" / "graph" / "graph.json").resolve()
    if resolved == graph:
        raise SupervisorTraceError("调度轨迹不能写入正式知识图谱。")
    return resolved
