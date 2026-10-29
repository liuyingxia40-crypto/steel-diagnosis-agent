"""用模拟工具验证闭环修正。不调用真实模型，不写正式图谱。"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
TEMP_DIR = Path(tempfile.mkdtemp(prefix="fault-revision-"))
os.environ["SUPERVISOR_TRACE_DB"] = str(TEMP_DIR / "traces.sqlite3")
os.environ["MONITORING_EVENTS_DB"] = str(TEMP_DIR / "events.sqlite3")
os.environ["STARTUP_TOOL_SELFTEST"] = "0"

from app.services.deepseek import DeepSeekError
from app.services.fault_revision import run_closed_loop
from app.services.supervisor import load_supervisor_trace, save_supervisor_trace

GRAPH = ROOT / "data" / "processed" / "graph" / "graph.json"
EXCEL = ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"
PRODUCTION_DB = ROOT / "data" / "runtime" / "monitoring_events.sqlite3"
ENV_FILE = ROOT / "backend" / ".env"
PROTECTED = (
    GRAPH,
    EXCEL,
    PRODUCTION_DB,
    ENV_FILE,
    ROOT / "backend" / "app" / "services" / "knowledge_physical_validator.py",
)
RESULTS: list[bool] = []


def file_hash(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append(passed)
    print(("PASS " if passed else "FAIL ") + name + ("" if not detail else " | " + detail))


def tool(name: str, result: dict, arguments: dict | None = None) -> dict:
    return {"id": name, "name": name, "result": result, "arguments": arguments or {}}


def diagnosis(fault: str, equipment: str = "") -> dict:
    return {
        "ok": True,
        "status": "possible_match",
        "confirmed": False,
        "equipment_name": equipment or None,
        "matches": [
            {
                "fault": fault,
                "confirmed": False,
                "matched_features": [{"document": "KB-02.md", "locator": "第1行"}],
            }
        ],
    }


def relations() -> dict:
    return {
        "ok": True,
        "ambiguous": False,
        "count": 4,
        "node": {"name": "棒线主轧机电机"},
        "relations": [
            {"source_name": "棒线主轧机电机", "relation": "HAS_COMPONENT", "target_name": "驱动端轴承"},
            {"source_name": "棒线主轧机电机", "relation": "HAS_COMPONENT", "target_name": "非驱动端轴承"},
            {"source_name": "棒线主轧机电机", "relation": "CONNECTS_TO", "target_name": "棒线联轴器"},
            {"source_name": "棒线主轧机电机", "relation": "SUPPORTED_BY", "target_name": "KB-01_设备知识图谱.docx"},
        ],
    }


def scripted(mapping: dict):
    seen = []

    def execute(name: str, arguments: str) -> dict:
        seen.append((name, arguments))
        handler = mapping[name]
        return handler(json.loads(arguments))

    execute.seen = seen
    return execute


def test_new_support() -> None:
    execute = scripted(
        {
            "search_knowledge": lambda arguments: {
                "ok": True,
                "query": arguments["query"],
                "count": 1,
                "results": [{"document": "KB-02.md", "locator": "第8行"}],
            },
            "get_equipment_relations": lambda arguments: relations(),
        }
    )
    loop = run_closed_loop(
        "可能故障是什么？",
        [tool("diagnose_fault", diagnosis("轴承磨损", "棒线主轧机电机"))],
        execute=execute,
        physics=lambda *args: (_ for _ in ()).throw(AssertionError("没有记录编号时不应调用 L2")),
        max_rounds=3,
    )
    reasons = [item.get("revision_reason") or "" for item in loop["fault_report"]["hypotheses"]]
    versions = [item.get("version") or 1 for item in loop["fault_report"]["hypotheses"]]
    check(
        "首轮假设得到新证据支持",
        any("新证据支持原假设" in item for item in reasons)
        and max(versions) >= 2
        and any(item.get("faults") == ["轴承磨损"] for item in loop["fault_report"]["hypotheses"])
        and all(item.get("verification_status") != "已验证" for item in loop["fault_report"]["hypotheses"]),
    )


def test_contradiction() -> None:
    execute = scripted(
        {
            "search_knowledge": lambda arguments: {"ok": True, "query": arguments["query"], "count": 0, "results": []},
            "analyze_equipment_record": lambda arguments: {
                "ok": True,
                "record_id": arguments["record_id"],
                "device_id": "MM-01",
                "diagnosis_level": "insufficient_evidence",
                "detection_status": "未发现显著偏离",
                "candidate_faults": [],
            },
        }
    )
    loop = run_closed_loop(
        "记录 REC-TEMP-9 的可能故障是什么？",
        [tool("diagnose_fault", diagnosis("轴承磨损"))],
        execute=execute,
        max_rounds=3,
    )
    hypothesis = next(item for item in loop["fault_report"]["hypotheses"] if item["faults"] == ["轴承磨损"])
    check(
        "新证据与原假设矛盾后重新评估",
        hypothesis["verification_status"] == "被削弱"
        and "未发现显著偏离" in hypothesis["revision_reason"]
        and hypothesis["faults"] == ["轴承磨损"],
    )


def test_replaced_hypothesis() -> None:
    execute = scripted(
        {
            "search_knowledge": lambda arguments: {"ok": True, "query": arguments["query"], "count": 0, "results": []},
            "analyze_equipment_record": lambda arguments: {
                "ok": True,
                "record_id": arguments["record_id"],
                "device_id": "MM-01",
                "diagnosis_level": "possible_match",
                "confirmed": False,
                "candidate_faults": [{"fault": "润滑不良", "confirmed": False}],
                "detection_status": "偏离基线",
            },
        }
    )
    loop = run_closed_loop(
        "记录 REC-TEMP-8 的可能故障是什么？",
        [tool("diagnose_fault", diagnosis("轴承磨损"))],
        execute=execute,
        max_rounds=3,
    )
    hypotheses = loop["fault_report"]["hypotheses"]
    old = next(item for item in hypotheses if item["faults"] == ["轴承磨损"])
    new = next(item for item in hypotheses if item["faults"] == ["润滑不良"])
    check(
        "原假设被否定后保留并形成新候选",
        old["verification_status"] == "被证据否定"
        and "被否定" in old["revision_reason"]
        and new["revision_reason"].startswith("由新增工具结果形成候选假设")
        and new["causal_conclusion"] is False,
    )


def test_no_record_skips_l2() -> None:
    called = []

    def physics(*args):
        called.append(args)
        return {}

    loop = run_closed_loop(
        "可能故障是什么？",
        [tool("diagnose_fault", diagnosis("轴承磨损"))],
        execute=scripted({"search_knowledge": lambda arguments: {"ok": True, "query": arguments["query"], "count": 0, "results": []}}),
        physics=physics,
        max_rounds=2,
    )
    check(
        "缺少记录编号时不执行 L2",
        called == []
        and any("没有记录编号" in item for item in loop["fault_report"]["missing_capabilities"]),
    )


def test_l2_contradiction_not_confirmed() -> None:
    def physics(entity_a: str, entity_b: str, record_id: str) -> dict:
        return {
            "overall_status": "numeric_contradiction",
            "contradicted_checks": ["speed_frequency"],
            "physical_checks": [{"check_id": "speed_frequency", "title": "转速与转频一致性", "status": "contradicted"}],
            "missing_measurements": [],
            "causal_conclusion": False,
            "written_to_graph": False,
        }

    initial = [
        tool("diagnose_fault", diagnosis("轴承磨损", "棒线主轧机电机")),
        tool("get_equipment_relations", relations()),
        tool("detect_anomaly", {"ok": True, "record_id": "REC-TEMP-4", "device_id": "MM-01", "detection_status": "偏离基线"}),
    ]
    loop = run_closed_loop("可能故障是什么？", initial, execute=scripted({}), physics=physics, max_rounds=1)
    answer = loop["fault_report"]["public_answer"]
    hypothesis = next(item for item in loop["fault_report"]["hypotheses"] if item["faults"] == ["轴承磨损"])
    check(
        "L2 数值矛盾时不输出确诊",
        hypothesis["verification_status"] == "被削弱"
        and loop["fault_report"]["physics"]["called"] is True
        and "已经确诊" not in answer
        and "不等于诊断成功" in answer
        and loop["fault_report"]["causal_conclusion"] is False,
    )


def test_names_are_not_causal() -> None:
    loop = run_closed_loop(
        "请判断是否存在复合故障。",
        [tool("diagnose_fault", {"ok": True, "status": "possible_match", "confirmed": False, "matches": [
            {"fault": "轴承磨损", "confirmed": False, "matched_features": [{"document": "KB-02.md", "locator": "第1行"}]},
            {"fault": "润滑不良", "confirmed": False, "matched_features": [{"document": "KB-02.md", "locator": "第2行"}]},
        ]})],
        execute=scripted({}),
        max_rounds=1,
    )
    compound = next(item for item in loop["fault_report"]["hypotheses"] if item["compound"])
    check(
        "多个故障名称不会自动变成因果链",
        compound["causal_conclusion"] is False
        and "不能据此认定因果关系" in compound["basis"]
        and "导致" not in loop["fault_report"]["public_answer"],
    )


def test_ambiguous_not_selected() -> None:
    execute = scripted({"search_knowledge": lambda arguments: {"ok": True, "query": arguments["query"], "count": 0, "results": []}})
    ambiguous = {
        "ok": True,
        "ambiguous": True,
        "count": 0,
        "relations": [],
        "candidates": [{"name": "驱动端轴承/外圈"}, {"name": "非驱动端轴承/外圈"}],
    }
    loop = run_closed_loop(
        "同名部件的根因可能是什么故障？",
        [tool("get_equipment_relations", ambiguous), tool("diagnose_fault", diagnosis("轴承磨损"))],
        execute=execute,
        max_rounds=2,
    )
    arguments = " ".join(item[1] for item in execute.seen)
    hypothesis = next(item for item in loop["fault_report"]["hypotheses"] if item["faults"] == ["轴承磨损"])
    check(
        "同名节点无法消歧时不擅自选择",
        "驱动端轴承/外圈" not in arguments
        and "非驱动端轴承/外圈" not in arguments
        and hypothesis["equipment"] == []
        and hypothesis["components"] == [],
    )


def test_two_empty_rounds_stop() -> None:
    execute = scripted(
        {
            "search_knowledge": lambda arguments: {"ok": True, "query": arguments["query"], "count": 0, "results": []},
            "get_equipment_relations": lambda arguments: {"ok": True, "ambiguous": False, "count": 0, "node": None, "relations": [], "message": "没有找到节点。"},
        }
    )
    loop = run_closed_loop(
        "可能故障是什么？",
        [tool("diagnose_fault", diagnosis("轴承磨损", "棒线主轧机电机"))],
        execute=execute,
        max_rounds=3,
    )
    check(
        "连续两轮没有新证据时停止",
        loop["stop_reason"] == "连续两轮没有新增有效证据"
        and [item[0] for item in execute.seen] == ["search_knowledge", "get_equipment_relations"],
    )


def test_max_rounds() -> None:
    execute = scripted({"search_knowledge": lambda arguments: {"ok": True, "query": arguments["query"], "count": 1, "results": [{"document": "临时.md", "locator": "第1行"}]}})
    loop = run_closed_loop("可能故障是什么？", [tool("diagnose_fault", diagnosis("轴承磨损"))], execute=execute, max_rounds=1)
    check(
        "达到最大轮数时停止",
        loop["stop_reason"] == "达到最大推理轮数"
        and execute.seen == []
        and "达到最大推理轮数不等于诊断成功。" in loop["fault_report"]["public_answer"],
    )


def test_failures_are_kept() -> None:
    def broken(name: str, arguments: str) -> dict:
        raise RuntimeError("读取失败")

    tool_loop = run_closed_loop("可能故障是什么？", [tool("diagnose_fault", diagnosis("轴承磨损"))], execute=broken, max_rounds=2)

    def fail_model(_payload):
        raise DeepSeekError("模型超时", status_code=504)

    model_loop = run_closed_loop(
        "可能故障是什么？",
        [tool("diagnose_fault", diagnosis("轴承磨损"))],
        execute=scripted({}),
        model=fail_model,
        max_rounds=2,
    )
    tool_error = tool_loop["rounds"][-1].get("tool_error") or ""
    model_error = model_loop["rounds"][-1].get("model_error") or ""
    check(
        "工具失败或模型失败时保留错误",
        tool_loop["stop_reason"] == "工具或模型调用失败"
        and "读取失败" in tool_error
        and model_loop["stop_reason"] == "工具或模型调用失败"
        and "模型超时" in model_error
        and tool_loop["rounds"][-1]["tool_call"]["result_ok"] is False,
    )


def test_trace_rounds() -> None:
    execute = scripted({"search_knowledge": lambda arguments: {"ok": True, "query": arguments["query"], "count": 1, "results": [{"document": "KB-02.md", "locator": "第4行"}]}})
    loop = run_closed_loop("可能故障是什么？", [tool("diagnose_fault", diagnosis("轴承磨损"))], execute=execute, max_rounds=2)
    outcome = {
        "research_id": "revision-trace",
        "question": "可能故障是什么？",
        "final_status": loop["stop_reason"],
        "answer": loop["fault_report"]["public_answer"],
        "rounds": len(loop["rounds"]),
        "evidence": {"revision_rounds": loop["rounds"], "stop_reason": loop["stop_reason"], "fault_hypotheses": loop["fault_report"]["hypotheses"]},
        "steps": loop["extra_steps"],
    }
    save_supervisor_trace(outcome)
    stored = load_supervisor_trace("revision-trace")
    stored_rounds = stored["evidence"]["revision_rounds"]
    hypothesis = stored_rounds[0]["hypotheses"][0]
    check(
        "每轮修正可追溯",
        stored["evidence"]["stop_reason"] == loop["stop_reason"]
        and hypothesis["hypothesis_id"]
        and hypothesis["version"] >= 1
        and hypothesis["revision_reason"]
        and stored["steps"][0]["tool_name"] == "search_knowledge"
        and "elapsed_ms" in stored["steps"][0]
        and "physics" in stored_rounds[0]
        and stored_rounds[0]["changes"]["added_evidence"] is not None,
    )


def hypothesis_named(loop: dict, fault: str) -> dict:
    return next(item for item in loop["fault_report"]["hypotheses"] if item.get("faults") == [fault])


def test_version_unchanged_without_new_evidence() -> None:
    execute = scripted({"search_knowledge": lambda arguments: {"ok": True, "query": arguments["query"], "count": 0, "results": []}})
    loop = run_closed_loop(
        "可能故障是什么？",
        [tool("diagnose_fault", diagnosis("轴承磨损"))],
        execute=execute,
        max_rounds=2,
    )
    hypothesis = hypothesis_named(loop, "轴承磨损")
    first = loop["rounds"][0]["hypotheses"][0]
    check(
        "没有新证据时版本不变",
        hypothesis["version"] == 1
        and first["version"] == 1
        and "没有实质变化" in hypothesis["revision_reason"]
        and hypothesis["verification_status"] != "已验证"
        and loop["rounds"][-1]["changes"]["material_change"] is False,
    )


def test_duplicate_evidence_does_not_bump_version() -> None:
    same = {"ok": True, "query": "轴承磨损", "count": 1, "results": [{"document": "KB-02.md", "locator": "第8行"}]}
    execute = scripted({"search_knowledge": lambda arguments: dict(same, query=arguments["query"])})
    loop = run_closed_loop(
        "可能故障是什么？",
        [tool("diagnose_fault", diagnosis("轴承磨损")), tool("search_knowledge", dict(same, query="轴承"), {"query": "轴承"})],
        execute=execute,
        max_rounds=2,
    )
    hypothesis = hypothesis_named(loop, "轴承磨损")
    check(
        "重复获取相同证据不增加版本",
        execute.seen
        and execute.seen[0][0] == "search_knowledge"
        and hypothesis["version"] == 1
        and "没有实质变化" in hypothesis["revision_reason"]
        and hypothesis["verification_status"] != "已验证",
    )


def test_new_evidence_bumps_version_with_id() -> None:
    execute = scripted(
        {
            "search_knowledge": lambda arguments: {
                "ok": True,
                "query": arguments["query"],
                "count": 1,
                "results": [{"document": "KB-02.md", "locator": "第8行"}],
            }
        }
    )
    loop = run_closed_loop("可能故障是什么？", [tool("diagnose_fault", diagnosis("轴承磨损"))], execute=execute, max_rounds=2)
    hypothesis = hypothesis_named(loop, "轴承磨损")
    evidence_ids = [item.get("evidence_id") for item in hypothesis["supporting_evidence"]]
    history = hypothesis.get("version_history") or []
    check(
        "新的有效支持证据才增加版本",
        hypothesis["version"] == 2
        and history
        and history[0]["version"] == 1
        and history[0]["verification_status"] != "被证据否定"
        and "新证据支持原假设" in hypothesis["revision_reason"]
        and "S1" in hypothesis["revision_reason"]
        and "S1" in evidence_ids
        and hypothesis["verification_status"] == "未验证",
    )


def test_l2_change_records_basis() -> None:
    calls = {"count": 0}

    def physics(entity_a: str, entity_b: str, record_id: str) -> dict:
        calls["count"] += 1
        if calls["count"] == 1:
            return {
                "overall_status": "consistent",
                "contradicted_checks": [],
                "physical_checks": [],
                "missing_measurements": [],
                "causal_conclusion": False,
                "written_to_graph": False,
            }
        return {
            "overall_status": "numeric_contradiction",
            "contradicted_checks": ["speed_frequency"],
            "physical_checks": [{"check_id": "speed_frequency", "title": "转速与转频一致性", "status": "contradicted"}],
            "missing_measurements": [],
            "causal_conclusion": False,
            "written_to_graph": False,
        }

    initial = [
        tool("diagnose_fault", diagnosis("轴承磨损", "棒线主轧机电机")),
        tool("get_equipment_relations", relations()),
        tool("detect_anomaly", {"ok": True, "record_id": "REC-TEMP-D", "device_id": "MM-01", "detection_status": "偏离基线"}),
    ]
    loop = run_closed_loop(
        "可能故障是什么？",
        initial,
        execute=scripted({"search_knowledge": lambda arguments: {"ok": True, "query": arguments["query"], "count": 0, "results": []}}),
        physics=physics,
        max_rounds=2,
    )
    hypothesis = hypothesis_named(loop, "轴承磨损")
    history = hypothesis.get("version_history") or []
    check(
        "矛盾的 L2 结果调整状态并记录依据",
        hypothesis["version"] == 2
        and hypothesis["verification_status"] == "被削弱"
        and history
        and history[0]["version"] == 1
        and history[0]["verification_status"] == "未验证"
        and "L2-speed_frequency" in hypothesis["revision_reason"]
        and "转速与转频一致性" in hypothesis["revision_reason"]
        and "numeric_contradiction" in hypothesis["revision_reason"]
        and hypothesis["verification_status"] != "已验证",
    )


def test_negation_keeps_old_version() -> None:
    execute = scripted(
        {
            "search_knowledge": lambda arguments: {"ok": True, "query": arguments["query"], "count": 0, "results": []},
            "analyze_equipment_record": lambda arguments: {
                "ok": True,
                "record_id": arguments["record_id"],
                "device_id": "MM-01",
                "diagnosis_level": "possible_match",
                "confirmed": False,
                "candidate_faults": [{"fault": "润滑不良", "confirmed": False}],
                "detection_status": "偏离基线",
            },
        }
    )
    loop = run_closed_loop(
        "记录 REC-TEMP-E 的可能故障是什么？",
        [tool("diagnose_fault", diagnosis("轴承磨损"))],
        execute=execute,
        max_rounds=3,
    )
    old = hypothesis_named(loop, "轴承磨损")
    new = hypothesis_named(loop, "润滑不良")
    original = loop["rounds"][0]["hypotheses"][0]
    check(
        "原假设被否定后保留旧版本",
        old["verification_status"] == "被证据否定"
        and old["version"] == 2
        and "被否定" in old["revision_reason"]
        and "AN-润滑不良" in old["revision_reason"]
        and old["version_history"][0]["version"] == 1
        and old["version_history"][0]["verification_status"] != "被证据否定"
        and original["version"] == 1
        and original["verification_status"] == "未验证"
        and new["version"] == 1
        and new["revision_reason"].startswith("由新增工具结果形成候选假设")
        and new["verification_status"] != "已验证",
    )


def test_round_limit_is_not_revision_success() -> None:
    execute = scripted({"search_knowledge": lambda arguments: {"ok": True, "query": arguments["query"], "count": 1, "results": [{"document": "临时.md", "locator": "第1行"}]}})
    loop = run_closed_loop("可能故障是什么？", [tool("diagnose_fault", diagnosis("轴承磨损"))], execute=execute, max_rounds=1)
    hypothesis = hypothesis_named(loop, "轴承磨损")
    answer = loop["fault_report"]["public_answer"]
    check(
        "达到循环上限不等于修正成功",
        loop["stop_reason"] == "达到最大推理轮数"
        and execute.seen == []
        and hypothesis["version"] == 1
        and hypothesis["verification_status"] != "已验证"
        and "修正成功" not in answer
        and "修正成功" not in hypothesis["revision_reason"]
        and "达到最大推理轮数不等于诊断成功。" in answer
        and len(loop["rounds"]) == 1,
    )


def expect(name: str, conditions: list[tuple[str, bool]]) -> None:
    failed = [label for label, ok in conditions if not ok]
    check(name, not failed, "；".join(failed))


def original_hypothesis(loop: dict, fault: str) -> dict:
    for item in loop["rounds"][0]["hypotheses"]:
        if item.get("faults") == [fault]:
            return item
    raise AssertionError(fault)


def evidence_matches(items: list[dict], evidence_id: str, source: str, locator: str) -> bool:
    return any(
        item.get("evidence_id") == evidence_id and item.get("source") == source and locator in str(item.get("locator") or "")
        for item in items
    )


def trace_complete(loop: dict) -> bool:
    if not loop["rounds"]:
        return False
    for item in loop["extra_steps"]:
        result = item.get("result")
        if not item.get("tool_name") or "arguments" not in item or "elapsed_ms" not in item or not item.get("status"):
            return False
        if not isinstance(result, dict) or "ok" not in result:
            return False
    for item in loop["rounds"]:
        if not item.get("hypotheses"):
            return False
    return True


def not_promoted(loop: dict, hypothesis: dict) -> bool:
    answer = loop["fault_report"]["public_answer"]
    return (
        hypothesis.get("verification_status") != "已验证"
        and "已经确诊" not in answer
        and "已确诊" not in answer
        and loop["written_to_graph"] is False
        and loop["fault_report"]["written_to_graph"] is False
        and hypothesis.get("written_to_graph") is False
        and loop["causal_conclusion"] is False
    )


def test_scenario_support_keeps_unverified() -> None:
    execute = scripted(
        {
            "search_knowledge": lambda arguments: {
                "ok": True,
                "query": arguments["query"],
                "count": 1,
                "results": [{"document": "KB-02.md", "locator": "第8行"}],
            }
        }
    )
    loop = run_closed_loop("可能故障是什么？", [tool("diagnose_fault", diagnosis("轴承磨损"))], execute=execute, max_rounds=2)
    hypothesis = hypothesis_named(loop, "轴承磨损")
    original = original_hypothesis(loop, "轴承磨损")
    history = hypothesis.get("version_history") or []
    expect(
        "场景A 新证据支持原假设但仍未验证",
        [
            ("原始假设保留", original["faults"] == ["轴承磨损"] and original["version"] == 1 and original["verification_status"] == "未验证"),
            ("版本增加", hypothesis["version"] == 2 and bool(history) and history[0]["version"] == 1),
            ("证据可追溯", evidence_matches(hypothesis["supporting_evidence"], "S1", "search_knowledge", "KB-02.md 第8行")),
            ("理由引用证据", "S1" in hypothesis["revision_reason"] and "KB-02.md 第8行" in hypothesis["revision_reason"] and "检索返回了该资料位置" in hypothesis["revision_reason"]),
            ("停止原因", loop["stop_reason"] == "现有数据无法验证关键条件" and loop["stop_reason"] in loop["fault_report"]["public_answer"]),
            ("轨迹完整", trace_complete(loop) and loop["extra_steps"][0]["tool_name"] == "search_knowledge"),
            ("未确诊也未写图", not_promoted(loop, hypothesis) and hypothesis["verification_status"] == "未验证"),
        ],
    )


def test_scenario_contradiction_keeps_history() -> None:
    execute = scripted(
        {
            "search_knowledge": lambda arguments: {"ok": True, "query": arguments["query"], "count": 0, "results": []},
            "analyze_equipment_record": lambda arguments: {
                "ok": True,
                "record_id": arguments["record_id"],
                "device_id": "MM-01",
                "diagnosis_level": "insufficient_evidence",
                "detection_status": "未发现显著偏离",
                "candidate_faults": [],
            },
        }
    )
    loop = run_closed_loop(
        "记录 REC-TEMP-B 的可能故障是什么？",
        [tool("diagnose_fault", diagnosis("轴承磨损"))],
        execute=execute,
        max_rounds=3,
    )
    hypothesis = hypothesis_named(loop, "轴承磨损")
    original = original_hypothesis(loop, "轴承磨损")
    history = hypothesis.get("version_history") or []
    expect(
        "场景B 矛盾证据削弱原假设并保留历史",
        [
            ("原始假设保留", original["faults"] == ["轴承磨损"] and original["verification_status"] == "未验证"),
            ("历史版本保留", hypothesis["version"] == 2 and bool(history) and history[0]["verification_status"] == "未验证"),
            ("反对证据可追溯", evidence_matches(hypothesis["opposing_evidence"], "AN-STATUS-REC-TEMP-B", "analyze_equipment_record", "REC-TEMP-B")),
            ("理由与证据一致", "未发现显著偏离" in hypothesis["revision_reason"] and "AN-STATUS-REC-TEMP-B" in hypothesis["revision_reason"]),
            ("状态为削弱且故障名未丢", hypothesis["verification_status"] == "被削弱" and hypothesis["faults"] == ["轴承磨损"]),
            ("停止原因", loop["stop_reason"] == "现有数据无法验证关键条件"),
            ("轨迹完整", trace_complete(loop) and [item["tool_name"] for item in loop["extra_steps"]] == ["search_knowledge", "analyze_equipment_record"]),
            ("未确诊也未写图", not_promoted(loop, hypothesis)),
        ],
    )


def test_scenario_l2_change_does_not_confirm_another_fault() -> None:
    calls = {"count": 0}

    def physics(entity_a: str, entity_b: str, record_id: str) -> dict:
        calls["count"] += 1
        if calls["count"] == 1:
            return {
                "overall_status": "consistent",
                "contradicted_checks": [],
                "physical_checks": [],
                "missing_measurements": [],
                "causal_conclusion": False,
                "written_to_graph": False,
            }
        return {
            "overall_status": "numeric_contradiction",
            "contradicted_checks": ["speed_frequency"],
            "physical_checks": [{"check_id": "speed_frequency", "title": "转速与转频一致性", "status": "contradicted"}],
            "missing_measurements": [],
            "causal_conclusion": False,
            "written_to_graph": False,
        }

    initial = [
        tool("diagnose_fault", diagnosis("轴承磨损", "棒线主轧机电机")),
        tool("search_knowledge", {"ok": True, "query": "轴承磨损", "count": 0, "results": []}, {"query": "轴承磨损"}),
        tool("get_equipment_relations", relations()),
        tool("detect_anomaly", {"ok": True, "record_id": "REC-TEMP-C", "device_id": "MM-01", "detection_status": "偏离基线"}),
    ]
    execute = scripted(
        {
            "analyze_equipment_record": lambda arguments: {
                "ok": True,
                "record_id": arguments["record_id"],
                "device_id": "MM-01",
                "diagnosis_level": "insufficient_evidence",
                "detection_status": "偏离基线",
                "candidate_faults": [],
            }
        }
    )
    loop = run_closed_loop("可能故障是什么？", initial, execute=execute, physics=physics, max_rounds=3)
    hypothesis = hypothesis_named(loop, "轴承磨损")
    history = hypothesis.get("version_history") or []
    names = [fault for item in loop["fault_report"]["hypotheses"] for fault in item.get("faults") or []]
    physics_results = (loop["fault_report"].get("physics") or {}).get("results") or []
    expect(
        "场景C L2 由一致变为矛盾但不证明其他故障",
        [
            ("原假设仍在", hypothesis["faults"] == ["轴承磨损"]),
            ("版本和历史", hypothesis["version"] == 2 and bool(history) and history[0]["version"] == 1 and history[0]["verification_status"] == "未验证"),
            ("校验依据", "L2-speed_frequency" in hypothesis["revision_reason"] and "numeric_contradiction" in hypothesis["revision_reason"] and "转速与转频一致性" in hypothesis["revision_reason"]),
            ("反对证据来自 L2", evidence_matches(hypothesis["opposing_evidence"], "L2-speed_frequency", "validate_research_physics", "")),
            ("没有换成其他故障", names == ["轴承磨损"]),
            ("状态只是削弱", hypothesis["verification_status"] == "被削弱"),
            ("停止原因", loop["stop_reason"] == "现有数据无法验证关键条件"),
            ("轨迹完整", trace_complete(loop) and loop["extra_steps"][0]["tool_name"] == "analyze_equipment_record"),
            ("未确诊也未写图", not_promoted(loop, hypothesis) and all(item.get("causal_conclusion") is not True and item.get("written_to_graph") is not True for item in physics_results)),
        ],
    )


def test_scenario_new_candidate_stays_unconfirmed() -> None:
    execute = scripted(
        {
            "search_knowledge": lambda arguments: {"ok": True, "query": arguments["query"], "count": 0, "results": []},
            "analyze_equipment_record": lambda arguments: {
                "ok": True,
                "record_id": arguments["record_id"],
                "device_id": "MM-01",
                "diagnosis_level": "possible_match",
                "confirmed": False,
                "candidate_faults": [{"fault": "润滑不良", "confirmed": False}],
                "detection_status": "偏离基线",
            },
        }
    )
    loop = run_closed_loop(
        "记录 REC-TEMP-D2 的可能故障是什么？",
        [tool("diagnose_fault", diagnosis("轴承磨损"))],
        execute=execute,
        max_rounds=3,
    )
    old = hypothesis_named(loop, "轴承磨损")
    new = hypothesis_named(loop, "润滑不良")
    original = original_hypothesis(loop, "轴承磨损")
    expect(
        "场景D 旧假设被否定后新候选仍未确诊",
        [
            ("原始版本保留", original["version"] == 1 and original["verification_status"] == "未验证" and original["faults"] == ["轴承磨损"]),
            ("旧假设进入历史", old["verification_status"] == "被证据否定" and old["version"] == 2 and old["version_history"][0]["version"] == 1),
            ("否定理由对应新证据", "AN-润滑不良" in old["revision_reason"] and "被否定" in old["revision_reason"]),
            ("新候选有独立证据", evidence_matches(new["supporting_evidence"], "AN-润滑不良", "analyze_equipment_record", "REC-TEMP-D2")),
            ("新候选未确诊", new["version"] == 1 and new["verification_status"] != "已验证" and new["causal_conclusion"] is False),
            ("停止原因", loop["stop_reason"] == "达到最大推理轮数" and "达到最大推理轮数不等于诊断成功。" in loop["fault_report"]["public_answer"]),
            ("轨迹完整", trace_complete(loop)),
            ("未写图", not_promoted(loop, old) and not_promoted(loop, new)),
        ],
    )


def test_scenario_missing_measurements_do_not_swap_fault() -> None:
    execute = scripted(
        {
            "search_knowledge": lambda arguments: {"ok": True, "query": arguments["query"], "count": 0, "results": []},
            "analyze_equipment_record": lambda arguments: {
                "ok": True,
                "record_id": arguments["record_id"],
                "device_id": "MM-01",
                "diagnosis_level": "insufficient_evidence",
                "detection_status": "偏离基线",
                "candidate_faults": [],
                "missing_data": ["相位", "轴向振动", "润滑油压力"],
            },
        }
    )
    loop = run_closed_loop(
        "现有资料不足以区分轴承磨损、转子不平衡或润滑故障。记录 REC-TEMP-M 的可能故障是什么？",
        [tool("diagnose_fault", diagnosis("轴承磨损"))],
        execute=execute,
        max_rounds=3,
    )
    hypothesis = hypothesis_named(loop, "轴承磨损")
    original = original_hypothesis(loop, "轴承磨损")
    names = [fault for item in loop["fault_report"]["hypotheses"] for fault in item.get("faults") or []]
    arguments = " ".join(item[1] for item in execute.seen)
    answer = loop["fault_report"]["public_answer"]
    expect(
        "场景E 测量缺失时不更换故障名称",
        [
            ("原始假设保留", original["faults"] == ["轴承磨损"] and original["version"] == 1),
            ("版本不变", hypothesis["version"] == 1 and hypothesis["faults"] == ["轴承磨损"]),
            ("没有强行换名", names == ["轴承磨损"] and "转子不平衡" not in arguments and "润滑故障" not in arguments),
            ("缺失测量被记录", hypothesis["missing_measurements"] == ["相位", "轴向振动", "润滑油压力"]),
            ("给出后续验证方法", "相位" in hypothesis["verification_method"] and "不能改成另一个故障名称" in hypothesis["verification_method"]),
            ("理由与缺失一致", "相位" in hypothesis["revision_reason"] and "轴向振动" in hypothesis["revision_reason"] and "润滑油压力" in hypothesis["revision_reason"]),
            ("回答列出缺口", "相位" in answer and "验证方法" in answer),
            ("停止原因", loop["stop_reason"] == "现有数据无法验证关键条件" and loop["stop_reason"] in answer),
            ("轨迹完整", trace_complete(loop)),
            ("未确诊也未写图", not_promoted(loop, hypothesis)),
        ],
    )


def test_scenario_duplicate_evidence_stops() -> None:
    same = {"ok": True, "query": "轴承磨损", "count": 1, "results": [{"document": "KB-02.md", "locator": "第8行"}]}
    execute = scripted(
        {
            "search_knowledge": lambda arguments: dict(same, query=arguments["query"]),
            "get_equipment_relations": lambda arguments: {"ok": True, "ambiguous": False, "count": 0, "node": {"name": "棒线主轧机电机"}, "relations": [], "message": "没有找到节点。"},
        }
    )
    loop = run_closed_loop(
        "可能故障是什么？",
        [tool("diagnose_fault", diagnosis("轴承磨损", "棒线主轧机电机")), tool("search_knowledge", dict(same, query="轴承"), {"query": "轴承"})],
        execute=execute,
        max_rounds=3,
    )
    hypothesis = hypothesis_named(loop, "轴承磨损")
    locators = [item.get("locator") for item in hypothesis["supporting_evidence"]]
    expect(
        "场景F 重复证据不升版并按规则停止",
        [
            ("版本不变", hypothesis["version"] == 1 and hypothesis["faults"] == ["轴承磨损"]),
            ("识别重复证据", locators.count("KB-02.md 第8行") == 1 and "没有实质变化" in hypothesis["revision_reason"]),
            ("停止原因说明无法继续", loop["stop_reason"] == "连续两轮没有新增有效证据" and loop["stop_reason"] in loop["fault_report"]["public_answer"]),
            ("调用被记录", [item["tool_name"] for item in loop["extra_steps"]] == ["search_knowledge", "get_equipment_relations"]),
            ("轨迹完整", trace_complete(loop) and len(loop["rounds"]) == 3),
            ("未确诊也未写图", not_promoted(loop, hypothesis)),
        ],
    )


def test_record_level_without_fault_name() -> None:
    calls = []

    def physics(entity_a: str, entity_b: str, record_id: str) -> dict:
        calls.append((entity_a, entity_b, record_id))
        return {
            "overall_status": "no_testable_hypothesis",
            "validation_level": "record",
            "consistent_checks": ["speed_frequency", "xy_vibration"],
            "contradicted_checks": [],
            "insufficient_checks": ["phase_stability"],
            "not_applicable_checks": ["kb02_temperature_threshold"],
            "research_hypotheses": [],
            "missing_measurements": ["相位"],
            "causal_conclusion": False,
            "written_to_graph": False,
        }

    analysis = {
        "ok": True,
        "record_id": "REC-TEMP-R",
        "device_id": "MM-01",
        "diagnosis_level": "diagnostic_clue",
        "confirmed": False,
        "candidate_faults": [],
        "detection_status": "偏离基线",
        "missing_data": ["相位"],
    }
    loop = run_closed_loop(
        "记录 REC-TEMP-R 的可能故障是什么？问题中的轴承磨损不能当成工具结果。",
        [tool("analyze_equipment_record", analysis)],
        execute=scripted({}),
        physics=physics,
        max_rounds=2,
    )
    answer = loop["fault_report"]["public_answer"]
    expect(
        "没有故障假设时执行记录级 L2 且不生成故障名称",
        [
            ("调用记录级校验", calls == [("", "", "REC-TEMP-R"), ("", "", "REC-TEMP-R")]),
            ("没有生成假设", loop["fault_report"]["hypotheses"] == []),
            ("回答区分校验状态", "没有可供验证的故障假设" in answer and "speed_frequency" in answer and "不能生成故障名称" in answer),
            ("不采用问题中的故障名", "轴承磨损" not in answer),
            ("停止且未确诊", loop["stop_reason"] == "现有数据无法验证关键条件" and not_promoted(loop, {"verification_status": "未验证", "written_to_graph": False})),
        ],
    )


def test_same_hypothesis_check_does_not_bump_version() -> None:
    calls = []

    def physics(entity_a: str, entity_b: str, record_id: str) -> dict:
        calls.append((entity_a, entity_b, record_id))
        return {
            "overall_status": "numeric_contradiction",
            "contradicted_checks": ["speed_frequency"],
            "physical_checks": [{"check_id": "speed_frequency", "title": "转速与转频一致性", "status": "contradicted"}],
            "missing_measurements": [],
            "causal_conclusion": False,
            "written_to_graph": False,
        }

    initial = [
        tool("diagnose_fault", diagnosis("轴承磨损", "棒线主轧机电机")),
        tool("search_knowledge", {"ok": True, "query": "轴承磨损", "count": 0, "results": []}, {"query": "轴承磨损"}),
        tool("get_equipment_relations", relations()),
        tool("detect_anomaly", {"ok": True, "record_id": "REC-TEMP-H", "device_id": "MM-01", "detection_status": "偏离基线"}),
        tool(
            "analyze_equipment_record",
            {
                "ok": True,
                "record_id": "REC-TEMP-H",
                "device_id": "MM-01",
                "diagnosis_level": "insufficient_evidence",
                "candidate_faults": [],
                "detection_status": "偏离基线",
            },
        ),
    ]
    loop = run_closed_loop("可能故障是什么？", initial, execute=scripted({}), physics=physics, max_rounds=2)
    hypothesis = hypothesis_named(loop, "轴承磨损")
    expect(
        "重复相同的假设级校验不增加版本",
        [
            ("仍校验该假设", calls and all(item[1] == "轴承磨损" for item in calls) and len(calls) >= 2),
            ("版本保持", hypothesis["version"] == 1 and hypothesis["verification_status"] == "被削弱"),
            ("没有新版本历史", hypothesis.get("version_history") == []),
            ("未确诊也未写图", not_promoted(loop, hypothesis)),
        ],
    )


def clue_analysis(record_id: str, clues: list[dict], *, missing: list[str] | None = None, equipment: str = "") -> dict:
    observation = {"equipment_category": equipment} if equipment else {}
    return {
        "ok": True,
        "record_id": record_id,
        "device_id": "MM-01",
        "diagnosis_level": "diagnostic_clue",
        "confirmed": False,
        "candidate_faults": [],
        "diagnostic_clues": clues,
        "detection_status": "偏离基线",
        "missing_data": missing or [],
        "observation": observation,
    }


def observed_clue() -> dict:
    return {
        "partial_condition_met": True,
        "complete_condition_met": False,
        "document": "KB-02.md",
        "line_number": 8,
        "official_condition": "相位稳定且存在谐波",
        "unverified_reason": "同一条记录满足 X向振动大于 Y向振动。轴向侧偏心没有这项测量，不能确认转子不平衡。",
        "observed_fields": [
            {"field": "X向振动(mm/s)", "value": 3.346},
            {"field": "Y向振动(mm/s)", "value": 3.017},
            {"field": "相位", "value": None},
        ],
    }


def possible_match(fault: str, equipment: str = "") -> dict:
    return {
        "ok": True,
        "tool": "diagnose_fault",
        "confirmed": False,
        "equipment_name": equipment or None,
        "status": "possible_match",
        "matches": [
            {
                "fault": fault,
                "confirmed": False,
                "matched_features": [{"document": "KB-02.md", "locator": "第8行", "description": "只是可能匹配"}],
            }
        ],
    }


def test_clue_triggers_supplement() -> None:
    execute = scripted(
        {
            "diagnose_fault": lambda arguments: {
                "ok": True,
                "confirmed": False,
                "status": "insufficient",
                "matches": [],
                "message": "这些观测特征没有明确对应条目，证据不足，不能诊断。",
            }
        }
    )
    loop = run_closed_loop(
        "记录 REC-TEMP-A 的可能故障是什么？不要采用转子不平衡。",
        [tool("analyze_equipment_record", clue_analysis("REC-TEMP-A", [observed_clue()], missing=["相位", "原始振动波形", "谐波"]))],
        execute=execute,
        max_rounds=2,
    )
    arguments = execute.seen[0][1] if execute.seen else ""
    expect(
        "场景A 有诊断线索且无候选时触发补充诊断",
        [
            ("调用了故障对照", execute.seen and execute.seen[0][0] == "diagnose_fault"),
            ("使用已观测值", "X向振动(mm/s)=3.346" in arguments and "Y向振动(mm/s)=3.017" in arguments),
            ("没有把缺失项当成观测", "相位=" not in arguments and "原始振动波形" not in arguments and "谐波" not in arguments),
            ("没有从问题编造故障", "转子不平衡" not in arguments),
            ("线索本身没有变成假设", loop["fault_report"]["hypotheses"] == []),
        ],
    )


def test_supplement_match_stays_unverified() -> None:
    execute = scripted({"diagnose_fault": lambda arguments: possible_match("轴承磨损")})
    loop = run_closed_loop(
        "记录 REC-TEMP-B 的可能故障是什么？",
        [tool("analyze_equipment_record", clue_analysis("REC-TEMP-B", [observed_clue()]))],
        execute=execute,
        max_rounds=2,
    )
    hypothesis = hypothesis_named(loop, "轴承磨损")
    stored = next(item for item in loop["tool_results"] if item["name"] == "diagnose_fault")
    expect(
        "场景B 补充诊断的候选只形成未验证假设",
        [
            ("状态不是确诊", hypothesis["verification_status"] == "未验证" and hypothesis["causal_conclusion"] is False),
            ("来源可追溯", evidence_matches(hypothesis["supporting_evidence"], "D-轴承磨损", "diagnose_fault", "KB-02.md 第8行")),
            ("工具结果保持未确认", stored["result"]["confirmed"] is False and stored["result"]["status"] == "possible_match"),
            ("未写图", not_promoted(loop, hypothesis)),
        ],
    )


def test_supplement_without_match_has_no_hypothesis() -> None:
    execute = scripted(
        {
            "diagnose_fault": lambda arguments: {
                "ok": True,
                "confirmed": False,
                "status": "insufficient",
                "matches": [],
                "message": "这些观测特征没有明确对应条目，证据不足，不能诊断。",
            }
        }
    )
    loop = run_closed_loop(
        "记录 REC-TEMP-C 的可能故障是什么？",
        [tool("analyze_equipment_record", clue_analysis("REC-TEMP-C", [observed_clue()]))],
        execute=execute,
        max_rounds=2,
    )
    answer = loop["fault_report"]["public_answer"]
    expect(
        "场景C 没有匹配结果时不生成假设",
        [
            ("没有假设", loop["fault_report"]["hypotheses"] == []),
            ("保留无候选原因", "证据不足，不能诊断" in answer),
            ("未写图", loop["written_to_graph"] is False and loop["fault_report"]["written_to_graph"] is False),
        ],
    )


def test_missing_measurement_is_not_observed() -> None:
    execute = scripted({"diagnose_fault": lambda arguments: possible_match("轴承磨损")})
    clue = {
        "partial_condition_met": True,
        "complete_condition_met": False,
        "official_condition": "相位稳定",
        "unverified_reason": "没有相位，不能判断相位稳定。",
        "observed_fields": [{"field": "相位", "value": None}, {"field": "原始振动波形", "value": ""}],
    }
    loop = run_closed_loop(
        "记录 REC-TEMP-D 的可能故障是什么？",
        [tool("analyze_equipment_record", clue_analysis("REC-TEMP-D", [clue], missing=["相位", "原始振动波形"]))],
        execute=execute,
        max_rounds=2,
    )
    expect(
        "场景D 只有缺失测量时不编造诊断输入",
        [
            ("没有调用故障对照", execute.seen == []),
            ("没有生成假设", loop["fault_report"]["hypotheses"] == []),
            ("保留缺失测量", "相位" in loop["fault_report"]["public_answer"]),
        ],
    )


def test_same_supplement_is_not_repeated() -> None:
    execute = scripted(
        {
            "diagnose_fault": lambda arguments: {
                "ok": True,
                "confirmed": False,
                "status": "insufficient",
                "matches": [],
                "message": "这些观测特征没有明确对应条目，证据不足，不能诊断。",
            }
        }
    )
    loop = run_closed_loop(
        "记录 REC-TEMP-E 的可能故障是什么？",
        [tool("analyze_equipment_record", clue_analysis("REC-TEMP-E", [observed_clue()]))],
        execute=execute,
        max_rounds=3,
    )
    expect(
        "场景E 相同补充诊断只调用一次",
        [
            ("只调用一次", [item[0] for item in execute.seen] == ["diagnose_fault"]),
            ("没有重复假设", loop["fault_report"]["hypotheses"] == []),
        ],
    )


def test_record_level_runs_before_candidate() -> None:
    calls = []

    def physics(entity_a: str, entity_b: str, record_id: str) -> dict:
        calls.append((entity_a, entity_b, record_id))
        return {
            "overall_status": "no_testable_hypothesis",
            "consistent_checks": ["speed_frequency"],
            "contradicted_checks": [],
            "insufficient_checks": ["phase_stability"],
            "not_applicable_checks": [],
            "missing_measurements": [],
            "causal_conclusion": False,
            "written_to_graph": False,
        }

    execute = scripted(
        {
            "diagnose_fault": lambda arguments: {
                "ok": True,
                "confirmed": False,
                "status": "insufficient",
                "matches": [],
                "message": "没有明确对应条目，证据不足，不能诊断。",
            }
        }
    )
    loop = run_closed_loop(
        "记录 REC-TEMP-F 的可能故障是什么？",
        [tool("analyze_equipment_record", clue_analysis("REC-TEMP-F", [observed_clue()]))],
        execute=execute,
        physics=physics,
        max_rounds=2,
    )
    expect(
        "场景F 没有候选时仍执行记录级 L2",
        [
            ("记录级调用", bool(calls) and all(item[0] == "" and item[1] == "" and item[2] == "REC-TEMP-F" for item in calls)),
            ("没有假设级故障名", loop["fault_report"]["hypotheses"] == []),
            ("回答保留记录级结果", "没有可供验证的故障假设" in loop["fault_report"]["public_answer"] and "speed_frequency" in loop["fault_report"]["public_answer"]),
        ],
    )


def test_candidate_then_hypothesis_level_l2() -> None:
    calls = []

    def physics(entity_a: str, entity_b: str, record_id: str) -> dict:
        calls.append((entity_a, entity_b, record_id))
        if not entity_b:
            return {
                "overall_status": "no_testable_hypothesis",
                "consistent_checks": ["speed_frequency"],
                "contradicted_checks": [],
                "insufficient_checks": [],
                "not_applicable_checks": [],
                "missing_measurements": [],
                "causal_conclusion": False,
                "written_to_graph": False,
            }
        return {
            "overall_status": "numeric_contradiction",
            "contradicted_checks": ["speed_frequency"],
            "physical_checks": [{"check_id": "speed_frequency", "title": "转速与转频一致性", "status": "contradicted"}],
            "missing_measurements": [],
            "causal_conclusion": False,
            "written_to_graph": False,
        }

    execute = scripted(
        {
            "diagnose_fault": lambda arguments: possible_match("轴承磨损", arguments.get("equipment_name") or ""),
            "search_knowledge": lambda arguments: {"ok": True, "query": arguments["query"], "count": 0, "results": []},
        }
    )
    loop = run_closed_loop(
        "记录 REC-TEMP-G 的可能故障是什么？",
        [
            tool("analyze_equipment_record", clue_analysis("REC-TEMP-G", [observed_clue()], equipment="棒线主轧机电机")),
            tool("get_equipment_relations", relations()),
        ],
        execute=execute,
        physics=physics,
        max_rounds=3,
    )
    hypothesis = hypothesis_named(loop, "轴承磨损")
    expect(
        "场景G 获得候选后执行假设级 L2 且不确诊",
        [
            ("先有记录级", ("", "", "REC-TEMP-G") in calls),
            ("后有假设级", ("棒线主轧机电机", "轴承磨损", "REC-TEMP-G") in calls),
            ("矛盾后只是削弱", hypothesis["verification_status"] == "被削弱" and "speed_frequency" in " ".join(item.get("evidence_id") or "" for item in hypothesis["opposing_evidence"])),
            ("不是确诊", hypothesis["verification_status"] != "已验证" and not_promoted(loop, hypothesis)),
        ],
    )


def test_supplement_keeps_old_hypothesis() -> None:
    execute = scripted(
        {
            "search_knowledge": lambda arguments: {"ok": True, "query": arguments["query"], "count": 0, "results": []},
            "diagnose_fault": lambda arguments: possible_match("润滑不良"),
        }
    )
    loop = run_closed_loop(
        "记录 REC-TEMP-H 的可能故障是什么？",
        [
            tool("diagnose_fault", diagnosis("轴承磨损"), {"observed_features": ["旧特征"]}),
            tool("analyze_equipment_record", clue_analysis("REC-TEMP-H", [observed_clue()])),
        ],
        execute=execute,
        max_rounds=3,
    )
    old = hypothesis_named(loop, "轴承磨损")
    new = hypothesis_named(loop, "润滑不良")
    original = original_hypothesis(loop, "轴承磨损")
    expect(
        "场景H 补充诊断保留旧假设和历史",
        [
            ("原始版本还在", original["version"] == 1 and original["verification_status"] == "未验证"),
            ("旧假设被保留", old["verification_status"] == "被证据否定" and old["version_history"] and old["version_history"][0]["version"] == 1),
            ("新候选未确诊", new["verification_status"] == "未验证" and new["causal_conclusion"] is False),
            ("未写图", not_promoted(loop, old) and not_promoted(loop, new)),
        ],
    )


def test_answer_matches_tool_counts() -> None:
    loop = run_closed_loop(
        "请总结可能故障和设备关系。",
        [tool("diagnose_fault", diagnosis("轴承磨损")), tool("get_equipment_relations", relations())],
        execute=scripted({}),
        model_answer="相邻关系共 9 条，部件共 3 个。",
        max_rounds=1,
    )
    answer = loop["fault_report"]["public_answer"]
    check(
        "最终回答数字与工具结果一致",
        loop["fault_report"]["consistency"]["ok"] is False
        and "相邻关系：4 条" in answer
        and "部件：2 个" in answer
        and "共 9 条" not in answer
        and "共 3 个" not in answer,
    )


def main() -> None:
    before = {path: file_hash(path) for path in PROTECTED}
    test_new_support()
    test_contradiction()
    test_replaced_hypothesis()
    test_no_record_skips_l2()
    test_l2_contradiction_not_confirmed()
    test_names_are_not_causal()
    test_ambiguous_not_selected()
    test_two_empty_rounds_stop()
    test_max_rounds()
    test_failures_are_kept()
    test_trace_rounds()
    test_version_unchanged_without_new_evidence()
    test_duplicate_evidence_does_not_bump_version()
    test_new_evidence_bumps_version_with_id()
    test_l2_change_records_basis()
    test_negation_keeps_old_version()
    test_round_limit_is_not_revision_success()
    test_scenario_support_keeps_unverified()
    test_scenario_contradiction_keeps_history()
    test_scenario_l2_change_does_not_confirm_another_fault()
    test_scenario_new_candidate_stays_unconfirmed()
    test_scenario_missing_measurements_do_not_swap_fault()
    test_scenario_duplicate_evidence_stops()
    test_record_level_without_fault_name()
    test_same_hypothesis_check_does_not_bump_version()
    test_clue_triggers_supplement()
    test_supplement_match_stays_unverified()
    test_supplement_without_match_has_no_hypothesis()
    test_missing_measurement_is_not_observed()
    test_same_supplement_is_not_repeated()
    test_record_level_runs_before_candidate()
    test_candidate_then_hypothesis_level_l2()
    test_supplement_keeps_old_hypothesis()
    test_answer_matches_tool_counts()
    after = {path: file_hash(path) for path in PROTECTED}
    check("正式图谱和监测库未被修改", before == after and not (ROOT / "data" / "processed" / "graph" / "graph.json").read_text(encoding="utf-8").startswith("改写"))
    passed = sum(RESULTS)
    failed = len(RESULTS) - passed
    print(f"RESULT passed={passed} failed={failed}")
    raise SystemExit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
