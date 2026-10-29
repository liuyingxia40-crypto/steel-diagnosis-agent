# 数据目录说明（不随普通源码包自动提供完整内容）

本目录约定了后端通过**相对项目根路径**读取的数据位置。  
GitHub 默认发布**不包含**下列官方与派生数据（见根目录 `.gitignore`）。请向项目负责人申请授权后再按路径放入。

## 目录约定

| 目录 | 典型文件 | 分类 | 源码中的读取约定 |
|------|----------|------|------------------|
| `official/` | `KB-01`～`KB-06` 等 `.docx` | **B 授权官方原件** | `backend/app/main.py`、`agent_tools.py`、`knowledge_document_evidence.py`、`knowledge_file_store.py` 等 |
| `official_dataset/` | `钢铁设备数据集_开发集.xlsx` | **B 授权官方数据集** | `official_dataset.DATASET_FILE`；缺失时报「未找到官方开发集 Excel」 |
| `processed/graph/` | `graph.json`、`knowledge_view.json` | **B 授权派生知识** | `knowledge_graph.GRAPH_FILE`、`knowledge_view.VIEW_FILE` |
| `processed/rag/` | `index.json` | **B 授权检索索引** | `knowledge_search.INDEX_PATH` |
| `processed/markdown/` 等 | 清洗/导出中间结果 | **B/视授权** | 知识文件树与加工流水线 |
| `runtime/` | `*.sqlite3`、日志、上传缓存 | **C 本机运行时** | 监测事件、诊断历史、证据库等；可自动创建 |

## 缺失时的功能影响（依据后端接口）

### `data/processed/graph/graph.json` 缺失

- `/api/knowledge/graph` 无法加载基础图谱（`GraphFileError`：未找到本地知识图谱文件）。
- 依赖图谱的知识导入预演/合并、跨知识研究等能力受影响。
- **不阻止**后端进程启动；前端壳子仍可打开。

### `data/processed/graph/knowledge_view.json` 缺失

- `/api/knowledge/view` 不可用（未找到本地知识展示文件）。

### `data/processed/rag/index.json` 缺失

- `/api/knowledge/search` 返回索引缺失错误。
- 聊天在部分回退路径会调用本地检索（`grounded_fallback` → `search`）；无索引时该路径失败。
- **不阻止**服务启动。

### `data/official_dataset/钢铁设备数据集_开发集.xlsx` 缺失

- `/api/dataset/summary`、`/devices`、`/records`、`/observations/{id}`、`/analysis/{id}` 等数据集接口失败。
- 自动监测、监测事件写入所依赖的官方记录回放不可用。
- 启动自检若开启，相关案例会失败，但 lifespan **仍会继续启动 Web 服务**。

### `data/official/*.docx` 缺失

- 知识原件预览、文档证据核对、来源“原始文档”确认等能力不完整。
- RAG 命中结果可能无法映射到已确认的官方 Word 文件名。

### `data/runtime/` 缺失或为空

- **正常**：首次使用相关功能时由程序在本机创建 SQLite / 日志目录。
- **不要**把他人正式 `monitoring_events.sqlite3` 等库当作开源附件公开发布。

## 分类汇总

- **A 可随源码发布：** 本说明文件；仓库内源码、`testdata/`（合成信号）、配置模板。  
- **B 须负责人授权：** `official/`、`official_dataset/`、`processed/` 下官方派生知识与索引。  
- **C 必须留在本机：** `.env`、正式/个人运行库、联调临时库、备份、日志。

放置授权资料后，目录名与文件名须与上表一致，不要改成随意中文路径。
