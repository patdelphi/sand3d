# 场景 Agent 任务清单

创建：2026-11-13 10:02
更新：2026-11-13 11:40
约定：`[ ]` 未开始，`[x]` 已完成；每项带完成时间。代码位于 `tools/scene_agent/`，不改 `src/sand3d/`。

## 调研（前置）

- [x] 2026-11-13 浅克隆 9 个候选仓库到 `research/`（已 gitignore）
- [x] 2026-11-13 核对许可证：5 个可复用、1 个待确认、3 个不可复用
- [x] 2026-11-13 输出结论 `Docs/scene-agent/research.md`
- [x] 2026-11-13 拆解 Agent 循环、检索打分、布局协议、安全执行，输出 `Docs/scene-agent/patterns.md`
- [x] 2026-11-13 决定：不下载 106,094 张渲染图（`render` 字段是 Parquet 内嵌缩略图，暂不落盘）
- [x] 2026-11-13 决定：检索先用关键词 + FTS5（中文别名表），CLIP 后置
- [x] 2026-11-13 决定：不移植第三方代码，只按逻辑重写

## M0 索引层

- [x] 2026-11-13 11:08 建 SQLite 表结构 `schema.sql`（components / components_fts / rebuild_cache / scenes）
- [x] 2026-11-13 11:12 写 `index.py`：遍历 29 个 Parquet，写入 components 与 FTS 表
- [x] 2026-11-13 11:12 索引幂等：`INSERT OR REPLACE` + 稳定 rowid，重复运行不产生重复行
- [x] 2026-11-13 11:12 校验：`COUNT(*) = 106094`（实际 106094，库 917MB，用时 56 秒）
- [x] 2026-11-13 11:20 写 `search.py`：中文关键词 → FTS 查询 → 返回 id/family/包围盒
- [x] 2026-11-13 11:20 验证：「沙发」「茶几」「落地灯」「chair」各返回非空且相关

## M1 重建层

- [x] 2026-11-13 10:58 写 `rebuild.py`：`exec(program)` + `entry()`，返回 sand3d 对象
- [x] 2026-11-13 10:58 每次重建放独立子进程，180 秒超时，失败写 rebuild_cache.error
- [x] 2026-11-13 10:58 写 `normalize.py`：mm、Z-up、包围盒落地、XY 居中
- [x] 2026-11-13 10:58 缓存：按 `(id, program_sha256, sand3d_version)` 存 `.glb/.blend` 与 bbox，命中不重建
- [x] 2026-11-13 11:37 抽样 20 个组件重建：成功率 20/20 = 100%，单件 1.2~11.1 秒

## M2 布局层

- [x] 2026-11-13 11:00 写 `builder.py`：布局 JSON → 导入 GLB 拼装 → 导出
- [x] 2026-11-13 11:00 实现 AABB 冲突检测，返回冲突对与重叠面积
- [x] 2026-11-13 11:00 房间边界约束（5mm 容差）+ `clamp` 夹紧，越界自动修正
- [x] 2026-11-13 11:33 三件家具（沙发/茶几/落地灯）完整跑通，导出 `room.blend` 与 `room.glb`
- [x] 2026-11-13 11:33 用渲染图目视确认无浮空、无穿插（无 GUI，改用渲染验证）

## M3 渲染与视觉闭环

- [x] 2026-11-13 11:00 写 `render.py`：Cycles CPU、768×768、32 samples、OIDN 降噪
- [x] 2026-11-13 11:00 相机预设 `iso/front/top`，三点布光 + 环境光
- [x] 2026-11-13 11:33 单次渲染 768px/32samples 耗时 4 秒（640px/16samples 约 2 秒）
- [x] 2026-11-13 11:20 写 `agent.py`：需求解析 → 选型 → 布局 → 构建 → 评审 → 导出（JSON 协议）
- [x] 2026-11-13 11:20 结构化修改协议 `{actions: [...]}`，轮数上限参数化（默认 3）
- [x] 2026-11-13 11:32 预览图回喂多模态模型，完成 2 轮「看图 → 调整 → 重渲染」
- [x] 2026-11-13 11:20 API Key 只读 `.env`，日志与产物中不出现

## M4 交付与联动

- [x] 2026-11-13 11:33 写 `cli.py`：`python -m tools.scene_agent.cli "布置一个客厅"` 一条命令跑完
- [x] 2026-11-13 11:33 输出 `out/scene/scenes/<id>/scene.json`，含每个组件的 id、license、坐标
- [x] 2026-11-13 11:52 验收脚本 `verify.py`：检查 scene.json、许可证、布局合法性、产物文件与预览图分辨率
- [x] 2026-11-13 11:40 编写使用说明 `Docs/scene-agent/usage.md`
- [x] 2026-11-13 12:18 Blender GUI 视口联动：`blender_live.py` 轮询 `live.blend`，每轮构建后自动写；GUI 定时器与重载已实测通过
- [x] 2026-11-13 12:20 CLIP 图像向量：`clipidx.py` 用 ViT-B-32 从 Parquet 缩略图建库，支持 `clipidx query` 与 `cli --clip` 重排

## 实现记录与偏差

1. **单位**：sand3d 程序内部是毫米，`export` 出的 GLB 根节点带 0.001 缩放（米）。拼装时必须
   **相乘**而不是覆盖根节点缩放，否则组件会放大 1000 倍（已修复）。
2. **FTS 写入性能**：`DELETE FROM components_fts WHERE id=?` 对 UNINDEXED 列是全表扫描，
   1.7 万行后每批要几分钟。改为由 id 派生稳定 60 位 rowid + `INSERT OR REPLACE`，56 秒建完 10.6 万行。
3. **数据源**：默认 `DATA_ROOT` 会选中 H 盘云端硬盘，逐行读 Parquet 极慢（5000 行 67 秒）。
   建索引时用 `SAND3D_DATA=data\sand3d-datasets` 走本地盘。
4. **模型延迟**：DashScope `qwen3.7-plus` 默认会输出思维链，布局请求单次 200 秒；
   加 `enable_thinking: false` 后降到 3~30 秒。
5. **朝向**：数据集组件正面朝向不统一，统一假设 +Y，由视觉评审纠正；无法自动判别，属已知限制。
6. **无头 bpy**：未安装 Blender GUI，全部用 `.venv` 里的 bpy 5.2 + Cycles CPU 渲染。

## 验收标准（来自设计文档第 12 节）

- [x] 2026-11-13 一句中文 → `room.blend` + `preview.png`，全流程无人改代码
- [x] 2026-11-13 组件全部来自数据集并记录许可证（scene.json.components）
- [x] 2026-11-13 无浮空/穿地，XY 无 AABB 重叠（校验 issues 为空）
- [x] 2026-11-13 渲染 ≥ 768px，三件物体可辨认
- [ ] 2026-11-13 同输入重跑布局坐标误差 ≤ 1mm——几何重建确定，布局坐标受 LLM 采样影响，不保证
