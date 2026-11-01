import { useState } from "react"
import { DiagnosisPage } from "./events/DiagnosisPage"
import { HealthPage } from "./health/HealthPage"
import { EventsPage } from "./events/EventsPage"
import { EquipmentMonitoringPage } from "./pages/EquipmentMonitoringPage"
import { KnowledgeGraphPage } from "./pages/KnowledgeGraphPage"
import { PortalPage } from "./pages/PortalPage"
import { RouteProvider, useRoute, type PageId } from "./routing"

const NAV_GROUPS: { label: string; items: { label: string; icon: string; page?: PageId }[] }[] = [
  {
    label: "运行监测",
    items: [
      { label: "产线总览", icon: "overview", page: "portal" },
      { label: "设备运行监测", icon: "monitor", page: "monitor" },
      { label: "设备健康中心", icon: "health", page: "health" },
      { label: "事件与日志", icon: "log", page: "events" },
    ],
  },
  {
    label: "智能分析",
    items: [
      { label: "智能诊断工作台", icon: "workbench", page: "diagnosis" },
      { label: "知识图谱", icon: "graph", page: "graph" },
      { label: "报告中心", icon: "report" },
    ],
  },
  {
    label: "系统管理",
    items: [
      { label: "设备与数据管理", icon: "data" },
      { label: "系统设置", icon: "settings" },
    ],
  },
]

function NavIcon({ name }: { name: string }) {
  const pen = {
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.5,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  }
  return (
    <svg className="nav-icon" viewBox="0 0 16 16" aria-hidden="true">
      {name === "brand" ? (
        <>
          <rect {...pen} x="2" y="2" width="12" height="12" rx="2.5" />
          <path {...pen} d="M4.5 10.5 6.7 8.2l1.6 1.4L11.6 5.5" />
        </>
      ) : null}
      {name === "overview" ? (
        <>
          <rect {...pen} x="2.2" y="2.2" width="4.6" height="4.6" rx="1" />
          <rect {...pen} x="9.2" y="2.2" width="4.6" height="4.6" rx="1" />
          <rect {...pen} x="2.2" y="9.2" width="4.6" height="4.6" rx="1" />
          <rect {...pen} x="9.2" y="9.2" width="4.6" height="4.6" rx="1" />
        </>
      ) : null}
      {name === "monitor" ? <path {...pen} d="M1.5 8h2.8l1.4-3.2 2.2 6.4L9.2 8H14.5" /> : null}
      {name === "health" ? <path {...pen} d="M8 13.2S3.2 10 3.2 6.8A2.6 2.6 0 0 1 8 5.4a2.6 2.6 0 0 1 4.8 1.4C12.8 10 8 13.2 8 13.2Z" /> : null}
      {name === "log" ? (
        <>
          <circle {...pen} cx="8" cy="8" r="5.2" />
          <path {...pen} d="M8 5.1V8l2 1.3" />
        </>
      ) : null}
      {name === "workbench" ? (
        <>
          <path {...pen} d="M8 2.2 9.2 6h3.8L10.2 8.3l1.1 3.7L8 9.8 4.7 12l1.1-3.7L3 6h3.8L8 2.2Z" />
        </>
      ) : null}
      {name === "graph" ? (
        <>
          <circle {...pen} cx="4" cy="4.5" r="1.6" />
          <circle {...pen} cx="12" cy="4.5" r="1.6" />
          <circle {...pen} cx="8" cy="12" r="1.6" />
          <path {...pen} d="M5.3 5.4 6.8 10.6M10.7 5.4 9.2 10.6M5.5 4.5h5" />
        </>
      ) : null}
      {name === "report" ? (
        <>
          <path {...pen} d="M4 2.2h5.2L12.8 5.8V13.8H4V2.2Z" />
          <path {...pen} d="M9.2 2.4V5.8h3.4M6 8.2h4.2M6 10.6h4.2" />
        </>
      ) : null}
      {name === "data" ? (
        <>
          <ellipse {...pen} cx="8" cy="4" rx="4.6" ry="1.7" />
          <path {...pen} d="M3.4 4v4c0 .9 2.1 1.7 4.6 1.7s4.6-.8 4.6-1.7V4" />
          <path {...pen} d="M3.4 8v4c0 .9 2.1 1.7 4.6 1.7s4.6-.8 4.6-1.7V8" />
        </>
      ) : null}
      {name === "settings" ? (
        <>
          <path {...pen} d="M2.4 4.2h11.2M2.4 8h11.2M2.4 11.8h11.2" />
          <circle {...pen} cx="5.4" cy="4.2" r="1.15" />
          <circle {...pen} cx="10.6" cy="8" r="1.15" />
          <circle {...pen} cx="6.8" cy="11.8" r="1.15" />
        </>
      ) : null}
    </svg>
  )
}

export default function App() {
  return (
    <RouteProvider>
      <AppShell />
    </RouteProvider>
  )
}

function AppShell() {
  const { route, navigate } = useRoute()
  const [page, setPage] = useState<PageId>("portal")
  if (page !== route.page) {
    setPage(route.page)
  }

  return (
    <div className="app-shell app-shell-light">
      <aside className="app-rail">
        <button type="button" className="brand" onClick={() => navigate({ page: "portal" })}>
          <NavIcon name="brand" />
          <span className="brand-name">钢铁设备故障预测与诊断</span>
        </button>
        <nav className="nav" aria-label="页面">
          {NAV_GROUPS.map((group) => (
            <div key={group.label} className="nav-section">
              <p className="nav-group">{group.label}</p>
              {group.items.map((item) => (
                <button
                  key={item.label}
                  type="button"
                  className="nav-button"
                  aria-current={item.page && page === item.page ? "page" : undefined}
                  onClick={item.page ? () => navigate({ page: item.page as PageId }) : undefined}
                >
                  <NavIcon name={item.icon} />
                  {item.label}
                </button>
              ))}
            </div>
          ))}
        </nav>
      </aside>
      <div className="app-main">
        {page === "portal" && <PortalPage />}
        {page === "monitor" && <EquipmentMonitoringPage />}
        {page === "graph" && <KnowledgeGraphPage />}
        {page === "events" && <EventsPage />}
        {page === "diagnosis" && <DiagnosisPage />}
        {page === "health" && <HealthPage />}
      </div>
    </div>
  )
}
