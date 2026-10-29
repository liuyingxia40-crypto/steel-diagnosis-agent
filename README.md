# 钢铁设备故障预测与根因诊断智能体

基于官方开发集历史回放与本地知识库的工业设备智能诊断原型：支持知识图谱浏览、知识检索问答、设备监测回放、统计偏离分析，以及在严格证据门槛下的故障确认告警（不是把统计偏离直接当成确诊故障）。

> **重要：** 本仓库默认只发布**源码与说明**。官方 Word/Excel、知识图谱加工结果、正式运行库和 API Key **不会**随普通 GitHub 下载包提供，须由项目负责人另行授权。

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | Vite + React + TypeScript（`@antv/g6` 图谱展示） |
| 后端 | FastAPI + Uvicorn + Pydantic |
| 模型 | DeepSeek（通过 `backend/.env` 中的 `DEEPSEEK_API_KEY` 配置） |
| 数据 | 官方 Excel 开发集、本地 JSON 知识图谱 / RAG 索引、SQLite 运行时库 |

本项目**没有** `docker-compose` 配置，不能使用 `docker compose up` 一键启动。

---

## 目录结构（源码侧）

```text
steel-diagnosis-agent/
├── frontend/          # 前端（npm run dev → 默认 http://localhost:5173）
├── backend/           # 后端（uvicorn → http://127.0.0.1:8000）
│   ├── app/           # FastAPI 应用与服务
│   ├── requirements.txt
│   └── .env.example   # 环境变量模板（可发布）
├── scripts/           # 联调与回归脚本
├── docs/              # 使用说明（含老板操作文档）
├── testdata/          # 合成算法测试信号（非官方真实波形）
├── data/              # 数据目录说明见 data/README.md（多数内容不入库）
└── .gitignore
```

被 `.gitignore` 排除、通常**不会**出现在公开克隆结果中的目录包括：`data/official/`、`data/official_dataset/`、`data/processed/`、`data/runtime/`、`backup/`、`node_modules/`、虚拟环境、SQLite、`.env` 等。

---

## 环境要求

- Python **3.11+**（开发机曾用 3.13）
- Node.js **18+** 与 npm（开发机曾用 Node 24 / npm 11）
- 可访问外网（首次安装依赖）
- 负责人提供的 DeepSeek API Key（仅本地写入 `backend/.env`）
- 负责人授权的数据包（见下文；缺数据时服务仍可启动，但业务功能受限）

---

## 必要环境变量

1. 复制模板：

```powershell
Copy-Item backend\.env.example backend\.env
```

2. 编辑 `backend/.env`，填写：

```text
DEEPSEEK_API_KEY=你的密钥
```

- **不要**把真实密钥写入 README、聊天或 GitHub。
- `backend/.env` 已被 `.gitignore` 忽略；`backend/.env.example` 可以随源码发布。

可选（运维/联调）：

- `STARTUP_TOOL_SELFTEST=0`：关闭启动时本地工具自检（缺官方 Excel 时自检会失败，但**不会阻止** Web 服务启动）。

---

## 启动方式（Windows PowerShell）

在**两个终端**中分别启动（项目根目录）：

**后端：**

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

**前端：**

```powershell
cd frontend
npm install
npm run dev
```

浏览器打开前端地址（一般为 `http://localhost:5173`）。  
前端通过 Vite 把 `/api` 代理到 `http://127.0.0.1:8000`（见 `frontend/vite.config.ts`）。

更面向非开发人员的步骤见：

`docs/老板从GitHub下载并在Cursor中打开项目.md`

---

## 官方数据与正式库获取要求

| 类别 | 路径（源码约定） | 是否随源码发布 | 说明 |
|------|------------------|----------------|------|
| B 授权官方原件 | `data/official/*.docx` | 否 | 知识库原始 Word |
| B 授权官方数据集 | `data/official_dataset/钢铁设备数据集_开发集.xlsx` | 否 | 监测/诊断依赖的开发集 |
| B 授权派生知识 | `data/processed/graph/graph.json` 等 | 否 | 图谱与展示数据 |
| B 授权检索索引 | `data/processed/rag/index.json` | 否 | 知识检索 / 部分问答回退 |
| C 本机密钥 | `backend/.env` | 否 | API Key |
| C 运行时库 | `data/runtime/*.sqlite3` 等 | 否 | 监测事件、诊断历史等，运行中生成 |
| A 可发布 | 源码、`scripts/`、`docs/`、`testdata/`、`.env.example` | 是 | 不含真实密钥与官方资料 |

详细影响与放置方式见 [`data/README.md`](data/README.md)。

**请勿编造官方 Excel 或知识图谱；缺失时请向项目负责人申请授权拷贝。**

---

## 缺数据时大致能做什么

- **仅源码 + 依赖安装：** 可启动前后端页面与 `/api/health`、接口文档（`http://127.0.0.1:8000/docs`）。
- **无官方 Excel：** 设备列表、记录回放、自动监测、记录级诊断分析等数据集接口不可用。
- **无 `graph.json` / `knowledge_view.json`：** 知识图谱与知识展示相关接口不可用。
- **无 `rag/index.json`：** 知识检索及依赖该索引的问答回退不可用。
- **无 `DEEPSEEK_API_KEY`：** 需要大模型的对话/智能体能力不可用；本地规则与工具链不替代密钥。
- **无 `data/runtime` 正式库：** 不影响“从零启动”；相关 SQLite 会在使用过程中于本机生成（不要把正式库当开源附件传播）。

---

## 安全提示

- 发布前确认 `.gitignore` 已忽略 `.env`、官方目录、`data/runtime/`、`backup/`、数据库与依赖目录。
- 推荐使用**私有 GitHub 仓库**；官方资料走线下/授权通道，不要用公开 Issue 附件传播。
- `testdata/` 仅为合成测试信号，不能当作真实故障或官方波形。
