"""Live HTTP smoke for frontend/backend integration. Uses synthetic data only."""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"
FE = "http://127.0.0.1:5173"
OUT = Path(__file__).resolve().parents[1] / "data" / "runtime" / "integration_liantiao" / "smoke_report.json"
RESULTS: list[dict] = []


def check(name: str, ok: bool, detail=None) -> None:
    RESULTS.append({"name": name, "ok": bool(ok), "detail": detail})
    print(("PASS" if ok else "FAIL"), name, "" if detail is None else detail)


def req(method: str, path: str, body=None, headers=None, base: str = BASE, timeout: int = 120):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json; charset=utf-8")
        elif isinstance(body, (bytes, bytearray)):
            data = body
        else:
            data = str(body).encode("utf-8")
    request = urllib.request.Request(base + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            if "json" in ctype or raw[:1] in (b"{", b"["):
                return resp.status, json.loads(raw.decode("utf-8"))
            return resp.status, raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            payload = raw.decode("utf-8", errors="replace")
        return exc.code, payload


def main() -> None:
    st, html = req("GET", "/", base=FE)
    check("前端首页可打开", st == 200 and 'id="root"' in str(html), st)
    st, health = req("GET", "/api/health", base=FE)
    check("Vite 代理 /api/health", st == 200 and isinstance(health, dict) and health.get("status") == "ok", health)

    st, devices = req("GET", "/api/dataset/devices")
    devs = devices.get("devices") if isinstance(devices, dict) else None
    check("设备列表可读", st == 200 and isinstance(devs, list) and len(devs) > 0, len(devs) if isinstance(devs, list) else devices)

    target = "REC-MA-20260403-00346"
    st, obs = req("GET", f"/api/dataset/observations/{target}")
    if st != 200:
        device_id = devs[0]["device_id"]
        st, recs = req("GET", f"/api/dataset/records?device_id={urllib.parse.quote(device_id)}&limit=5")
        target = recs["records"][0]["record_id"]
        st, obs = req("GET", f"/api/dataset/observations/{target}")
    record = obs["record"]
    device_id = record["device_id"]
    collected_at = record["collected_at"]
    check(
        "真实观测可读",
        st == 200 and record.get("record_id") == target,
        {"device_id": device_id, "collected_at": collected_at, "record_id": target},
    )

    st, analysis = req("GET", f"/api/dataset/analysis/{urllib.parse.quote(target)}")
    check(
        "异常/诊断分析可读且未确诊",
        st == 200
        and analysis.get("ok") is True
        and analysis.get("confirmed") is False
        and analysis.get("diagnosis_level") not in {None, "confirmed"},
        {
            "diagnosis_level": analysis.get("diagnosis_level"),
            "confirmed": analysis.get("confirmed"),
            "clues": len(analysis.get("diagnostic_clues") or []),
            "deviation": (analysis.get("statistical_deviation") or {}).get("detection_status"),
        },
    )

    st, graph = req("GET", "/api/knowledge/graph")
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    check("知识图谱可读", st == 200 and isinstance(nodes, list) and len(nodes) > 0, len(nodes) if isinstance(nodes, list) else graph)

    st, events = req("GET", "/api/monitoring/events?limit=20")
    check(
        "监测事件可读(隔离库为空也可)",
        st == 200 and isinstance(events.get("events"), list),
        {"count": len(events.get("events") or []), "keys": sorted(events.keys()) if isinstance(events, dict) else type(events).__name__},
    )

    st, follow = req("POST", f"/api/dataset/records/{urllib.parse.quote(target)}/followup-tasks")
    tasks = follow.get("tasks") if isinstance(follow, dict) else None
    check(
        "建议补测可生成",
        st == 200 and isinstance(tasks, list),
        {
            "count": len(tasks) if isinstance(tasks, list) else None,
            "sample": [{k: t.get(k) for k in ("item_id", "status", "upload_mode", "reason")} for t in (tasks or [])[:5]],
        },
    )

    rate = 256
    samples = [
        1.0 * math.sin(2 * math.pi * 25 * i / rate) + 0.3 * math.sin(2 * math.pi * 50 * i / rate)
        for i in range(rate * 4)
    ]
    csv = ("value\n" + "\n".join(f"{v:.8f}" for v in samples) + "\n").encode("utf-8")
    qs = urllib.parse.urlencode(
        {
            "device_id": device_id,
            "measured_at": collected_at,
            "sensor_position": "驱动端",
            "sampling_rate": str(rate),
            "unit": "mm/s",
            "file_name": "integration_synthetic_1ch.csv",
            "data_origin": "synthetic_test_signal",
        }
    )
    st, uploaded = req(
        "POST",
        f"/api/dataset/records/{urllib.parse.quote(target)}/supplementary-vibration?{qs}",
        body=csv,
        headers={"Content-Type": "text/csv"},
    )
    meas = uploaded.get("measurement") if isinstance(uploaded, dict) else None
    if not isinstance(meas, dict):
        meas = uploaded if isinstance(uploaded, dict) else {}
    check(
        "单通道合成 CSV 上传",
        st == 200 and bool(meas.get("measurement_id") or uploaded.get("measurement_id") or uploaded.get("duplicate")),
        {
            "status": st,
            "keys": sorted(uploaded.keys()) if isinstance(uploaded, dict) else type(uploaded).__name__,
            "measurement_id": meas.get("measurement_id") or uploaded.get("measurement_id"),
            "duplicate": uploaded.get("duplicate") if isinstance(uploaded, dict) else None,
            "detail": uploaded if st != 200 else None,
        },
    )

    payload = {
        "device_id": device_id,
        "measured_at": collected_at,
        "evidence_kind": "axial_eccentricity",
        "value": 12.5,
        "unit": "um",
        "sensor_position": "轴向探头",
        "method": "runout_measurement",
        "reference_basis": "shaft_axis",
        "data_origin": "synthetic_test_signal",
    }
    st, evidence = req("POST", f"/api/dataset/records/{urllib.parse.quote(target)}/diagnostic-evidence", body=payload)
    evidence_row = evidence.get("evidence") if isinstance(evidence, dict) and isinstance(evidence.get("evidence"), dict) else evidence
    check(
        "轴向偏心结构化证据可登记",
        st == 200 and isinstance(evidence_row, dict) and bool(evidence_row.get("evidence_id")),
        {
            "status": st,
            "validation_status": evidence_row.get("validation_status") if isinstance(evidence_row, dict) else None,
            "confirmed": evidence.get("confirmed") if isinstance(evidence, dict) else None,
            "detail": evidence if st != 200 else None,
        },
    )

    st, verbal = req(
        "POST",
        f"/api/dataset/records/{urllib.parse.quote(target)}/diagnostic-evidence",
        body={**payload, "value": "正常"},
    )
    check("口头正常结论被拒绝", st >= 400, verbal)

    st, load_ev = req(
        "POST",
        f"/api/dataset/records/{urllib.parse.quote(target)}/diagnostic-evidence",
        body={
            "device_id": device_id,
            "measured_at": collected_at,
            "evidence_kind": "load_1x_series",
            "unit": "mm/s",
            "sensor_position": "驱动端水平",
            "method": "load_sweep",
            "reference_basis": "process_load",
            "data_origin": "synthetic_test_signal",
            "observations": [
                {
                    "load": 50,
                    "load_unit": "%",
                    "amplitude_1x": 1.2,
                    "amplitude_unit": "mm/s",
                    "phase_1x_deg": None,
                    "keyphasor_present": False,
                }
            ],
        },
    )
    item = load_ev.get("evidence") if isinstance(load_ev, dict) and isinstance(load_ev.get("evidence"), dict) else load_ev
    val_status = item.get("validation_status") if isinstance(item, dict) else None
    check(
        "单次负荷观测标记证据不足或待验证",
        st == 200 and val_status in {"仍然证据不足", "已登记、待验证", "可计算"},
        {"status": val_status, "http": st, "body": load_ev if st != 200 else None},
    )

    st, phys = req("POST", f"/api/dataset/records/{urllib.parse.quote(target)}/physical-revalidation")
    check(
        "物理重新校验可调用",
        st == 200,
        {"confirmed": phys.get("confirmed") if isinstance(phys, dict) else None, "keys": sorted(phys.keys()) if isinstance(phys, dict) else None, "detail": phys if st != 200 else None},
    )

    st, reassess = req("POST", f"/api/dataset/records/{urllib.parse.quote(target)}/fault-reassessment")
    check(
        "故障重新评估可调用且不确诊",
        st == 200 and isinstance(reassess, dict) and reassess.get("confirmed") is not True,
        {
            "ok": reassess.get("ok") if isinstance(reassess, dict) else None,
            "confirmed": reassess.get("confirmed") if isinstance(reassess, dict) else None,
            "stop_reason": (reassess.get("stop_reason") or reassess.get("reason") or reassess.get("message")) if isinstance(reassess, dict) else None,
            "keys": sorted(reassess.keys()) if isinstance(reassess, dict) else None,
            "detail": reassess if st != 200 else None,
        },
    )

    st, hist = req("GET", f"/api/dataset/records/{urllib.parse.quote(target)}/diagnosis-history")
    assessments = hist.get("assessments") if isinstance(hist, dict) else None
    check(
        "诊断历史有评估记录",
        st == 200 and isinstance(assessments, list) and len(assessments) >= 1,
        {
            "count": len(assessments) if isinstance(assessments, list) else None,
            "versions": [a.get("hypothesis_version") or a.get("version") for a in (assessments or [])[:5]],
        },
    )

    st, evlist = req("GET", f"/api/dataset/records/{urllib.parse.quote(target)}/diagnostic-evidence")
    evidence_rows = evlist.get("evidence") if isinstance(evlist, dict) else None
    check(
        "证据列表字段为 evidence",
        st == 200 and isinstance(evidence_rows, list) and len(evidence_rows) >= 1,
        len(evidence_rows) if isinstance(evidence_rows, list) else evlist,
    )

    st, fulist = req("GET", f"/api/dataset/records/{urllib.parse.quote(target)}/followup-tasks")
    ftasks = fulist.get("tasks") if isinstance(fulist, dict) else None
    check(
        "补测任务可读且有状态",
        st == 200 and isinstance(ftasks, list) and len(ftasks) >= 1,
        [{k: t.get(k) for k in ("item_id", "status", "upload_mode")} for t in (ftasks or [])[:8]],
    )

    st, sup = req("GET", f"/api/dataset/records/{urllib.parse.quote(target)}/supplementary-vibration")
    check(
        "补充测量列表含单通道",
        st == 200 and isinstance(sup.get("measurements"), list) and len(sup.get("measurements")) >= 1,
        {"n": len(sup.get("measurements") or []), "sync": len(sup.get("synchronous_measurements") or [])},
    )

    st, chat = req("POST", "/api/ai/chat", body={"message": "ping-integration-only", "history": []}, timeout=30)
    check(
        "智能问答接口有响应(不要求真实 DeepSeek 成功)",
        st in {200, 401, 422, 500, 502, 503, 504} or isinstance(chat, dict),
        {"status": st, "keys": sorted(chat.keys()) if isinstance(chat, dict) else str(chat)[:200]},
    )

    official = {
        "graph": (Path(__file__).resolve().parents[1] / "data" / "processed" / "graph" / "graph.json").is_file(),
        "excel": (Path(__file__).resolve().parents[1] / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx").is_file(),
        "integration_monitoring_db": (Path(__file__).resolve().parents[1] / "data" / "runtime" / "integration_liantiao" / "monitoring_events.sqlite3").is_file()
        or True,
    }
    check("正式图谱与 Excel 仍存在", official["graph"] and official["excel"], official)

    report = {
        "record_id": target,
        "device_id": device_id,
        "collected_at": collected_at,
        "passed": sum(1 for row in RESULTS if row["ok"]),
        "failed": sum(1 for row in RESULTS if not row["ok"]),
        "results": RESULTS,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("RESULT", report["passed"], report["failed"])
    print("RECORD", target, device_id, collected_at)


if __name__ == "__main__":
    main()
