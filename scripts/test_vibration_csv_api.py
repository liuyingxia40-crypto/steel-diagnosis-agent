"""CSV 振动接口测试。数据由数学公式生成，是算法测试信号，不是官方设备波形。"""

import hashlib
import math
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.services.vibration_csv import MAX_CSV_BYTES

ROOT = Path(__file__).resolve().parents[1]
CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append(ok)
    print(("PASS" if ok else "FAIL"), name, detail)


def sine_csv(frequency, sampling_rate, count, amplitude=1.0, header="value"):
    lines = [header]
    for index in range(count):
        value = amplitude * math.sin(2 * math.pi * frequency * index / sampling_rate)
        lines.append(f"{value:.12g}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def post_csv(client, content, sampling_rate=1000, column=None, unit=None, content_type="text/csv"):
    params = {}
    if sampling_rate is not None:
        params["sampling_rate"] = sampling_rate
    if column is not None:
        params["column"] = column
    if unit is not None:
        params["unit"] = unit
    return client.post(
        "/api/vibration/analyze",
        params=params,
        content=content,
        headers={"content-type": content_type},
    )


print("信号类型：算法测试信号。不是官方设备波形。")
client = TestClient(app)
response = post_csv(client, sine_csv(50, 1000, 1000), unit="mm/s")
body = response.json()
peak = ((body.get("fft") or {}).get("peaks") or [{}])[0].get("frequency_hz")
resolution = (body.get("fft") or {}).get("frequency_resolution_hz")
print("A_STATUS", response.status_code)
print("A_RMS", body.get("rms"))
print("A_PEAK", peak, "resolution", resolution)
print("A_SOURCE", body.get("data_source"), body.get("sample_count"), body.get("sampling_rate_hz"), body.get("unit"))
print("A_NOTE", body.get("analysis_note"))
check(
    "A 接口返回 50 Hz 正弦分析结果",
    response.status_code == 200
    and abs(body.get("rms") - (1 / math.sqrt(2))) < 1e-4
    and peak is not None
    and abs(peak - 50) <= resolution
    and body.get("data_source") == "uploaded_csv"
    and body.get("sample_count") == 1000
    and body.get("sampling_rate_hz") == 1000
    and body.get("unit") == "mm/s"
    and body.get("conclusions", {}).get("faults") == []
    and "不是故障确诊" in str(body.get("analysis_note")),
    f"rms={body.get('rms')} peak={peak}",
)

bom_csv = b"\xef\xbb\xbf" + b"timestamp,value\n" + b"\n".join(
    f"{index / 1000:.6f},{math.sin(2 * math.pi * 50 * index / 1000):.12g}".encode("ascii")
    for index in range(1000)
) + b"\n"
selected = post_csv(client, bom_csv, column="value")
selected_body = selected.json()
check(
    "B 指定 value 列并处理 BOM",
    selected.status_code == 200
    and selected_body.get("column") == "value"
    and selected_body.get("sample_count") == 1000
    and abs(selected_body.get("rms") - (1 / math.sqrt(2))) < 1e-4,
    f"status={selected.status_code} column={selected_body.get('column')} rms={selected_body.get('rms')}",
)

cases = {
    "空文件": (b"", 1000, None),
    "缺失列": (b"timestamp\n0\n1\n2\n3\n4\n5\n6\n7\n8\n", 1000, "value"),
    "非法数值": (b"value\nabc\n1\n2\n3\n4\n5\n6\n7\n8\n", 1000, None),
    "NaN": (b"value\n0\n1\nNaN\n0\n1\n0\n1\n0\n", 1000, None),
    "重复列名": (b"value,value\n1,2\n1,2\n1,2\n1,2\n1,2\n1,2\n1,2\n1,2\n", 1000, "value"),
    "错误采样频率": (sine_csv(50, 1000, 16), 0, None),
    "缺少采样频率": (sine_csv(50, 1000, 16), None, None),
}
for name, (content, rate, column) in cases.items():
    outcome = post_csv(client, content, sampling_rate=rate, column=column)
    payload = outcome.json()
    print("C", name, outcome.status_code, payload.get("message"))
    check(
        "C 拒绝 " + name,
        outcome.status_code >= 400 and payload.get("success") is False and "rms" not in payload and payload.get("message"),
        payload.get("message"),
    )

oversized = b"value\n" + b"0\n" * 20 + b"x" * (MAX_CSV_BYTES + 1)
large = post_csv(client, oversized)
large_body = large.json()
print("D", large.status_code, large_body.get("message"), "bytes", len(oversized))
check("D 超过 5MB 被拒绝", large.status_code == 413 and "5MB" in str(large_body.get("message")), large_body.get("message"))

search = client.get("/api/knowledge/search", params={"q": "轴承磨损", "top_k": 5})
graph = client.get("/api/knowledge/graph")
paths = {getattr(route, "path", "") for route in app.routes}
search_body = search.json()
graph_body = graph.json()
check("E 检索接口兼容", search.status_code == 200 and search_body.get("count", 0) > 0 and "results" in search_body)
check(
    "E 图谱接口兼容",
    graph.status_code == 200 and len(graph_body.get("nodes") or []) == 252 and len(graph_body.get("edges") or []) == 464,
)
check("E 问答接口仍存在且本次未调用模型", "/api/ai/chat" in paths and "/api/vibration/analyze" in paths)

protected = {
    "backend/app/services/vibration_analysis.py": "07ed9d9508e99890d17571695a65f65e92c87b74205a58fbe2296b22b0a2a8b5",
    "backend/app/services/deepseek.py": "767b4bff9b1b36b095eb616117fde9e7a64bd30a9dfde78b71889d0830a64c0a",
    "backend/app/services/agent_tools.py": "50ca60a3a5206e4bc127cefd6329e8b425cebf378254c63c38c20d95f9e9d2ae",
    "backend/app/services/knowledge_search.py": "9f104ab11995765e6f5b3ff23d37f44cbc7831ec3babbe635646a1006c97e983",
    "backend/app/services/knowledge_graph.py": "cdf4a2b38debca5d433e5e8a8fd69234e7b1e651a4657369152bcb8da4cf7475",
}
for relative, digest in protected.items():
    current = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
    check("未修改 " + relative, current == digest)

algorithm = subprocess.run(
    [sys.executable, str(ROOT / "scripts" / "test_vibration_analysis.py")],
    cwd=ROOT / "backend",
    env={**dict(**{key: value for key, value in __import__("os").environ.items()}), "PYTHONPATH": str(ROOT / "backend"), "PYTHONIOENCODING": "utf-8"},
    capture_output=True,
    text=True,
    encoding="utf-8",
)
print(algorithm.stdout)
if algorithm.returncode != 0:
    print(algorithm.stderr)
check("F 原有 19 项算法测试通过", algorithm.returncode == 0 and "SUMMARY 19 / 19" in algorithm.stdout)

print("SUMMARY", sum(1 for item in CHECKS if item), "/", len(CHECKS))
raise SystemExit(0 if all(CHECKS) else 1)
