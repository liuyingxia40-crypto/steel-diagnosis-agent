"""验收后端启动时的本地工具自检。正式日志和监测事件库不写入测试数据。"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.services import startup_tool_selftest as selftest  # noqa: E402
from app.services.agent_tools import execute_tool  # noqa: E402

CHECKS = []
EXCEL = ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"
EXPECTED_XLSX = "1e9caeba1fe526f3ad1b5dd6445a9dbcc02fa22ec02273d9863ebc84e3e30f87"
SQLITE = ROOT / "data" / "runtime" / "monitoring_events.sqlite3"
PROTECTED = (
    ROOT / "backend" / "app" / "services" / "agent_tools.py",
    ROOT / "backend" / "app" / "services" / "deepseek.py",
    ROOT / "backend" / "app" / "services" / "anomaly_detection.py",
    ROOT / "backend" / "app" / "services" / "diagnostic_bridge.py",
    ROOT / "backend" / "app" / "services" / "official_dataset.py",
    EXCEL,
)
LABELS = (
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
)


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append(bool(ok))
    print(("PASS" if ok else "FAIL"), name, detail)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.update(node.module.split("."))
    return names


def selections(seed: int) -> tuple[str, ...]:
    return tuple(item["case"].case_id for item in selftest.choose_cases(seed) if item["case"] is not None)


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def log_is_clean(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    lowered = text.lower()
    if "api_key" in lowered or "authorization" in lowered or "sk-" in lowered:
        return False
    for row in read_jsonl(path):
        blob = json.dumps(row, ensure_ascii=False)
        for label in LABELS:
            if f'"{label}"' in blob:
                return False
    return True


def failing_execute(name: str, arguments: dict) -> dict:
    if name == "detect_anomaly":
        raise RuntimeError("模拟检测失败")
    return execute_tool(name, arguments)


def start_server(log_path: Path) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["STARTUP_TOOL_SELFTEST"] = "1"
    env["STARTUP_TOOL_SELFTEST_LOG"] = str(log_path)
    env.pop("MONITORING_EVENTS_DB", None)
    return subprocess.Popen(
        [
            str(BACKEND / ".venv" / "Scripts" / "python.exe"),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "18766",
        ],
        cwd=BACKEND,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def main() -> None:
    before = {path: file_hash(path) for path in PROTECTED if path.is_file()}
    sqlite_before = file_hash(SQLITE) if SQLITE.is_file() else None
    banned = imported_modules(ROOT / "backend" / "app" / "services" / "startup_tool_selftest.py") & {
        "deepseek",
        "requests",
        "httpx",
        "urllib",
        "monitoring_events",
    }
    check("自检模块没有导入 DeepSeek、HTTP 或监测事件库", not banned, str(sorted(banned)))
    check("导入模块没有执行自检", selftest._STARTED is False)

    pools = selftest.case_pools()
    check("五个工具都有案例", set(pools) == set(selftest.registered_tool_names()) and all(pools.values()))
    fixed = selftest.choose_cases(1, {"only_tool": [pools["search_knowledge"][0]]}, ["only_tool"])
    check("只有一条案例时标明固定案例", fixed[0]["fixed_case"] is True and fixed[0]["case"].case_id == "search-bearing-wear")
    missing = selftest.choose_cases(1, {}, ["search_knowledge", "missing_tool"])
    check("未配置工具不会被算作已覆盖", missing[1]["configured"] is False)
    check("同一随机种子选择可复现", selections(7) == selections(7) and len(selections(7)) == 5)
    varied = next((seed for seed in range(1, 30) if selections(seed) != selections(seed + 1000)), None)
    check("不同随机种子可以选择不同案例", varied is not None, str(varied))
    check("reload 父进程会被跳过", selftest.is_reload_parent(["uvicorn", "--reload"], "MainProcess") is True)
    check("reload 工作进程不会被跳过", selftest.is_reload_parent(["uvicorn", "--reload"], "SpawnProcess-1") is False)
    check("普通启动不会被跳过", selftest.is_reload_parent(["uvicorn"], "MainProcess") is False)

    temp_dir = Path(os.environ["TEMP"]) / "startup-selftest"
    temp_dir.mkdir(parents=True, exist_ok=True)
    first_log = temp_dir / "round-a.jsonl"
    second_log = temp_dir / "round-b.jsonl"
    append_log = temp_dir / "append.jsonl"
    failure_log = temp_dir / "failure.jsonl"
    server_log = temp_dir / "server.jsonl"
    for path in (first_log, second_log, append_log, failure_log, server_log):
        if path.exists():
            path.unlink()

    first = selftest.run_round(seed=11, log_file=first_log)
    check(
        "第一轮覆盖五个工具并全部通过",
        first["passed"] == 5 and first["failed"] == 0 and first["covered_tools"] == 5 and first["coverage_complete"] is True,
        f"{first['passed']}/{first['case_total']}",
    )
    second = selftest.run_round(seed=11, log_file=second_log)
    check(
        "同一种子的案例编号一致",
        [item["case_id"] for item in first["cases"]] == [item["case_id"] for item in second["cases"]],
    )
    other = selftest.run_round(seed=varied or 2, log_file=temp_dir / "other.jsonl")
    check(
        "不同种子的案例编号发生变化",
        [item["case_id"] for item in first["cases"]] != [item["case_id"] for item in other["cases"]],
    )
    selftest.run_round(seed=3, log_file=append_log)
    selftest.run_round(seed=4, log_file=append_log)
    appended = read_jsonl(append_log)
    check("连续两轮追加且历史不丢失", len(appended) == 2 and appended[0]["run_id"] != appended[1]["run_id"])
    failed = selftest.run_round(seed=5, log_file=failure_log, execute=failing_execute)
    statuses = {item["tool"]: item["status"] for item in failed["cases"]}
    check(
        "一个工具失败后其余工具继续执行",
        statuses.get("detect_anomaly") == "FAIL" and failed["passed"] == 4 and failed["failed"] == 1 and failed["covered_tools"] == 5,
        str(statuses),
    )
    sample_text = first_log.read_text(encoding="utf-8")
    check("日志是逐行 JSON 且不含答案标签或密钥", log_is_clean(first_log) and '"kind":"local_tool_selftest"' in sample_text)
    check("日志说明这不是 DeepSeek 端到端测试", "不是 DeepSeek 端到端选工具测试" in sample_text)

    lines: list[str] = []

    def read_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.append(line)

    process = start_server(server_log)
    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    deadline = time.time() + 90
    while time.time() < deadline and not any("总结：" in line for line in lines):
        if process.poll() is not None:
            break
        time.sleep(0.2)
    output = "".join(lines)
    check("启动进程输出了自检开始和总结", "[SELFTEST] 本轮测试开始" in output and "[SELFTEST] 总结：" in output, output[-800:])
    check("启动日志逐项包含工具名和 PASS", all(f"[SELFTEST] {name} PASS" in output for name in selftest.registered_tool_names()))
    health_codes = []
    for _index in range(2):
        try:
            with urllib.request.urlopen("http://127.0.0.1:18766/api/health", timeout=10) as response:
                health_codes.append(response.status)
        except Exception as exc:
            health_codes.append(str(exc))
    check("启动后的两次 HTTP 请求成功", health_codes == [200, 200], str(health_codes))
    server_rows = read_jsonl(server_log) if server_log.is_file() else []
    check("两次请求没有新增自检轮次", len(server_rows) == 1 and server_rows[0]["passed"] == 5, str(len(server_rows)))
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=15)
    check("验证服务是测试结束后主动停止的", True, f"exit={process.returncode}")

    after = {path: file_hash(path) for path in before}
    sqlite_after = file_hash(SQLITE) if SQLITE.is_file() else None
    check("官方 Excel 哈希不变", after.get(EXCEL) == EXPECTED_XLSX)
    check("业务代码在测试期间没有变化", before == after)
    check("监测事件库没有被自检写入", sqlite_before == sqlite_after)
    official_log = selftest.DEFAULT_LOG
    if official_log.exists():
        recent = time.time() - official_log.stat().st_mtime < 600
        check("本轮测试没有更新正式自检日志", not recent)
    else:
        check("正式自检日志保持未创建", True)

    print(f"{sum(CHECKS)}/{len(CHECKS)} passed")
    if not all(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
