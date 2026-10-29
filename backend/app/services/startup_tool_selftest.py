"""后端启动时的本地工具自检。

直接调用 execute_tool，不调用 DeepSeek，也不写入监测事件库。
这不是 DeepSeek 端到端选工具测试。
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from app.services.agent_tools import RELATION_TYPES, TOOLS, execute_tool

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LOG = ROOT / "data" / "runtime" / "logs" / "tool_startup_tests.jsonl"
ANSWER_LABELS = {
    "设备状态",
    "故障类型",
    "故障严重度",
    "故障位置",
    "诊断置信度",
    "频谱描述",
    "根因分析",
    "建议措施",
    "优先级",
    "影响程度",
}
BEARING_FEATURE = "包络谱中出现明显的轴承特征频率（BPFO/BPFI/BSF）及其谐波（2X、3X）"
Execute = Callable[[str, dict], dict]
_STARTED = False


class Case:
    def __init__(self, case_id: str, tool: str, arguments: dict, check: Callable[[dict], list[str]]):
        self.case_id = case_id
        self.tool = tool
        self.arguments = arguments
        self.check = check


def registered_tool_names() -> list[str]:
    names = []
    for item in TOOLS:
        function = item.get("function") if isinstance(item, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        if isinstance(name, str) and name and name not in names:
            names.append(name)
    return names


def selftest_enabled() -> bool:
    value = os.environ.get("STARTUP_TOOL_SELFTEST", "1").strip().lower()
    return value not in {"0", "false", "off", "no"}


def is_reload_parent(argv: list[str] | None = None, process_name: str | None = None) -> bool:
    """uvicorn --reload 的父进程只负责监视文件，不在那里跑自检。"""
    arguments = sys.argv if argv is None else argv
    if "--reload" not in arguments:
        return False
    if process_name is None:
        import multiprocessing

        process_name = multiprocessing.current_process().name
    return process_name == "MainProcess"


def log_path() -> Path:
    override = os.environ.get("STARTUP_TOOL_SELFTEST_LOG", "").strip()
    path = Path(override) if override else DEFAULT_LOG
    resolved = path.resolve()
    blocked = (ROOT / "data" / "official").resolve(), (ROOT / "data" / "official_dataset").resolve()
    if any(resolved == item or item in resolved.parents for item in blocked):
        raise RuntimeError("自检日志不能写入官方资料目录。")
    return path


def _metric(result: dict, field: str) -> dict | None:
    rows = result.get("analyzed_metrics")
    if not isinstance(rows, list):
        rows = ((result.get("statistical_deviation") or {}).get("analyzed_metrics") if isinstance(result.get("statistical_deviation"), dict) else None)
    if not isinstance(rows, list):
        return None
    for item in rows:
        if isinstance(item, dict) and item.get("field") == field:
            return item
    return None


def _require(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def _search_errors(result: dict, query: str, top_k: int) -> list[str]:
    errors = []
    _require(errors, result.get("ok") is True, "ok 不是 true")
    _require(errors, result.get("tool") == "search_knowledge", "工具名不符")
    _require(errors, result.get("query") == query, "返回的查询词与所选案例不一致")
    _require(errors, result.get("top_k") == top_k, "top_k 与所选案例不一致")
    rows = result.get("results")
    _require(errors, isinstance(rows, list), "results 不是列表")
    _require(errors, result.get("count") == (len(rows) if isinstance(rows, list) else None), "count 与结果条数不一致")
    _require(errors, isinstance(rows, list) and len(rows) == top_k, "没有返回请求的条数")
    if isinstance(rows, list):
        seen_query = False
        for row in rows:
            if not isinstance(row, dict):
                errors.append("结果条目不是对象")
                continue
            for key in ("document", "section", "locator", "text"):
                if not str(row.get(key) or "").strip():
                    errors.append(f"结果缺少{key}")
            if query in str(row.get("section") or "") or query in str(row.get("text") or ""):
                seen_query = True
        _require(errors, seen_query, "没有一条结果在章节或正文中出现查询词")
    return errors


def _relation_errors(result: dict, name: str, node_id: str, relation_count: int) -> list[str]:
    errors = []
    node = result.get("node") if isinstance(result.get("node"), dict) else {}
    relations = result.get("relations")
    _require(errors, result.get("ok") is True, "ok 不是 true")
    _require(errors, result.get("tool") == "get_equipment_relations", "工具名不符")
    _require(errors, result.get("match") == "exact", "不是精确匹配")
    _require(errors, result.get("ambiguous") is False, "唯一节点被当成歧义结果")
    _require(errors, node.get("name") == name, "节点名称与查询不一致")
    _require(errors, node.get("id") == node_id, "节点编号与官方图谱不一致")
    _require(errors, isinstance(relations, list), "relations 不是列表")
    _require(errors, result.get("count") == relation_count, "关系数量与官方图谱不一致")
    _require(errors, isinstance(relations, list) and len(relations) == relation_count, "返回的关系条数与 count 不一致")
    if isinstance(relations, list):
        for relation in relations:
            if not isinstance(relation, dict):
                errors.append("关系条目不是对象")
                continue
            kind = relation.get("relation")
            if kind not in RELATION_TYPES:
                errors.append("出现图谱关系类型之外的关系")
            if node_id not in (relation.get("source_id"), relation.get("target_id")):
                errors.append("关系没有连接到所选节点")
            if not relation.get("edge_id"):
                errors.append("关系缺少边编号")
    return errors


def _diagnosis_errors(result: dict, equipment_name: str | None) -> list[str]:
    errors = []
    matches = result.get("matches")
    _require(errors, result.get("ok") is True, "ok 不是 true")
    _require(errors, result.get("tool") == "diagnose_fault", "工具名不符")
    _require(errors, result.get("status") == "possible_match", "匹配等级不是 possible_match")
    _require(errors, result.get("confirmed") is False, "possible_match 被当成了 confirmed")
    _require(errors, result.get("equipment_used_for_matching") is False, "设备名称参与了特征匹配")
    _require(errors, result.get("equipment_name") == equipment_name, "设备名称与案例不一致")
    _require(errors, isinstance(matches, list) and len(matches) >= 1, "没有返回匹配")
    if isinstance(matches, list) and matches:
        first = matches[0] if isinstance(matches[0], dict) else {}
        _require(errors, first.get("fault") == "轴承磨损", "没有匹配到已验证的轴承磨损")
        _require(errors, first.get("confirmed") is False, "故障条目被标成确诊")
        features = first.get("matched_features")
        _require(errors, isinstance(features, list) and len(features) >= 1, "没有特征来源")
        if isinstance(features, list) and features and isinstance(features[0], dict):
            for key in ("document", "section", "locator"):
                _require(errors, bool(str(features[0].get(key) or "").strip()), f"缺少来源字段 {key}")
            _require(errors, features[0].get("document") == "KB-02_故障模式诊断知识库.md", "来源不是 KB-02")
    return errors


def _anomaly_common(result: dict, record_id: str, device_id: str) -> list[str]:
    errors = []
    _require(errors, result.get("ok") is True, "ok 不是 true")
    _require(errors, result.get("tool") == "detect_anomaly", "工具名不符")
    _require(errors, result.get("record_id") == record_id, "记录编号不一致")
    _require(errors, result.get("device_id") == device_id, "设备编号不一致")
    _require(errors, result.get("confirmed_fault") is False, "统计偏离被当成确诊")
    _require(errors, "不是故障确诊" in str(result.get("message") or ""), "返回说明没有保留非确诊界限")
    _require(errors, isinstance(result.get("anomalous_metrics"), list), "anomalous_metrics 不是列表")
    _require(errors, isinstance(result.get("analyzed_metrics"), list), "analyzed_metrics 不是列表")
    return errors


def _anomaly_motor_errors(result: dict) -> list[str]:
    errors = _anomaly_common(result, "REC-MA-20260403-00346", "MM-01")
    _require(errors, result.get("detection_status") == "偏离基线", "统计状态不是偏离基线")
    _require(errors, result.get("history_sample_count") == 80, "历史样本数与已验证记录不一致")
    anomalous = result.get("anomalous_metrics") if isinstance(result.get("anomalous_metrics"), list) else []
    for field in ("Y向振动(mm/s)", "包络谱峰值(g)", "BPFO幅值(g)"):
        _require(errors, field in anomalous, f"缺少已验证的偏离指标 {field}")
    metric = _metric(result, "Y向振动(mm/s)") or {}
    _require(errors, metric.get("current_value") == 3.017, "Y向振动当前值与已验证记录不一致")
    _require(errors, metric.get("metric_status") == "偏离基线", "Y向振动指标状态不一致")
    return errors


def _anomaly_gear_errors(result: dict) -> list[str]:
    errors = _anomaly_common(result, "REC-GE-20260409-00447", "GB-01")
    _require(errors, result.get("detection_status") == "偏离基线", "统计状态不是偏离基线")
    anomalous = result.get("anomalous_metrics") if isinstance(result.get("anomalous_metrics"), list) else []
    _require(errors, set(anomalous) == {"驱动端轴承温度(°C)", "非驱动端轴承温度(°C)"}, "温度偏离指标与已验证记录不一致")
    drive = _metric(result, "驱动端轴承温度(°C)") or {}
    other = _metric(result, "非驱动端轴承温度(°C)") or {}
    _require(errors, drive.get("current_value") in (77, 77.0), "驱动端温度与已验证记录不一致")
    _require(errors, other.get("current_value") == 73.2, "非驱动端温度与已验证记录不一致")
    return errors


def _anomaly_early_errors(result: dict) -> list[str]:
    errors = _anomaly_common(result, "REC-MA-20260314-00001", "MM-01")
    _require(errors, result.get("detection_status") == "基线不足", "最早记录不是基线不足")
    _require(errors, result.get("history_sample_count") == 0, "最早记录的历史样本数不是 0")
    _require(errors, result.get("anomalous_metrics") == [], "基线不足被写成了偏离指标")
    return errors


def _analysis_common(result: dict, record_id: str, device_id: str) -> list[str]:
    errors = []
    _require(errors, result.get("ok") is True, "ok 不是 true")
    _require(errors, result.get("tool") == "analyze_equipment_record", "工具名不符")
    _require(errors, result.get("record_id") == record_id, "记录编号不一致")
    _require(errors, result.get("device_id") == device_id, "设备编号不一致")
    _require(errors, result.get("confirmed") is False, "confirmed 不是 false")
    _require(errors, result.get("confirmed_fault") is False, "记录分析被当成确诊")
    candidates = result.get("candidate_faults") if isinstance(result.get("candidate_faults"), list) else None
    _require(errors, isinstance(candidates, list), "candidate_faults 不是列表")
    if candidates:
        _require(
            errors,
            all(isinstance(item, dict) and item.get("confirmed") is not True for item in candidates),
            "候选故障被标成确诊",
        )
        _require(errors, result.get("diagnose_fault_called") is True, "有候选时未调用 diagnose_fault")
    _require(errors, isinstance(result.get("diagnostic_clues"), list), "diagnostic_clues 不是列表")
    _require(errors, isinstance(result.get("insufficient_reasons"), list), "insufficient_reasons 不是列表")
    # 完整条件在无轴向偏心证据时必须为 false。
    for clue in result.get("diagnostic_clues") or []:
        if not isinstance(clue, dict):
            continue
        if clue.get("feature_code") == "amplitude":
            _require(errors, clue.get("complete_condition_met") is not True, "缺少轴向偏心时 amplitude 被标完整")
        if clue.get("feature_code") == "temperature":
            _require(errors, clue.get("complete_condition_met") is not True, "温度条款被错误标完整")
    return errors


def _analysis_motor_errors(result: dict) -> list[str]:
    errors = _analysis_common(result, "REC-MA-20260403-00346", "MM-01")
    level = result.get("diagnosis_level")
    _require(errors, level in {"diagnostic_clue", "possible_match"}, f"诊断等级异常：{level}")
    _require(errors, result.get("detection_status") == "偏离基线", "统计状态不是偏离基线")
    anomalous = result.get("anomalous_metrics") if isinstance(result.get("anomalous_metrics"), list) else []
    _require(errors, "Y向振动(mm/s)" in anomalous, "缺少已验证的 Y 向振动偏离")
    return errors


def _analysis_gear_errors(result: dict) -> list[str]:
    errors = _analysis_common(result, "REC-GE-20260409-00447", "GB-01")
    _require(errors, result.get("confirmed") is False, "温度记录被确诊")
    _require(errors, result.get("detection_status") == "偏离基线", "统计状态不是偏离基线")
    anomalous = result.get("anomalous_metrics") if isinstance(result.get("anomalous_metrics"), list) else []
    _require(errors, set(anomalous) == {"驱动端轴承温度(°C)", "非驱动端轴承温度(°C)"}, "温度偏离指标与已验证记录不一致")
    return errors


def _analysis_early_errors(result: dict) -> list[str]:
    errors = _analysis_common(result, "REC-MA-20260314-00001", "MM-01")
    level = result.get("diagnosis_level")
    _require(errors, level in {"diagnostic_clue", "possible_match"}, f"基线不足时的诊断等级异常：{level}")
    _require(errors, result.get("detection_status") == "基线不足", "最早记录的统计状态不是基线不足")
    return errors


def case_pools() -> dict[str, list[Case]]:
    return {
        "search_knowledge": [
            Case("search-bearing-wear", "search_knowledge", {"query": "轴承磨损", "top_k": 5}, lambda result: _search_errors(result, "轴承磨损", 5)),
            Case("search-bearing", "search_knowledge", {"query": "轴承", "top_k": 1}, lambda result: _search_errors(result, "轴承", 1)),
        ],
        "get_equipment_relations": [
            Case(
                "graph-mill-motor",
                "get_equipment_relations",
                {"name": "棒线主轧机电机"},
                lambda result: _relation_errors(result, "棒线主轧机电机", "device-f803cb74ec718d83", 30),
            ),
            Case(
                "graph-gearbox",
                "get_equipment_relations",
                {"name": "棒线主减速机/齿轮箱"},
                lambda result: _relation_errors(result, "棒线主减速机/齿轮箱", "device-23b2e75b5fe45e6c", 19),
            ),
            Case(
                "graph-pump",
                "get_equipment_relations",
                {"name": "棒线冷却水循环泵"},
                lambda result: _relation_errors(result, "棒线冷却水循环泵", "device-7c5990a651f517cd", 29),
            ),
        ],
        "diagnose_fault": [
            Case(
                "diagnose-bearing-phrase",
                "diagnose_fault",
                {"observed_features": [BEARING_FEATURE]},
                lambda result: _diagnosis_errors(result, None),
            ),
            Case(
                "diagnose-bearing-phrase-with-equipment",
                "diagnose_fault",
                {"observed_features": [BEARING_FEATURE], "equipment_name": "棒线主轧机电机"},
                lambda result: _diagnosis_errors(result, "棒线主轧机电机"),
            ),
        ],
        "detect_anomaly": [
            Case("anomaly-mm01-deviation", "detect_anomaly", {"record_id": "REC-MA-20260403-00346"}, _anomaly_motor_errors),
            Case("anomaly-gb01-temperature", "detect_anomaly", {"record_id": "REC-GE-20260409-00447"}, _anomaly_gear_errors),
            Case("anomaly-mm01-early", "detect_anomaly", {"record_id": "REC-MA-20260314-00001"}, _anomaly_early_errors),
        ],
        "analyze_equipment_record": [
            Case("analysis-mm01-deviation", "analyze_equipment_record", {"record_id": "REC-MA-20260403-00346"}, _analysis_motor_errors),
            Case("analysis-gb01-temperature", "analyze_equipment_record", {"record_id": "REC-GE-20260409-00447"}, _analysis_gear_errors),
            Case("analysis-mm01-early", "analyze_equipment_record", {"record_id": "REC-MA-20260314-00001"}, _analysis_early_errors),
        ],
    }


def choose_cases(seed: int, pools: dict[str, list[Case]] | None = None, tools: list[str] | None = None) -> list[dict]:
    """每个已注册工具都抽取一条。案例池只有一条时固定使用，并标明。"""
    selected_pools = case_pools() if pools is None else pools
    names = registered_tool_names() if tools is None else list(tools)
    picker = random.Random(seed)
    chosen = []
    for name in names:
        pool = selected_pools.get(name) or []
        if not pool:
            chosen.append({"tool": name, "configured": False, "fixed_case": False, "case": None})
            continue
        case = pool[0] if len(pool) == 1 else picker.choice(pool)
        chosen.append({"tool": name, "configured": True, "fixed_case": len(pool) == 1, "case": case})
    return chosen


def _safe_arguments(arguments: dict) -> dict:
    safe = {}
    for key, value in arguments.items():
        if key.lower() in {"api_key", "authorization", "token", "secret"} or key in ANSWER_LABELS:
            continue
        if isinstance(value, str):
            safe[key] = value[:200]
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            safe[key] = [item[:200] for item in value[:20]]
        elif isinstance(value, (int, float, bool)) or value is None:
            safe[key] = value
    return safe


def _redact(text: str) -> str:
    lowered = text.lower()
    if "api_key" in lowered or "authorization" in lowered or "sk-" in lowered:
        return "内容已省略，因为其中可能包含密钥。"
    for label in ANSWER_LABELS:
        text = text.replace(label, "[结果字段]")
    return text[:300]


def _print_line(message: str) -> None:
    print(f"[SELFTEST] {message}", flush=True)


def _append_log(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()


def run_round(
    *,
    seed: int | None = None,
    log_file: Path | None = None,
    execute: Execute = execute_tool,
    pools: dict[str, list[Case]] | None = None,
    tools: list[str] | None = None,
) -> dict:
    started = datetime.now().astimezone().isoformat(timespec="seconds")
    run_id = uuid.uuid4().hex
    chosen_seed = random.SystemRandom().randrange(1, 2**31) if seed is None else int(seed)
    names = registered_tool_names() if tools is None else list(tools)
    selected = choose_cases(chosen_seed, pools, names)
    _print_line(f"本轮测试开始 run_id={run_id} seed={chosen_seed}")
    results = []
    for item in selected:
        tool_name = item["tool"]
        case = item["case"]
        if case is None:
            _print_line(f"{tool_name} FAIL 耗时=0.000s 原因=尚未配置测试案例")
            results.append(
                {
                    "tool": tool_name,
                    "case_id": None,
                    "fixed_case": False,
                    "configured": False,
                    "arguments": {},
                    "started_at": started,
                    "elapsed_seconds": 0.0,
                    "status": "FAIL",
                    "assertions": [],
                    "failure_reason": "尚未配置测试案例",
                }
            )
            continue
        _print_line(f"{tool_name} 开始 case={case.case_id}")
        case_started = datetime.now().astimezone().isoformat(timespec="seconds")
        clock = time.perf_counter()
        try:
            outcome = execute(tool_name, case.arguments)
            errors = case.check(outcome if isinstance(outcome, dict) else {})
            if not isinstance(outcome, dict):
                errors = ["工具没有返回对象", *errors]
        except Exception as exc:
            errors = [_redact(f"执行异常：{exc}")]
        elapsed = time.perf_counter() - clock
        status = "PASS" if not errors else "FAIL"
        reason = "；".join(errors)
        if status == "PASS":
            _print_line(f"{tool_name} PASS 耗时={elapsed:.3f}s")
        else:
            _print_line(f"{tool_name} FAIL 耗时={elapsed:.3f}s 原因={reason}")
        results.append(
            {
                "tool": tool_name,
                "case_id": case.case_id,
                "fixed_case": item["fixed_case"],
                "configured": True,
                "arguments": _safe_arguments(case.arguments),
                "started_at": case_started,
                "elapsed_seconds": round(elapsed, 3),
                "status": status,
                "assertions": [] if errors else ["返回字段、来源和已验证数据与案例一致"],
                "failure_reason": reason,
            }
        )
    passed = sum(1 for item in results if item["status"] == "PASS")
    failed = len(results) - passed
    covered = sum(1 for item in results if item["configured"])
    unconfigured = [item["tool"] for item in results if not item["configured"]]
    finished = datetime.now().astimezone().isoformat(timespec="seconds")
    _print_line(f"总结：通过{passed}/{len(results)}，失败{failed}，覆盖{covered}/{len(names)}")
    if unconfigured:
        _print_line("尚未配置测试案例的工具：" + "、".join(unconfigured))
        _print_line("未覆盖全部已注册工具，不能视为全工具覆盖成功。")
    record = {
        "run_id": run_id,
        "started_at": started,
        "finished_at": finished,
        "seed": chosen_seed,
        "kind": "local_tool_selftest",
        "note": "这是启动时直接执行 Python 工具的本地自检，不是 DeepSeek 端到端选工具测试。",
        "tool_total": len(names),
        "case_total": len(results),
        "passed": passed,
        "failed": failed,
        "covered_tools": covered,
        "coverage_complete": covered == len(names) and not unconfigured,
        "registered_tools": names,
        "unconfigured_tools": unconfigured,
        "cases": results,
    }
    target = log_file if log_file is not None else log_path()
    try:
        _append_log(target, record)
    except Exception as exc:
        _print_line(f"历史日志写入失败：{_redact(str(exc))} 路径={target}")
    return record


def run_startup_selftest() -> dict | None:
    """供 FastAPI lifespan 调用。同一进程只执行一次，失败也不阻止服务启动。"""
    global _STARTED
    if _STARTED:
        return None
    _STARTED = True
    if not selftest_enabled():
        _print_line("已通过环境变量 STARTUP_TOOL_SELFTEST 关闭")
        return None
    if is_reload_parent():
        _print_line("跳过 uvicorn --reload 文件监视父进程")
        return None
    try:
        return run_round()
    except Exception as exc:
        _print_line(f"自检未能完成，Web 服务继续启动。原因：{_redact(str(exc))}")
        return None


def reset_startup_flag() -> None:
    """只给自动化测试重置本进程的一次性标记。"""
    global _STARTED
    _STARTED = False


if __name__ == "__main__":
    run_startup_selftest()
