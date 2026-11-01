/** 确认独立智能问答前端页面已移除，同时后端问答能力仍保留。 */
import { existsSync, readFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const results: boolean[] = []

function check(name: string, passed: boolean, detail = ""): void {
  results.push(passed)
  console.log(`${passed ? "PASS" : "FAIL"} ${name}${detail ? " | " + detail : ""}`)
}

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..")
const app = readFileSync(resolve(root, "frontend/src/App.tsx"), "utf8")
const css = readFileSync(resolve(root, "frontend/src/index.css"), "utf8")
const main = readFileSync(resolve(root, "backend/app/main.py"), "utf8")
const nav = app.slice(app.indexOf("<nav"), app.indexOf("</nav>"))

check("1 顶部导航不再包含智能问答", !nav.includes("智能问答") && !app.includes(">智能问答<"))
check("2 默认进入入口页且监测页仍保留", app.includes('useState<PageId>("portal")') && app.includes("<PortalPage />") && app.includes("<EquipmentMonitoringPage />"))
check("3 知识图谱与监测导航仍在且标题栏无首页按钮", nav.includes("运行监测") && nav.includes("知识图谱") && nav.includes("设备运行监测") && nav.includes("产线总览") && !nav.includes("首页") && nav.split('className="nav-button"').length - 1 === 9)
check("4 HomePage 与聊天存储文件已移除", !existsSync(resolve(root, "frontend/src/pages/HomePage.tsx")) && !existsSync(resolve(root, "frontend/src/chat/chatHistoryStore.ts")))
check("5 App 不再引用 HomePage", !app.includes("HomePage") && !app.includes('"home"'))
check("6 前端样式不再保留聊天侧栏", !css.includes(".chat-sidebar") && !css.includes(".chat-workspace") && !css.includes(".chat-bubble"))
check("7 后端 /api/ai/chat 仍保留", main.includes('@app.post("/api/ai/chat"') && main.includes("ask_with_tools"))
check("8 Supervisor 与 DeepSeek 服务文件仍在", existsSync(resolve(root, "backend/app/services/supervisor.py")) && existsSync(resolve(root, "backend/app/services/deepseek.py")))

if (results.some((item) => !item)) {
  console.log(`${results.filter(Boolean).length}/${results.length} passed`)
  process.exit(1)
}
console.log(`${results.length}/${results.length} passed`)
