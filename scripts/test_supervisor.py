"""用模拟模型和临时数据库验证 Supervisor 调度。不调用真实 DeepSeek。"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TEMP_DIR = Path(tempfile.mkdtemp(prefix="supervisor-trace-"))
TEMP_DB = TEMP_DIR / "traces.sqlite3"
TEMP_EVENTS = TEMP_DIR / "events.sqlite3"
os.environ["SUPERVISOR_TRACE_DB"] = str(TEMP_DB)
os.environ["MONITORING_EVENTS_DB"] = str(TEMP_EVENTS)
os.environ["STARTUP_TOOL_SELFTEST"] = "0"

from app.services.agent_tools import execute_tool
from app.services.deepseek import TOOLS, DeepSeekError
from app.services.supervisor import (
    SupervisorTraceError,
    load_supervisor_trace,
    run_supervisor,
    save_supervisor_trace,
    trace_database_path,
)

GRAPH = ROOT / "data" / "processed" / "graph" / "graph.json"
EXCEL = ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"
PRODUCTION_DB = ROOT / "data" / "runtime" / "monitoring_events.sqlite3"
ENV_FILE = ROOT / "backend" / ".env"
PROTECTED = (
    GRAPH,
    EXCEL,
    PRODUCTION_DB,
    ENV_FILE,
    ROOT / "backend" / "app" / "services" / "agent_tools.py",
    ROOT / "backend" / "app" / "services" / "anomaly_detection.py",
    ROOT / "backend" / "app" / "services" / "diagnostic_bridge.py",
)
PROMPT = "测试用系统提示。只依据工具结果回答。"
NOTICE = "工具调用已达到上限。未执行的调用不能当成已经执行。"
RESULTS: list[bool] = []


class Turn:
    def __init__(self, content: str | None = None, tool_calls: list | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []


def file_hash(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append(passed)
    print(("PASS " if passed else "FAIL ") + name + ("" if not detail else " | " + detail))


def tool_call(call_id: str, name: str, arguments: str) -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def scripted(turns: list[Turn]):
    pending = list(turns)
    seen: list[dict] = []

    def complete(messages, use_tools: bool):
        seen.append({"use_tools": use_tools, "messages": [dict(item) for item in messages]})
        if not pending:
            raise AssertionError("模拟模型被调用的次数超过脚本。")
        return pending.pop(0)

    complete.seen = seen
    return complete


def persist(outcome: dict) -> dict:
    save_supervisor_trace(outcome)
    return load_supervisor_trace(outcome["research_id"])


def test_single_tool() -> None:
    executed: list[str] = []

    def execute(name: str, arguments: str) -> dict:
        executed.append(name)
        return {
            "ok": True,
            "tool": name,
            "query": "轴承",
            "count": 1,
            "results": [{"document": "临时资料.md", "section": "轴承", "locator": "第1行", "text": "临时证据"}],
        }

    complete = scripted(
        [
            Turn(tool_calls=[tool_call("c1", "search_knowledge", '{"query":"轴承"}')]),
            Turn(content="最终回答：只引用了临时资料第1行。"),
        ]
    )
    outcome = run_supervisor("轴承资料在哪里", complete, execute=execute, system_prompt=PROMPT, limit_notice=NOTICE)
    stored = persist(outcome)
    evidence = outcome["evidence"]
    model_sentence = "最终回答：只引用了临时资料第1行。"
    check(
        "单工具调用",
        executed == ["search_knowledge"]
        and outcome["final_status"] == "已完成"
        and outcome["rounds"] == 1
        and outcome["tool_calls"][0]["name"] == "search_knowledge"
        and outcome["tool_results"][0]["result"]["ok"] is True
        and stored["steps"][0]["status"] == "成功"
        and stored["steps"][0]["arguments"] == '{"query":"轴承"}'
        and stored["steps"][0]["elapsed_ms"] >= 0
        and "临时资料.md 第1行" in evidence["verified_facts"]
        and model_sentence not in evidence["verified_facts"]
        and outcome["answer"] == model_sentence,
    )


def test_multiple_tools() -> None:
    executed: list[str] = []

    def execute(name: str, arguments: str) -> dict:
        executed.append(name)
        if name == "diagnose_fault":
            return {
                "ok": True,
                "tool": name,
                "status": "possible_match",
                "confirmed": False,
                "matches": [{"fault": "轴承磨损", "confirmed": False}],
            }
        if name == "search_knowledge":
            return {
                "ok": True,
                "tool": name,
                "count": 1,
                "results": [{"document": "KB-临时.md", "locator": "第2行", "text": "临时检索"}],
            }
        return {"ok": True, "tool": name, "record_id": "REC-TEMP-1", "detection_status": "基线不足"}

    complete = scripted(
        [
            Turn(
                tool_calls=[
                    tool_call("a", "diagnose_fault", '{"observed_features":["振动升高"]}'),
                    tool_call("b", "search_knowledge", '{"query":"振动"}'),
                ]
            ),
            Turn(tool_calls=[tool_call("c", "detect_anomaly", '{"record_id":"REC-TEMP-1"}')]),
            Turn(content="模型原文标记-不要入库"),
        ]
    )
    outcome = run_supervisor("请综合这些临时资料", complete, execute=execute, system_prompt=PROMPT, limit_notice=NOTICE)
    second_messages = complete.seen[1]["messages"]
    third_messages = complete.seen[2]["messages"]
    tool_messages = [item for item in second_messages if item.get("role") == "tool"]
    later_tools = [item for item in third_messages if item.get("role") == "tool"]
    evidence = outcome["evidence"]
    joined_verified = "\n".join(evidence["verified_facts"])
    check(
        "多工具连续调用",
        executed == ["diagnose_fault", "search_knowledge", "detect_anomaly"]
        and len(tool_messages) == 2
        and len(later_tools) == 3
        and "基线不足" in later_tools[-1]["content"]
        and outcome["rounds"] == 2
        and outcome["final_status"] == "已完成"
        and "轴承磨损" not in joined_verified
        and any("不是确诊" in item for item in evidence["unverified_hypotheses"])
        and any("基线不足" in item for item in evidence["insufficient_evidence"])
        and "模型原文标记-不要入库" not in joined_verified
        and "模型原文标记-不要入库" not in "\n".join(evidence["unverified_hypotheses"]),
    )


def test_bad_parameters() -> None:
    executed: list[str] = []

    def execute(name: str, arguments: str) -> dict:
        executed.append(name)
        return execute_tool(name, arguments)

    complete = scripted(
        [
            Turn(
                tool_calls=[
                    tool_call("bad-name", "drop_database", "{}"),
                    tool_call("bad-args", "search_knowledge", '{"not_a_field": 1}'),
                ]
            ),
            Turn(content="参数没有通过校验，没有形成资料依据。"),
        ]
    )
    outcome = run_supervisor("查一个不存在的参数", complete, execute=execute, system_prompt=PROMPT, limit_notice=NOTICE)
    stored = persist(outcome)
    results = [item["result"] for item in outcome["tool_results"]]
    check(
        "工具参数错误",
        executed == ["search_knowledge"]
        and stored["steps"][0]["status"] == "已拒绝"
        and stored["steps"][1]["status"] == "已拒绝"
        and results[0]["ok"] is False
        and results[0]["error"].startswith("未知工具")
        and results[1]["ok"] is False
        and results[1]["error"].startswith("不支持的参数")
        and outcome["evidence"]["verified_facts"] == [],
    )


def test_execution_failure() -> None:
    def execute(name: str, arguments: str) -> dict:
        raise RuntimeError("读取失败，没有得到结果")

    complete = scripted(
        [
            Turn(tool_calls=[tool_call("fail", "get_equipment_relations", '{"name":"临时设备"}')]),
            Turn(content="工具失败，不能把这次调用写成成功。"),
        ]
    )
    outcome = run_supervisor("查询临时设备关系", complete, execute=execute, system_prompt=PROMPT, limit_notice=NOTICE)
    stored = persist(outcome)
    result = stored["steps"][0]["result"]
    fed_back = complete.seen[1]["messages"][-1]["content"]
    check(
        "工具执行失败",
        stored["steps"][0]["status"] == "失败"
        and result["ok"] is False
        and result["error"] == "工具执行失败：读取失败，没有得到结果"
        and "读取失败，没有得到结果" in fed_back
        and outcome["evidence"]["verified_facts"] == []
        and any("读取失败" in item for item in outcome["evidence"]["insufficient_evidence"]),
    )


def test_max_rounds() -> None:
    executed: list[str] = []

    def execute(name: str, arguments: str) -> dict:
        executed.append(name)
        return {"ok": True, "tool": name, "count": 0, "results": []}

    complete = scripted(
        [
            Turn(tool_calls=[tool_call("r1", "search_knowledge", '{"query":"第一轮"}')]),
            Turn(tool_calls=[tool_call("r2", "search_knowledge", '{"query":"第二轮"}')]),
            Turn(tool_calls=[tool_call("r3", "detect_anomaly", '{"record_id":"REC-NOT-RUN"}')]),
            Turn(content="已到达最大轮数后的最终回答。"),
        ]
    )
    outcome = run_supervisor(
        "连续查询",
        complete,
        execute=execute,
        max_rounds=2,
        system_prompt=PROMPT,
        limit_notice=NOTICE,
    )
    stored = persist(outcome)
    check(
        "达到最大轮数",
        executed == ["search_knowledge", "search_knowledge"]
        and outcome["final_status"] == "达到最大轮数"
        and stored["steps"][2]["status"] == "未执行"
        and stored["steps"][2]["tool_name"] == "detect_anomaly"
        and stored["steps"][2]["result"]["ok"] is False
        and "未执行" in stored["steps"][2]["result"]["error"]
        and all(item["name"] != "detect_anomaly" for item in outcome["tool_results"])
        and complete.seen[-1]["use_tools"] is False
        and outcome["answer"] == "已到达最大轮数后的最终回答。",
    )


def test_no_tool_call() -> None:
    executed: list[str] = []

    def execute(name: str, arguments: str) -> dict:
        executed.append(name)
        return {"ok": True, "tool": name}

    complete = scripted([Turn(content="这次没有调用工具。")])
    outcome = run_supervisor("只需要直接说明", complete, execute=execute, system_prompt=PROMPT, limit_notice=NOTICE)
    stored = persist(outcome)
    check(
        "模型未调用工具",
        executed == []
        and outcome["final_status"] == "未调用工具"
        and outcome["tool_calls"] == []
        and outcome["tool_results"] == []
        and stored["steps"] == []
        and complete.seen[0]["use_tools"] is True
        and outcome["answer"] == "这次没有调用工具。",
    )


def test_final_answer_and_chat_contract() -> None:
    from app.main import ChatResponse, app, evidence_from_tools

    complete = scripted(
        [
            Turn(tool_calls=[tool_call("s", "search_knowledge", '{"query":"空"}')]),
            Turn(content="最终回答已经生成。"),
        ]
    )

    def execute(name: str, arguments: str) -> dict:
        return {"ok": False, "tool": name, "error": "知识检索没有返回资料。", "results": [{"document": "伪造.md"}]}

    outcome = run_supervisor("生成最终回答", complete, execute=execute, system_prompt=PROMPT, limit_notice=NOTICE)
    response = ChatResponse(answer=outcome["answer"], sources=[])
    executed_flag, supported, sources = evidence_from_tools(outcome["tool_results"], "生成最终回答")
    paths = {getattr(route, "path", "") for route in app.routes}
    required = {
        "/api/ai/chat",
        "/api/knowledge/graph",
        "/api/monitoring/events",
        "/api/monitoring/auto/status",
        "/api/monitoring/auto/start",
        "/api/monitoring/auto/stop",
    }
    check(
        "最终回答生成",
        outcome["answer"] == "最终回答已经生成。"
        and response.answer == outcome["answer"]
        and set(outcome) >= {"answer", "tool_calls", "tool_results"}
        and executed_flag is True
        and supported is False
        and sources == []
        and outcome["evidence"]["verified_facts"] == []
        and required <= paths
        and len(TOOLS) == 5,
    )


def test_adapter_saves_trace(monkey_state: dict) -> None:
    import app.services.agent_tools as agent_tools
    import app.services.deepseek as deepseek

    class Function:
        def __init__(self, name: str, arguments: str) -> None:
            self.name = name
            self.arguments = arguments

    class Call:
        def __init__(self, call_id: str, name: str, arguments: str) -> None:
            self.id = call_id
            self.function = Function(name, arguments)

    class Message:
        def __init__(self, content, tool_calls) -> None:
            self.content = content
            self.tool_calls = tool_calls

    script = [
        Message(None, [Call("live", "search_knowledge", '{"query":"临时"}')]),
        Message("适配层最终回答。", None),
    ]
    recorded: list[dict] = []

    class Completions:
        def create(self, **kwargs):
            recorded.append(kwargs)
            message = script.pop(0)
            choice = type("Choice", (), {"message": message})()
            return type("Response", (), {"choices": [choice]})()

    class FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            self.chat = type("Chat", (), {"completions": Completions()})()

        def close(self) -> None:
            return None

    def fake_execute(name: str, arguments: str) -> dict:
        return {
            "ok": True,
            "tool": name,
            "count": 1,
            "results": [{"document": "适配临时.md", "locator": "第3行", "text": "临时"}],
        }

    monkey_state["openai"] = deepseek.OpenAI
    monkey_state["key"] = deepseek._api_key
    monkey_state["execute"] = agent_tools.execute_tool
    deepseek.OpenAI = FakeOpenAI
    deepseek._api_key = lambda: "test-key"
    agent_tools.execute_tool = fake_execute
    try:
        outcome = deepseek.ask_with_tools("请查询临时问题")
    finally:
        deepseek.OpenAI = monkey_state["openai"]
        deepseek._api_key = monkey_state["key"]
        agent_tools.execute_tool = monkey_state["execute"]
    stored = load_supervisor_trace(outcome["research_id"])
    names = [item["function"]["name"] for item in recorded[0]["tools"]]
    tool_roles = [item.get("role") for item in recorded[1]["messages"]]
    check(
        "问答适配层保存轨迹",
        outcome["trace_saved"] is True
        and outcome["answer"] == "适配层最终回答。"
        and recorded[0]["tool_choice"] == "auto"
        and names == ["search_knowledge", "get_equipment_relations", "diagnose_fault", "detect_anomaly", "analyze_equipment_record"]
        and "tool" in tool_roles
        and stored["steps"][0]["status"] == "成功"
        and stored["final_status"] == "已完成",
    )


def test_trace_cannot_enter_official_data() -> None:
    target = ROOT / "data" / "official" / "supervisor-forbidden.sqlite3"
    previous = os.environ.get("SUPERVISOR_TRACE_DB")
    os.environ["SUPERVISOR_TRACE_DB"] = str(target)
    raised = False
    try:
        trace_database_path()
    except SupervisorTraceError:
        raised = True
    finally:
        if previous is None:
            os.environ.pop("SUPERVISOR_TRACE_DB", None)
        else:
            os.environ["SUPERVISOR_TRACE_DB"] = previous
    check("轨迹不写入官方目录", raised and not target.exists())


def test_empty_inputs() -> None:
    def unused(messages, use_tools: bool):
        raise AssertionError("空问题不应调用模型。")

    empty_question = False
    try:
        run_supervisor("  ", unused, system_prompt=PROMPT, limit_notice=NOTICE)
    except DeepSeekError as exc:
        empty_question = exc.status_code == 422
    empty_answer = False
    try:
        run_supervisor("需要回答", scripted([Turn(content="  ")]), system_prompt=PROMPT, limit_notice=NOTICE)
    except DeepSeekError as exc:
        empty_answer = exc.status_code == 502
    check("空问题与空回答", empty_question and empty_answer)


def main() -> None:
    before = {path: file_hash(path) for path in PROTECTED}
    test_single_tool()
    test_multiple_tools()
    test_bad_parameters()
    test_execution_failure()
    test_max_rounds()
    test_no_tool_call()
    test_final_answer_and_chat_contract()
    test_adapter_saves_trace({})
    test_trace_cannot_enter_official_data()
    test_empty_inputs()
    after = {path: file_hash(path) for path in PROTECTED}
    unchanged = before == after and trace_database_path() == TEMP_DB.resolve()
    check("正式数据未被修改", unchanged)
    passed = sum(RESULTS)
    failed = len(RESULTS) - passed
    print(f"RESULT passed={passed} failed={failed}")
    raise SystemExit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
