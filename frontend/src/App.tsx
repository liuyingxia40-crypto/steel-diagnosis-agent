import { useState } from "react"
import { EquipmentMonitoringPage } from "./pages/EquipmentMonitoringPage"
import { KnowledgeGraphPage } from "./pages/KnowledgeGraphPage"
import { VibrationAnalysisPage } from "./pages/VibrationAnalysisPage"

type PageId = "graph" | "vibration" | "monitor"

export default function App() {
  const [page, setPage] = useState<PageId>("monitor")

  return (
    <div className="app-shell">
      <header className="topbar">
        <p className="brand">钢铁设备故障预测与根因诊断智能体</p>
        <nav className="nav" aria-label="页面">
          <button
            type="button"
            className="nav-button"
            aria-current={page === "monitor" ? "page" : undefined}
            onClick={() => setPage("monitor")}
          >
            设备运行监测
          </button>
          <button
            type="button"
            className="nav-button"
            aria-current={page === "graph" ? "page" : undefined}
            onClick={() => setPage("graph")}
          >
            知识图谱
          </button>
        </nav>
      </header>
      {page === "monitor" && <EquipmentMonitoringPage />}
      {page === "graph" && <KnowledgeGraphPage />}
      {/* 振动数据分析页面保留，便于回滚，不放入顶部主导航。 */}
      {page === "vibration" && <VibrationAnalysisPage />}
    </div>
  )
}
