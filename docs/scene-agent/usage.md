# 场景 Agent 使用说明

更新时间：2026-11-13

## 1. 它能做什么

一句话描述房间 → 从 sand3d 数据集（106,094 个程序化组件）检索 → LLM 出布局 → 无头 bpy 拼装 →
Cycles 渲染 → 渲染图回喂多模态模型迭代 → 导出 `scene.json` / `room.blend` / `room.glb` / `preview_*.png`。

## 2. 前置条件

- Python 3.13 虚拟环境 `.venv`，已装 `bpy 5.2`、`pyarrow`、`requests`（无需安装 Blender GUI）。
- 数据集在 `data/sand3d-datasets/`（29 个 Parquet，约 1.9GB）。
- 根目录 `.env`，DashScope（OpenAI 兼容）配置：

```
AI_PROVIDER_1_BASE_URL=https://coding.dashscope.aliyuncs.com/v1
AI_PROVIDER_1_API_KEY=sk-***
AI_MODEL_1_1_NAME=qwen3.7-plus
```

## 3. 第一次使用：建索引

```powershell
$env:PYTHONPATH="src"; $env:SAND3D_DATA="data\sand3d-datasets"
.venv\Scripts\python -m tools.scene_agent.index
```

- 产物：`out/scene/index.db`（约 917MB，含程序全文与 FTS5 索引），10 万行约 1 分钟。
- 幂等：重复运行按 id 覆盖；`--reset` 先清空再建（重建缓存保留）。
- `--datasets cad,objaverse` 只索引部分数据集；`--limit 5000` 只写前 5000 行（调试）。

不设 `SAND3D_DATA` 时会优先用 H 盘云端硬盘，逐行读 Parquet 会慢一个数量级。

## 3.5 CLIP 图像向量（可选）

依赖 torch + open_clip_torch + Pillow（已装在 `.venv`）。用 Parquet 内嵌缩略图给每个组件建向量：

```powershell
$env:PYTHONPATH="src"; $env:SAND3D_DATA="data\sand3d-datasets"
.venv\Scripts\python -m tools.scene_agent.clipidx build
.venv\Scripts\python -m tools.scene_agent.clipidx stats
.venv\Scripts\python -m tools.scene_agent.clipidx query "a modern fabric sofa"
```

- 向量表 `clip_vectors(id, dim, vec)` 存在 `index.db` 里，float16，10.6 万条约 220MB。
- `build` 幂等：已编码的 id 默认跳过；`--datasets`/`--limit` 可分批。
- 有 CUDA 时自动用 GPU，否则退回 CPU（CPU 建全库约 1~2 小时）。

## 4. 生成场景

```powershell
$env:PYTHONPATH="src"
.venv\Scripts\python -m tools.scene_agent.cli "布置一个 4x5 米的客厅，有沙发、茶几和落地灯"
```

参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--rounds` | 3 | 看图迭代轮数上限（每轮最多 1 张图） |
| `--width` | 768 | 渲染分辨率 |
| `--samples` | 32 | Cycles 采样数（CPU） |
| `--out` | `out/scene/scenes` | 产物根目录 |
| `--clip` | 关 | 用 CLIP 向量对关键词候选重排（需先建向量库） |
| `--seed` | 随机 | LLM 采样种子；显式指定便于复现同一布局，未指定时随机并写入 `scene.json.meta.seed` |

产物目录 `out/scene/scenes/<时间戳>/`：`scene.json`、`room.blend`、`room.glb`、`preview_N.png`，
每轮构建还会写 `live.blend`（供 GUI 联动）。

`scene.json` 里每个组件带 `component_id`、`name`、`family`、`license`、`bbox`、坐标与旋转，
以及 `issues`（校验结果）和 `review_history`（每轮视觉评审）。

## 5. 网页 UI（推荐测试用）

```powershell
$env:PYTHONPATH="src"
.venv\Scripts\python -m tools.scene_agent.server --port 8787
```

浏览器打开 `http://127.0.0.1:8787`，页面能力：

| 区域 | 功能 |
|---|---|
| 左侧 | 输入场景描述、选轮数/分辨率/采样/种子、是否用 CLIP 重排 |
| 日志 | 实时显示检索、重建、渲染、评审过程 |
| 已有场景 | 点任意一条载入历史场景 |
| 渲染预览 | 当前轮次的 PNG |
| 3D 视图 | `room.glb` 在线旋转（依赖 model-viewer CDN，失败可下载后本地看） |
| 组件表 | 改 X/Y/旋转/缩放后点「应用修改并重渲染」 |
| 历史描述 | 下拉选以往描述回填输入框；每次生成自动记录，重复内容不重复入库，最多保留 50 条 |
| 环境 | 墙色、地色取色器 + 光照强度滑杆，随生成/微调一起保存到 `scene.json.env` |
| 再评审一轮 | 用当前坐标重渲染并让多模态模型评审、应用修改 |
| 原样重渲染 | 只读 `scene.json` 纯本地重建，不调 LLM；同参数下渲染结果与上次逐像素一致 |

默认环境：淡灰墙 `#d0d0ce`、棕色地板 `#6f4a2e`、布光强度 600（见 `builder.DEFAULT_ENV`）。
渲染走 AgX 色彩变换，实际画面会比色值偏亮，可下调光照滑杆。

`seed` 会随每次 LLM 请求（解析、选型、布局、评审）提交，并归档到 `scene.json.meta.seed`；
同一个 seed 通常能复现同一套组件与坐标，但服务端采样只保证「尽力一致」。
需要严格逐像素复现时，用「原样重渲染」（`/api/reproduce`）直接重放已归档的布局。

界面固定单屏（不出现页面滚动），默认深色主题，右上角「浅色/深色」切换会记在浏览器本地。

实现方式：仅标准库 `http.server` + 单页 HTML；重活（bpy）交给 `jobs.py` 子进程，
同一时间只允许一个任务，页面每 1.2 秒轮询日志。单个任务墙钟上限 600 秒，超时自动强杀；
运行中可点「取消」。每个任务的完整日志落在 `out/scene/logs/<job_id>.log`，排查失败先看这里。接口：

- `POST /api/run` `{prompt, rounds, width, samples, clip, env, seed}`（env: `{wall, floor, light}`；seed 可省略或传 null）
- `POST /api/adjust` `{scene, items:[{label, instance, x, y, rot_z, scale}], width, samples}`
- `POST /api/rerun` `{scene, width, samples}`（会调 LLM 评审一轮）
- `POST /api/reproduce` `{scene, width, samples}`（纯本地重建，等价 `jobs rerun --no-review`）
- `POST /api/cancel` 取消当前任务（强杀子进程，日志保留）
- `GET /api/job?id=`、`GET /api/scenes`、`GET /api/prompts`、`GET /files/<scene>/<file>`

## 5.1 单独使用各模块

```powershell
# 只检索
.venv\Scripts\python -m tools.scene_agent.search 沙发

# 只重建（会写缓存并打印包围盒）
.venv\Scripts\python -m tools.scene_agent.rebuild --id objaverse-9f265cc16f8a4d459fd79e777d939fd1

# 验收某个场景，或检查全部场景
.venv\Scripts\python -m tools.scene_agent.verify out\scene\scenes\<id>
.venv\Scripts\python -m tools.scene_agent.verify --all
```

## 5.5 Blender GUI 视口联动

需要本机 Blender（已用 winget 装 5.3）。先开始生成，另开一个终端：

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.3\blender.exe" --factory-startup `
  --python tools\scene_agent\blender_live.py -- "out\scene\scenes\<id>"
```

- 不传目录时自动盯 `out/scene/scenes` 下最新的场景。
- 每 1 秒检查一次 `live.blend`（没有则用 `room.blend`），文件一变就重载视口。
- 重载会丢弃场景内未保存的临时修改，这是联动的预期行为。

## 6. 协议与约定

- 坐标：房间左下角为原点，X 向右、Y 向上（俯视），单位毫米，角度为度，地面 z=0。
- 组件正面统一假设 +Y；靠 y=0 墙 rot_z=0，靠 y=d 墙 rot_z=180，靠 x=0 墙 rot_z=90，靠 x=w 墙 rot_z=270。
- 组件归一化：包围盒 XY 居中、最低点落地。
- 摆位：每个组件挂在一个枢轴空物体上，枢轴原点位于包围盒底面中心，平移/旋转/缩放都作用于枢轴，
  因此旋转围绕自身中心，不会甩出房间或陷入地面。
- 支撑关系：布局条目可带 `"on": "书桌#1"`，本地把它的底面高度设为支撑物顶面（`z`），
  只允许一层；离开支撑物 XY 范围时自动失效并落地。
- 冲突判定：XY 平面 AABB 相交超过 5mm **且** Z 区间也相交才算穿插；叠放（如电脑在桌上）合法。
- 兜底：LLM 布局两次仍越界/重叠时，改用「按面积沿墙排序摆放」的确定性布局。
- 评审保护：视觉评审给出的坐标若把原本合法的布局改成越界/重叠，动作整体回滚，场景保持上一版。

## 7. 已知限制

1. 检索默认是关键词 + 中文别名表；加 `--clip` 时用 CLIP 对候选重排，仍不支持纯语义的整库搜索（可用 `clipidx query` 单独试）。
2. 组件朝向无法自动判别，靠视觉评审纠正，可能反复。
3. 尺寸合理性只做粗筛（50~4000mm），同名词条仍可能选错型号，由 LLM 在候选里挑。
4. 布局坐标由 LLM 生成，温度 0.3，同一句话两次运行坐标可能不同（几何重建本身是确定的）。
5. 只有一面地板 + 两面墙，无材质贴图、无阴影开关等美术控制。
6. 每个组件在独立子进程里 `exec` 数据集程序，已做 import 白名单（仅 sand3d/math）与危险调用拦截，
   但仍是第三方代码，只在本地可信环境运行。

## 8. 目录

```
tools/scene_agent/
├── __init__.py    项目路径常量
├── schema.sql     索引库表结构
├── index.py       Parquet -> SQLite + FTS5
├── search.py      中文关键词 -> 组件候选
├── rebuild.py     子进程重建 + 缓存
├── normalize.py   包围盒、落地、居中
├── builder.py     布局校验、夹紧、兜底排布、场景拼装、导出
├── render.py      Cycles CPU 相机与灯光
├── llm.py         DashScope 调用（关思维链）
├── agent.py       主循环
├── clipidx.py     CLIP 图像向量（建库 / 语义检索 / 重排）
├── blender_live.py Blender GUI 视口联动（轮询 live.blend）
├── server.py      本地网页 UI 服务（标准库）
├── jobs.py        UI 后台任务子进程（run / adjust / rerun）
├── web/index.html UI 单页
├── verify.py      产物验收（布局、许可证、分辨率）
└── cli.py         命令行入口
```
