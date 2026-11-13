# Chat History

## 2026-11-13 11:45 — 场景 Agent（自然语言布置房间）实现

- 需求：一句话描述房间 → 检索 Sand3D 数据集组件 → LLM 布局 → 无头 bpy 构建渲染 → 看图迭代 → 导出 blend/glb。
- 环境：`.venv` 内 bpy 5.2 无头渲染，未安装 Blender GUI；LLM 用 DashScope `qwen3.7-plus`（`enable_thinking: false`）。
- 新增 `tools/scene_agent/`（index/search/rebuild/normalize/builder/render/llm/agent/cli + schema.sql）。
- 索引 106,094 条组件（56 秒，917MB SQLite + FTS5）；抽样 20 个组件重建成功率 100%。
- 端到端跑通：「布置一个 4x5 米的客厅，有沙发、茶几和落地灯」→ 768px 预览、room.blend、room.glb、scene.json，校验无越界无重叠。
- 修复：GLB 根节点 0.001 缩放被覆盖导致组件放大 1000 倍；FTS 按 UNINDEXED 列删除导致索引极慢；LLM 默认思维链导致单次请求 200 秒。
- 文档：更新 `docs/scene-agent/todo.md`，新增 `docs/scene-agent/usage.md`。
- 未执行：git commit/push；验收脚本、GUI 联动、CLIP 重排未做。


## 2026-11-13 11:55 — 数据集检查与验收脚本

- 核对 H 盘数据集：29 个 Parquet / 106,094 行 / 2049MB / 无损坏，与本地副本完全一致；objaverse 的 usda 仍在下载（26,675/66,887），不影响当前流程。
- 结论：不需要安装 Blender GUI 或 blender.exe，.venv 里的 bpy 5.2 足够。
- 新增 	ools/scene_agent/verify.py（验收：scene.json、许可证、布局、产物、分辨率）。
- 验收结果：最终场景 20261113-113329 通过；旧测试场景因 640px 或当时未加 clamp 而失败，属历史产物。

## 2026-11-13 12:35 — GUI 视口联动 + CLIP 图像重排

- 安装 Blender 5.3.0（winget）、torch 2.11.0+cu128、torchvision 0.26.0+cu128、open_clip_torch 4.2.2、Pillow 13。
- 新增 	ools/scene_agent/clipidx.py：ViT-B-32(laion2b_s34b_b79k) 从 Parquet 内嵌缩略图建 106,094 条向量（GPU 9 分钟，库增 170MB）。
- 新增 	ools/scene_agent/blender_live.py：轮询场景目录的 live.blend，1 秒粒度重载；GUI 定时器与重载均实测通过。
- agent 每轮构建后自动写 live.blend；cli 增加 --clip 参数做关键词候选重排。
- 带 --clip 端到端跑通（场景 20261113-123121），verify 全部 PASS。
- 未提交 git；pip 遗留 ~orch/~orchvision 临时目录待清理。
