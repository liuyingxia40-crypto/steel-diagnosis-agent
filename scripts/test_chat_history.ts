/** 智能问答前端历史已随页面移除；本脚本仅确认前端聊天代码不再存在，后端能力仍保留。 */
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
const main = readFileSync(resolve(root, "backend/app/main.py"), "utf8")

check("1 聊天历史前端模块已删除", !existsSync(resolve(root, "frontend/src/chat/chatHistoryStore.ts")))
check("2 独立问答页面已删除", !existsSync(resolve(root, "frontend/src/pages/HomePage.tsx")))
check("3 应用不再挂载聊天页", !app.includes("HomePage") && !app.includes("智能问答"))
check("4 后端问答接口仍存在", main.includes('@app.post("/api/ai/chat"'))
check("5 后端未因前端移除而改掉 Supervisor", existsSync(resolve(root, "backend/app/services/supervisor.py")))

if (results.some((item) => !item)) {
  console.log(`${results.filter(Boolean).length}/${results.length} passed`)
  process.exit(1)
}
console.log(`${results.length}/${results.length} passed`)
