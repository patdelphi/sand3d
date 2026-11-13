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
- 新增 tools/scene_agent/verify.py（验收：scene.json、许可证、布局、产物、分辨率）。
- 验收结果：最终场景 20261113-113329 通过；旧测试场景因 640px 或当时未加 clamp 而失败，属历史产物。

## 2026-11-13 12:35 — GUI 视口联动 + CLIP 图像重排

- 安装 Blender 5.3.0（winget）、torch 2.11.0+cu128、torchvision 0.26.0+cu128、open_clip_torch 4.2.2、Pillow 13。
- 新增 tools/scene_agent/clipidx.py：ViT-B-32(laion2b_s34b_b79k) 从 Parquet 内嵌缩略图建 106,094 条向量（GPU 9 分钟，库增 170MB）。
- 新增 tools/scene_agent/blender_live.py：轮询场景目录的 live.blend，1 秒粒度重载；GUI 定时器与重载均实测通过。
- agent 每轮构建后自动写 live.blend；cli 增加 --clip 参数做关键词候选重排。
- 带 --clip 端到端跑通（场景 20261113-123121），verify 全部 PASS。
- 未提交 git；pip 遗留 ~orch/~orchvision 临时目录待清理。

## 2026-11-13 13:10 — 清理残留 + git 身份 + 网页 UI

- 删除 `.venv\Lib\site-packages` 下 `~orch`（317.6MB）与 `~orchvision`（1.8MB），复验 torch/CUDA/open_clip 与向量库正常。
- git：提交 `14452af feat: add scene agent for natural-language room layout`（22 文件，+2888）并推送到 patdelphi/sand3d main；随后把作者身份设为全局 `patdelphi <patdelphi@users.noreply.github.com>`。
- 新增网页 UI：`server.py`（标准库 http.server，run/adjust/rerun + 静态文件）、`jobs.py`（bpy 子进程，日志行 + RESULT 行）、`web/index.html`（描述输入、实时日志、预览图、glb 在线旋转、组件表微调、再评审一轮）。
- 接口自测：生成 / 微调 / 再评审全部 done；路径穿越请求返回 404；浏览器验收标题、场景列表、预览图、控制台无报错。
- 修复两个真实缺陷：视觉评审此前只发文本未附图（改为 chat.vision 回喂渲染图）；评审动作若引入新越界/重叠则整体回滚。
- 验收：`verify --all` 3 个场景 2 通过，未通过的 20261113-125420 是 640px 测试跑（布局已无 issue）。
- 未执行：git commit/push 本轮改动（等确认）。

## 2026-11-13 13:25 — 修复穿模/越界，新增支撑关系与环境色

- 用户反馈：家具跑到房间外、穿模、电脑落地、询问颜色是否可改与 CLIP 含义。
- 根因：`drop_and_center` 把归一化位移写在 GLB 根节点 `location` 上，拼装时被 `root.location=(x,y,0)` 覆盖，
  导致组件丢失偏移（书桌越出左墙、沙发下沉 180mm）、旋转围绕错误原点摆动。
- 修复：拼装改为「枢轴空物体 + 包围盒底面中心」挂载，平移/旋转/缩放作用于枢轴（builder.build_scene）。
- 新增 `on` 支撑关系：LLM 输出 `"on": "书桌#1"`，本地算 z=支撑物顶面，只允许一层，离开 XY 范围自动落地。
- 校验升级 3D：XY 与 Z 同时相交才算穿插；新增悬空、低于地面、超层高检查（builder.validate）。
- 环境色：墙色/地色/光照强度写入 `scene.json.env`，UI 增加取色器与滑杆；仅改颜色也能重渲染。
- 验证：旧布局重建 6 件全部在房间内、Z≥0；新场景「书房」电脑 z=545mm 在桌面，issues 为空，verify PASS。
- 未执行：git commit/push。

## 2026-11-13 13:40 — 默认环境色与 CLIP 状态确认

- 默认环境色改为淡灰墙 `#d0d0ce`、棕地板 `#6f4a2e`、布光 600（`builder.DEFAULT_ENV`），UI 输入框同步，生成时写入 scene.json.env。
- 渲染仍用 AgX，实测色值会比画面深，已下调默认光照让墙/地更接近设定。
- CLIP 复核：权重已缓存（`~/.cache/huggingface/hub/models--laion--CLIP-ViT-B-32-laion2B-s34B-b79K`），向量库 106,094 条；
  `clipidx query "a modern fabric sofa"` 返回前 10 全是 sofa/armchair，分数 0.28~0.30。
- 未执行：git commit/push。

## 2026-11-13 14:05 — 场景描述历史回溯

- 需求：自动记录每次场景描述（重复不记），可下拉重选。
- 实现：`out/scene/prompts.json` 存历史（最新在前、上限 50、去重、原子写）；`GET /api/prompts` 读取；
  `/api/run` 落库（失败不影响生成）；首次无文件时用已有场景的 prompt 做种子。
- 界面：描述标题行右侧加「历史描述…」下拉，选中即回填输入框，任务完成后自动刷新列表。
- 验证：重复提交后条数不变（3→3）；新描述置顶且落盘 4 条；浏览器下拉 5 项、回填正确、无报错、页面不滚动。
- 未执行：git commit/push。

## 2026-11-13 13:40 — 任务卡住与 500 错误的修复

- 现象：界面提示报错、没有渲染图；日志显示上一任务长期处于 running，再次点击得到
  「已有任务在运行」，队列被占住。
- 复现：同 prompt 走 CLI 24 秒正常出图（preview_2），说明链路本身没坏；卡点在子进程内某个阻塞调用。
- 修复：`_start_job` 单任务墙钟 600 秒上限，超时强杀并置失败；新增 `POST /api/cancel` 与界面「取消」按钮；
  队列占用提示改为「上一轮任务还在运行，可点取消」；每任务日志落盘 `out/scene/logs/<job_id>.log`。
- 验证：运行 5 秒后取消 → `failed | 任务已取消，已终止`；紧接着新任务 200 受理并在 37.3 秒完成，
  `verify` PASS（20261113-133747）。
- 未执行：git commit/push。

## 2026-11-13 13:45 — 历史结果归档与复现

- 存盘早已具备：`out/scene/scenes/<id>/{scene.json,room.blend,room.glb,live.blend,preview_N.png}`。
- 新增 `scene.json.meta`：生成时间、模型名、轮数、分辨率、采样、是否 CLIP、bpy 版本。
- 新增「原样重渲染」按钮 / `POST /api/reproduce` / `jobs rerun --no-review`：只读 scene.json 纯本地重建，不调 LLM。
- 验证：preview_r1 与原始 preview_3 逐像素差值全 0（平均差 0、最大差 0）；rebuild_history 记录重建参数；
  新生成场景 meta = {created_at, model qwen3.7-plus, rounds, width, samples, clip, bpy 5.2.4}。
- 未执行：git commit/push。

## 2026-11-13 13:50 — Colab 下载脚本改为实时进度续传

- 问题：`scripts/colab_download_sand3d.py` 用 `snapshot_download`，每次启动都要逐个检查几万个已下载文件，Drive 上极慢。
- 改造：`list_repo_tree` 一次拉取远程清单 → 逐文件 `hf_hub_download`（8 并发）→ 每下载成功即记入内存，每 200 条写一个进度分片
  `DEST_ROOT/_progress/<repo>/part-xxxxx.txt`（tmp+原子改名）；下次启动只读分片直接跳过已完成文件，不再扫描目录。
- 兼容：进度为空但目标目录非空时自动全量扫描一次（按远程 size 校验）生成基线；`--scan` 强制扫描，`--reset` 忽略历史进度，`--repos` 指定仓库。
- 其他：失败文件重试 5 次指数退避并写 `<repo>_failed.txt`；每 20 个文件打印速度/ETA；Ctrl+C 保存进度。
- 验证：`py_compile` 通过；ProgressStore 分片读写、跨实例续传、分片序号不冲突的临时测试 PASS。
- 未执行：git commit/push；未在 Colab 实机跑（需用户上传替换 Drive 上的 /content/drive/MyDrive/comfyui/scripts/colab_download_sand3d.py）。

## 2026-11-13 14:05 — 复现功能补充 seed 参数

- 需求：为「复现」补充 seed，让相同种子尽量重放同一布局。
- 改动：`llm.Chat.seed`，`text()`/`vision()` 在 payload 里带 `seed`；`agent.run(seed=...)` 未指定时随机生成并写入
  `scene.json.meta.seed`；`cli --seed`；`jobs run --seed`；`jobs rerun` 评审轮复用归档种子；`/api/run` 接受 `seed`
  （非整数返回 400）；界面参数行新增「种子」输入框（留空随机），结果区显示 `seed xxx`，`_summary` 回传 seed。
- 验证：DashScope 接受 `seed` 字段；同进程同 seed 连续请求输出一致；seed=2024 经 UI 两次生成同一组件与坐标
  （落地灯 214.5/166.0）；seed=12345 两次 CLI 选型不同（double_flanged_spool vs router_bed），provider 仅尽力一致；
  `meta.seed` 落盘正确。
- 结论：seed 提高一致性但不保证跨请求完全一致；严格逐像素复现仍用「原样重渲染」。
- 未执行：git commit/push。
