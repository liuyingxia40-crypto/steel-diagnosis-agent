# 钢铁设备故障预测与根因诊断智能体  
# 从 GitHub 下载并在 Cursor 中打开（老板操作说明）

本文面向不熟悉开发的使用者，按编号步骤操作即可。  
**请先向项目负责人索取：GitHub 仓库链接、访问权限，以及启动所需的官方数据授权包。**

> 说明：仅把项目文件夹在 Cursor 里打开，**不等于**系统已经启动。打开项目后，还需要按本文第 7 节分别启动后端和前端。

---

## 1. 登录 GitHub 并进入项目仓库

1. 打开浏览器，访问：https://github.com  
2. 点击右上角 **Sign in**，使用项目负责人提供的 GitHub 账号登录。  
3. 在浏览器地址栏粘贴项目负责人发给您的**仓库链接**，回车进入仓库主页。  
4. 确认页面顶部能看到仓库名称（例如包含 `steel-diagnosis-agent` 字样），且左侧能看到 `Code` 按钮。

### 如果是私有仓库（Private）

1. 私有仓库必须先获得访问权限，否则页面会提示找不到仓库或没有权限。  
2. 请联系项目负责人，请其把您的 GitHub 账号加入该仓库的协作者（Collaborator）或所属组织。  
3. 收到邀请邮件后，先接受邀请，再重新打开仓库链接。  
4. **在未获得权限前，不要尝试用别人的账号登录，也不要公开转发仓库地址。**

---

## 2. 通过 Code → Download ZIP 下载并解压

1. 进入仓库主页后，点击绿色 **Code** 按钮。  
2. 选择 **Download ZIP**，等待浏览器下载完成。  
3. 找到下载的压缩包（文件名通常类似 `steel-diagnosis-agent-main.zip` 或带分支名）。  
4. 右键压缩包 → **解压到…**（或“全部提取”），建议解压到容易找到的位置，例如：  
   - `D:\工作\steel-diagnosis-agent`  
   - 或 `桌面\steel-diagnosis-agent`  
5. 解压后进入文件夹，确认能直接看到 `frontend`、`backend`、`scripts` 等目录（见第 5 节）。  
6. 如果解压后多了一层带 `-main` 后缀的文件夹，请打开**最内层**那个包含 `frontend` 和 `backend` 的目录，那才是项目根目录。

> 注意：ZIP 下载通常**不包含**官方 Word/Excel、正式数据库、知识图谱加工结果和您本人的 API Key。这些需要项目负责人另行授权提供（见第 8 节）。

---

## 3. 私有仓库访问权限（再强调一次）

| 情况 | 您需要做的事 |
|------|----------------|
| 能打开仓库并看到文件列表 | 权限正常，可继续下载 |
| 提示 404 / 没有权限 | 联系负责人开通访问，接受邀请后再试 |
| 只能看到空仓库 | 可能还没发布代码，请询问负责人 |

---

## 4. 在 Cursor 中打开解压后的项目

1. 安装并打开 **Cursor**（若尚未安装，请向项目负责人确认安装方式）。  
2. 菜单栏选择：**File（文件） → Open Folder…（打开文件夹）**。  
3. 选中刚才解压得到的**项目根目录**（能同时看到 `frontend` 和 `backend` 的那一层）。  
4. 点击 **选择文件夹 / Open**。  
5. 打开后，左侧资源管理器应显示项目文件树。

---

## 5. 如何确认打开的是正确目录

在 Cursor 左侧文件树中，应能同时看到以下内容（名称需一致）：

1. `frontend/`（前端，内含 `package.json`、`vite.config.ts`、`src/`）  
2. `backend/`（后端，内含 `app/`、`requirements.txt`、`.env.example`）  
3. `scripts/`（脚本与测试）  
4. `docs/`（说明文档，含本文件）  
5. `.gitignore`  
6. （可选）`testdata/`、`data/` 空壳或授权后放入的数据目录  

**打开错了的典型现象：**

- 只看到 `src`、`package.json`，却没有同级的 `backend` → 您可能打开了 `frontend` 子目录。  
- 只看到 `app`、`requirements.txt`，却没有同级的 `frontend` → 您可能打开了 `backend` 子目录。  
- 文件名带很多乱码且找不到 `frontend` → 可能解压不完整或选错了压缩包。

处理办法：关闭当前窗口，重新用 **File → Open Folder** 选中正确的项目根目录。

---

## 6. “在 Cursor 中打开项目”和“真正启动项目”的区别

| 操作 | 含义 | 浏览器能否使用系统 |
|------|------|-------------------|
| 仅 Open Folder | 只是把代码放进编辑器查看/修改 | **不能** |
| 启动后端 + 前端（第 7 节） | 本地服务真正运行 | **可以**（一般是前端地址） |

简单记：**打开 = 看书；启动 = 把车发动起来。**

---

## 7. 启动前准备与启动步骤

### 7.1 环境要求（请先让项目负责人或 IT 确认已安装）

1. **Python 3.13**（本机开发环境曾使用 3.13.9；建议 3.11+）  
2. **Node.js** 与 **npm**（本机开发环境曾使用 Node v24 / npm 11；建议 Node 18+）  
3. 可访问外网（首次安装依赖需要）  
4. 由负责人提供的 **DeepSeek API Key**（仅本地填写，不要发到微信/邮件群）  
5. 由负责人授权的**数据资料包**（见第 8 节）；没有数据包时，界面可能无法加载设备数据或知识图谱  

本项目**没有**提供 `docker-compose` / `Dockerfile` 一键编排，**不要**执行 `docker compose up`。需要按下面方式分别启动前后端。  
更完整的技术说明见项目根目录 `README.md` 与 `data/README.md`。

### 7.2 配置 API Key（不要写入真实密钥到文档或聊天）

1. 进入 `backend` 文件夹。  
2. 复制模板文件 `.env.example`，改名为 `.env`：  

在 **PowerShell** 中（当前目录为项目根目录）可执行：

```powershell
Copy-Item backend\.env.example backend\.env
```

3. 用记事本或 Cursor 打开 `backend\.env`。  
4. 在 `DEEPSEEK_API_KEY=` 后面填写负责人提供的密钥（**不要加引号**）。  
5. 保存文件。  
6. **不要**把 `backend\.env` 上传到 GitHub、不要截图发群、不要提交到版本库。

模板内容示意（仅结构，不含真实密钥）：

```text
DEEPSEEK_API_KEY=
```

### 7.3 安装并启动后端

打开 Cursor 终端（**Terminal → New Terminal**），确认当前在项目根目录，然后执行：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

成功时，终端会保持运行，并监听 `http://127.0.0.1:8000`。  
**请保持该终端窗口不要关闭。**

> 若本机已有可用的 `backend\.venv`，可跳过 `python -m venv` 与部分安装步骤，直接运行最后一行 `uvicorn` 命令。

### 7.4 安装并启动前端

再新建一个终端窗口（不要占用正在跑后端的那个），在项目根目录执行：

```powershell
cd frontend
npm install
npm run dev
```

成功时，终端会显示本地地址，一般为：

- `http://localhost:5173`

用浏览器打开该地址即可使用界面。

前端已配置把 `/api` 请求代理到 `http://127.0.0.1:8000`（见 `frontend/vite.config.ts`），因此通常**只需打开 5173 页面**，不要只开后端端口当网站用。

### 7.5 启动是否成功的快速判断

1. 后端终端无持续报错，且可在浏览器访问：`http://127.0.0.1:8000/docs`（若接口文档页能打开，说明后端在跑）。  
2. 前端终端显示 Vite 本地地址，浏览器打开 `http://localhost:5173` 能看到系统页面。  
3. 页面请求数据时，若提示缺少官方数据集或知识图谱，属于正常现象——需要按第 8 节放入授权资料后再试。

---

## 8. 官方数据、正式库与运行时文件说明（重要）

普通 GitHub 源码包 / Download ZIP **默认不会**自动提供下列内容（且不应随意公开传播）：

| 路径 | 内容 | 如何获得 |
|------|------|----------|
| `data/official/` | 官方原始 Word 知识库等 | 项目负责人另行授权拷贝 |
| `data/official_dataset/` | 官方开发集 Excel（如 `钢铁设备数据集_开发集.xlsx`） | 项目负责人另行授权拷贝 |
| `data/processed/` | 本地解析结果与知识图谱（如 `graph/graph.json`、`rag/index.json`） | 项目负责人另行授权拷贝 |
| `data/runtime/` | 监测事件库、诊断历史、日志等运行时文件 | 本地运行后自动生成；正式库勿随意拷贝传播 |
| `backend/.env` | 含 API Key 的本地密钥文件 | **自行按模板创建**，负责人只提供 Key 文本，不提供别人的 `.env` 文件 |

请注意：

1. **不要编造测试数据库，也不要假设下载后这些文件已经存在。**  
2. 未放入官方 Excel / 知识图谱时，部分页面与诊断功能将无法完整使用。  
3. 获得授权资料后，请把文件放到上表对应目录，目录名称必须一致。  
4. `testdata/` 中的 CSV 仅为算法测试信号，**不是**官方设备真实波形，不能当作正式故障数据。  
5. 解压源码后若只有 `data/README.md`、看不到 Excel/图谱，属于预期现象，不是下载损坏。

### 8.1 缺数据时：哪些还能用、哪些不能用

下面依据后端实际读取路径归纳（服务一般仍能启动，只是对应功能报错或空白）：

| 缺少的内容 | 仍可尝试 | 会受影响的功能 |
|------------|----------|----------------|
| 仅有源码，无任何授权数据 | 打开 Cursor；安装依赖；启动前后端；打开页面壳子与 `http://127.0.0.1:8000/docs` | 设备监测、诊断、知识图谱、知识检索等业务页 |
| 无 `钢铁设备数据集_开发集.xlsx` | 同上 | 设备列表/记录回放、自动监测、记录分析、监测告警链路 |
| 无 `data/processed/graph/graph.json` | 同上 | 知识图谱接口与依赖图谱的知识功能 |
| 无 `data/processed/graph/knowledge_view.json` | 同上 | 知识展示视图接口 |
| 无 `data/processed/rag/index.json` | 同上 | 知识检索；部分问答回退检索 |
| 无 `data/official/*.docx` | 部分界面可开 | 官方原件预览、文档证据与来源确认不完整 |
| 无 `DEEPSEEK_API_KEY` | 本地页面与部分只读接口 | 需要大模型的对话/智能体诊断 |
| 无 `data/runtime` 旧库 | 正常，可空目录起步 | 无历史监测/诊断记录（新运行会在本机生成库文件） |

---

## 9. 常见问题排查

### 9.1 打开错了目录

- 现象：找不到 `frontend` 与 `backend` 并列。  
- 处理：重新 **File → Open Folder**，选对项目根目录（见第 5 节）。

### 9.2 依赖未安装

- 现象：提示找不到 `uvicorn`、`npm`、`react` 等。  
- 处理：重新执行第 7.3、7.4 节的安装命令；确认用的是 `backend\.venv` 里的 Python。

### 9.3 端口被占用

- 后端默认端口：`8000`  
- 前端默认端口：`5173`  
- 现象：启动时报端口已被占用（Address already in use）。  
- 处理：关闭占用该端口的旧程序/旧终端后重试；或请技术人员协助更换端口（更换后需同步检查前端代理配置，非技术人员建议只关闭旧进程）。

### 9.4 环境变量缺失 / API Key 无效

- 现象：对话或智能体功能提示未配置 DeepSeek API Key，或 Key 无效。  
- 处理：检查是否存在 `backend\.env`，以及 `DEEPSEEK_API_KEY=` 后是否已填写；修改后重启后端。

### 9.5 前后端连接失败

- 现象：页面能打开，但数据加载失败、网络错误或一直转圈。  
- 处理：  
  1. 确认后端终端仍在运行（`8000`）。  
  2. 确认前端是通过 `npm run dev` 打开的 `5173` 地址。  
  3. 不要只用文件方式直接双击 `index.html`。  
  4. 若后端未启动，前端的 `/api` 代理会失败。

### 9.6 缺少官方数据或知识图谱

- 现象：提示找不到 `钢铁设备数据集_开发集.xlsx` 或 `data/processed/graph/graph.json` 等。  
- 处理：向项目负责人申请授权资料，按第 8 节放入对应目录，**不要自行编造数据文件**。

### 9.7 误把密钥或正式数据传到网上

- 若曾把 `backend/.env`、官方 Excel、正式库发到公开处，请立即联系项目负责人处理（更换 API Key、评估数据泄露影响）。

---

## 10. 给老板的最小操作清单（可打印）

1. 向负责人要：仓库链接 + 权限 + API Key + 数据授权包  
2. GitHub 登录 → 打开仓库 → **Code → Download ZIP** → 解压  
3. Cursor → **File → Open Folder** → 选中含 `frontend` 与 `backend` 的根目录  
4. 配置 `backend/.env` 中的 `DEEPSEEK_API_KEY`  
5. 放入授权数据到 `data/official`、`data/official_dataset`、`data/processed`（按负责人说明）  
6. 终端启动后端（`uvicorn`，端口 8000）  
7. 另一终端启动前端（`npm run dev`，端口 5173）  
8. 浏览器打开 `http://localhost:5173`

---

## 附：文档与命令核对说明

- 本文命令依据当前项目实际文件编写：`frontend/package.json`、`frontend/vite.config.ts`、`backend/requirements.txt`、`backend/.env.example`、`backend/app/main.py`。  
- 本机已核对：虚拟环境可导入 `uvicorn`/`fastapi`；前端存在 `npm run dev` 脚本；后端入口为 `app.main:app`；API 代理目标为 `127.0.0.1:8000`；CORS 允许 `http://localhost:5173`。  
- 已确认项目根目录**不存在** `docker-compose.yml` / `docker-compose.yaml`，故说明书中不含 Docker 启动步骤。  
- **完整长时间联调启动**需在放入授权数据并填写有效 API Key 后，由技术人员协助做一次当面验收。
