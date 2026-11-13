# 场景 Agent 相关项目调研

调研时间：2026-11-13
方法：`git clone --depth 1` 到 `research/`（已加入 .gitignore），读 README、源码与许可证；只读不改，未安装任何第三方依赖，未运行对方代码。

## 1. 许可证矩阵（决定能不能复制代码）

| 仓库 | 许可证 | 结论 |
|---|---|---|
| allenai/Holodeck | Apache-2.0 | 可复用，保留版权与 NOTICE |
| UCSB-AI/LayoutGPT | MIT | 可复用 |
| OrangeSodahub/SceneCraft | MIT | 可复用（与本项目重叠少） |
| 3DSceneAgent/AssetRetrieval3D | Apache-2.0 | 可复用 |
| ahujasid/mcp-for-blender | MIT | 可复用 |
| PolySummit/OptiScene | README 声称 MIT，**仓库未附 LICENSE 文件** | 暂不复用，只参考 |
| threedle/ll3m | Demonstration License：仅非商业学术评估、禁止修改、禁止竞品、禁止再分发 | **不可复用** |
| avraichur96/blender-langgraph-agent | README 写「educational and prototyping」 | **不可复用**（非标准开源许可） |
| ssona09/blender-multiagent | 无 LICENSE | **不可复用**（默认保留所有权利） |

## 2. 检索层

### AssetRetrieval3D（Apache-2.0）

- 架构：FastAPI + Gradio + PostgreSQL/pgvector，文本/图像/跨模态检索。
- `backend/embedding_service.py:89-137`：调用阿里云 DashScope 的 `MultiModalEmbedding`（Qwen3-VL 向量），需要 API Key，按调用计费。
- `backend/embedding_service.py:1-88`：另有本地 SigLIP 路径（torch + transformers）。
- `scripts/04_populate_database.py:116-199`：建表与 `ivfflat` 索引。
- 依赖其自带的中英文 caption（Cap3D）与 Objaverse 结构，我们的数据没有 caption。
- 结论：**需改造复用**。可借鉴「文本向量 / 图像向量双表 + 余弦 top-k」的接口设计；不采用 PostgreSQL，改用 SQLite BLOB + numpy（10 万条规模无需向量库）；模型选本地 SigLIP，避免外部 API 与费用。

### Holodeck 的检索（Apache-2.0）

- `ai2holodeck/generation/objaverse_retriever.py:18-80`：CLIP 图像特征 + SBERT 文本特征，按阈值融合打分。**不需要 caption**，文本直接检索图像。
- `ai2holodeck/generation/prompts.py:55`、`:68`：LLM 先产出「带外观描述与数量的物体清单」，再交给检索器——比直接检索更准。
- 结论：**改造后可复用**。我们的组件有渲染图 `render`，可直接算 CLIP 图像向量；把它的「LLM 选物 → 检索」两段式流程照搬。

### 结论

检索走「LLM 选物描述 + SigLIP 文本→图像余弦」路线，索引放 SQLite，不引入 pgvector、不调 DashScope。**前置条件：需要 106,094 张渲染图**，当前本地只有 Parquet 与 USDA。

## 3. 布局层

### LayoutGPT（MIT）

- `run_layoutgpt_3d.py:165-204`：把房间条件、家具类别频率、few-shot 示例拼成 prompt，输出 CSS 风格的 `{length; width; height; left; top; depth; orientation}`。
- `parse_llm_output.py:51-72`：解析上述字符串为结构化布局。
- 结论：**可直接复用 prompt 结构与解析思路**（我们改为输出 JSON，字段映射到 x/y/rot_z）。

### OptiScene（许可证待确认，仅参考）

- `prompt.py:1-47`：约束清单写得很完整——避免重叠、尽量贴墙、平行/垂直墙面、椅子面向桌子、先推理再给坐标；输出 `coordinates[x,y,z]` + `rotate.angle`（弧度，Z 向上，地面 z=0）。
- `default_prompt.txt:1-70`：客厅示例，含沙发、茶几、电视柜、边桌、落地灯及包围盒，可直接作为我们的 few-shot 素材（需自行重写，不复制原文）。
- 结论：**只参考约束与输出格式**；许可证文件缺失，不能复制代码。

### Holodeck 的摆放（Apache-2.0）

- `ai2holodeck/generation/floor_objects.py:1139-1500`：MILP 约束求解摆放，依赖 `gurobipy`（商业许可，`requirements.txt:20`）。
- 同文件 `:491-538` 有非 MILP 的启发式路径（`use_milp=False`）。
- 结论：MILP 路径**不可用**（Gurobi 商业授权）；启发式部分可学习，但不要直接复制依赖链。

### SceneCraft（MIT）

- 面向布局引导的生成式场景，依赖较重的生成模型与 3D-FRONT 类数据，与本项目「复用现成组件」路线不同。
- 结论：**仅参考**，不复用。

## 4. Blender 控制与反馈

### mcp-for-blender（MIT）

- `src/blender_mcp/server.py:216-246`：MCP server 通过 TCP socket 与 Blender GUI 插件通信。
- `src/blender_mcp/server.py:739-780`：暴露给 LLM 的 `execute_blender_code`。
- `src/blender_mcp/safe_mode.py:94-131`：模块白名单，只允许 `bpy/bmesh/mathutils` 与纯计算 stdlib，并明确注释拒绝 numpy（反序列化风险）。**这是本项目最值得移植的一段**。
- `src/blender_mcp/server.py:686-711`：视口截图回传。
- 结论：**需改造复用**。socket + GUI 插件部分不适用（我们无 GUI），但白名单与「执行代码 → 截帧」的工具形态可移植到进程内 bpy。

### ll3m（不可复用代码）

- `README.md:27-29`：多 agent 分工 plan / retrieve / write / debug / refine。
- `blender/headless.py:107-138`：通过 `blender.exe --background --python` 执行，依赖独立 Blender 可执行文件，不适用我们的 `bpy` 模块环境。
- `blender/client.py:142-170`：按是否渲染选择 headless 或 socket 执行。
- 结论：**仅学习「生成 → 执行 → 渲染 → 自查 → 修正」的循环设计**，不能复制代码。

### blender-langgraph-agent（不可复用）

- `src/blender_mcp_client.py:419-456`：`render_preview` 生成 bpy 渲染代码再执行，思路与我们的方案一致。
- 结论：**仅参考工具划分**。

### blender-multiagent（不可复用）

- 三个 agent 分别负责搭建、打光、质检。可借鉴「QA agent 单独看渲染图」的分工。

## 5. 可复用清单（按优先级）

| 优先级 | 来源 | 文件 | 用途 | 许可 |
|---|---|---|---|---|
| P0 | Holodeck | `ai2holodeck/generation/objaverse_retriever.py` | CLIP 图文检索 + 阈值融合 | Apache-2.0 |
| P0 | Holodeck | `ai2holodeck/generation/prompts.py` | LLM 选物 prompt | Apache-2.0 |
| P0 | mcp-for-blender | `src/blender_mcp/safe_mode.py` | LLM 代码执行白名单 | MIT |
| P1 | LayoutGPT | `run_layoutgpt_3d.py`、`parse_llm_output.py` | 布局 prompt 与解析 | MIT |
| P1 | AssetRetrieval3D | `backend/embedding_service.py`、`vector_search.py` | 向量检索接口设计 | Apache-2.0 |
| P2 | OptiScene | `prompt.py`、`default_prompt.txt` | 约束措辞与输出 schema（重写） | 待确认 |
| P2 | Holodeck | `floor_objects.py` 非 MILP 分支 | 摆放启发式（学习） | Apache-2.0 |

## 6. 对设计文档的影响

1. **检索层简化**：SQLite BLOB 存向量 + numpy 余弦即可，不引入 pgvector 与 DashScope。
2. **新增前置任务**：下载数据集渲染图（约 10 万张），否则图像检索无法建库。
3. **布局层**：先用 LLM 输出 JSON + AABB 校验；MILP 不引入（Gurobi 商业授权）。
4. **安全**：若允许 LLM 生成 bpy 代码，采用 mcp-for-blender 的白名单策略，并禁止 `numpy`、文件与网络模块。
5. **不引入的依赖**：gurobipy、pgvector、dashscope、torch（若改用 onnxruntime 版 CLIP）。

## 7. 待确认

1. 是否接受「先下载 10 万张渲染图」以启用图像检索（体积待测）。
2. 检索模型：本地 SigLIP/CLIP（ONNX）还是先用关键词兜底。
3. 是否允许移植 MIT/Apache 代码片段（需要保留版权声明与 NOTICE）。
4. OptiScene 许可证是否联系作者确认。
