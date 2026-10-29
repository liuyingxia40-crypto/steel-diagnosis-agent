import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["STARTUP_TOOL_SELFTEST"] = "0"
os.environ["MONITORING_EVENTS_DB"] = str(ROOT / "scripts" / "_phase_unused.sqlite3")

from app.services.diagnostic_bridge import analyze_record_for_diagnosis

analysis = analyze_record_for_diagnosis("REC-MA-20260403-00346")
for item in analysis["feature_checks"]:
    if item["feature_code"] == "phase":
        for key in ("related_fault_section", "document", "line_number", "official_condition", "unverified_reason", "method"):
            print(f"{key}: {item.get(key)}")
