# 场景 Agent 设计文档

版本：v0.1（2026-11-13）
状态：待评审，未开始编码

## 1. 目标

用户用一句自然语言描述场景（例如「布置一个 4×5 米的客厅，有沙发、茶几、落地灯」），系统自动：

1. 在 sand3d 数据集（106,094 个物体）里找到合适的组件；
2. 用组件自带的 Sand3D 程序重建出几何；
3. 按布局方案拼装成完整场景；
4. 在 Blender（无头 bpy）里渲染预览图；
5. 把预览图回喂给 LLM，自动调整相机、灯光、材质与摆位；
6. 导出 `.blend` / `.glb` / `.usda`。

## 2. 非目标

- 不改 `src/sand3d/` 的公共 API（库只依赖 bpy/bmesh/mathutils/标准库，约束见 AGENTS.md）。
- 不做生成式网格（不接文生 3D 模型），只复用数据集里已有的程序化物体。
- 不做完整物理仿真，只做 XY 平面 AABB 级别的防重叠。
- 第一版不做 Blender GUI 实时联动，先无头出图。

## 3. 关键假设（待确认）

| 项 | 默认取值 | 备选 |
|---|---|---|
| LLM 接口 | OpenAI 兼容 API（base_url 可配） | Claude SDK、本地 Ollama |
| 检索 | 关键词召回 + CLIP 重排（分两期） | 仅关键词、仅 CLIP |
| Blender | 先无头 bpy，稳定后接 GUI | 直接 GUI 实时联动 |
| 组件复用方式 | 代码复用（执行 program） | 网格复用（导入 usda/glb） |

## 4. 总体架构

```
用户一句话
    │
    ▼
┌─────────────────────────────┐
│ Agent（LLM，function calling）│
│  search → 选型 → place → 看效果 │
└──────────────┬──────────────┘
               │ 工具调用
               ▼
┌─────────────────────────────┐
│ 场景编排器（本地 Python）      │
│  ├ 组件服务：索引 / 检索 / 重建缓存 │
│  ├ 归一化：单位、朝向、落地、包围盒  │
│  ├ 组装器：sand3d frames/assembly │
│  └ 执行器：bpy 无头构建 + 渲染     │
└──────────────┬──────────────┘
               ▼
      scene.json + room.blend + preview.png
               │
               └──► 预览图回喂 LLM，进入下一轮调整
```

## 5. 数据与索引

数据源：`data/sand3d-datasets/` 下的 29 个 Parquet（关键字段：`id`、`source`、`source_group`、`family`、`program`、`entry`、`render`、`usda_path`、`n_joints`、`usda_bytes`、`license`）。

落库到本地 SQLite（`out/scene/index.db`），避免每次扫 2GB Parquet：

- `components(id TEXT PK, source, family, name, program TEXT, entry, render_path, usda_path, n_joints INT, license, bbox_x, bbox_y, bbox_z)`
- `components_fts`：FTS5 虚拟表，索引 `id / family / source / program` 文本
- `rebuild_cache(id PK, program_sha, sand3d_version, blend_path, glb_path, bbox_json, status, error)`
- `scenes(scene_id PK, prompt, spec_json, blend_path, preview_path, created_at)`

CLIP 向量（第二期）：`image_embeddings(id PK, dim, vector BLOB)`，用渲染图 `render` 建库，查询时余弦相似度重排关键词的前 200 个候选。

## 6. Agent 工具协议

| 工具 | 入参 | 返回 | 说明 |
|---|---|---|---|
| `search_components` | `query, family?, limit=10` | `[{id, family, thumb_url, bbox_mm}]` | 只返回缩略图与尺寸，避免整库重建 |
| `inspect_component` | `id` | `{program, entry, joints, license}` | 需要改参数时再取程序 |
| `add_component` | `id, x, y, rot_z, scale=1, params={}` | `{handle, bbox_mm, overlaps[]}` | 触发重建（走缓存）并加入场景 |
| `move_component` | `handle, x, y, rot_z` | `{ok, overlaps[]}` | 布局微调 |
| `remove_component` | `handle` | `{ok}` | |
| `render_preview` | `camera?, lights?, width=768, samples=32` | `{png_path, duration_s}` | Cycles CPU |
| `set_environment` | `light_energy?, color_temp?, world_color?, wall_style?` | `{ok}` | 控制观感 |
| `export_scene` | `format: blend\|glb\|usda, path` | `{path}` | 最终产物 |

工具返回必须短：图片走路径引用，不内联 base64，控制上下文成本。

## 7. 归一化规则

组件来自不同来源，原点和朝向不统一，统一按以下规则处理：

1. 单位统一为毫米（sand3d 默认单位）。
2. Z 轴向上，包围盒最低点 `z=0` 落地。
3. 水平方向按包围盒 XY 中心对齐到原点。
4. 正面朝向：第一版不做自动判别，统一按 `+Y`，由布局时 `rot_z` 修正；后续用「最大平面法线」或渲染图估计。
5. 缩放：默认 1；LLM 可传 `scale`，或用真实尺寸提示（如「沙发长 2 米」）反算。
6. 每个组件缓存 `bbox_mm`，用于摆放与碰撞。

## 8. 布局与碰撞

- 坐标系：地面 `z=0`，房间以左下角为原点，单位毫米。
- 碰撞：组件 AABB 在 XY 平面投影相交即冲突（允许贴边，容差 5mm）。
- 通道：茶几与沙发最小间距 400mm，主要通道 600mm（第一版作为软约束，只告警不阻断）。
- 布局方案由 LLM 输出结构化 JSON：`[{id, x, y, rot_z, scale}]`，本地代码执行并回报冲突。

## 9. Blender 执行层

- 使用现有 `.venv` 的 `bpy 5.2.4`，不安装 Blender GUI。
- 渲染器：Cycles CPU，`samples=32`，分辨率 768×768，开启 OIDN 降噪。
- EEVEE 在无显示设备的环境下不可靠，禁用。
- 相机：默认 45° 俯视等距视角；支持 `front / top / iso` 预设。
- 灯光：三点布光（key/fill/rim）+ 环境光，参数可由 LLM 调整。
- 渲染产物：`out/scene/<scene_id>/preview_<n>.png`。
- 单次渲染预计 10–60 秒，一轮对话最多渲染 3 次。

## 10. 视觉反馈闭环

1. Agent 生成布局 → 执行构建 → 渲染 `preview_1.png`。
2. 把图片与「当前布局清单」回喂多模态 LLM，要求输出结构化修改：`{actions: [{type, ...}], reason}`。
3. 本地执行修改 → 重新渲染 → 循环，最多 3 轮。
4. 结束条件：LLM 返回 `done`，或轮次用尽，或用户中断。
5. 成本控制：候选只给缩略图；预览图 768px 压缩后传输；不重复渲染未变化的画面。

## 11. 目录结构（新增，不触碰 src/sand3d）

```
tools/scene_agent/
├── __init__.py
├── index.py        # Parquet → SQLite + FTS
├── search.py       # 关键词检索，后续接入 CLIP 重排
├── rebuild.py      # 执行 program + 缓存 .blend/.glb
├── normalize.py    # 单位/朝向/落地/包围盒
├── builder.py      # 布局 JSON → sand3d assembly
├── render.py       # bpy 无头渲染与相机灯光预设
├── agent.py        # LLM 工具调用循环
├── schema.sql
└── cli.py          # python -m tools.scene_agent "布置一个客厅"
out/scene/
├── index.db
├── cache/components/<id>.glb
└── <scene_id>/{scene.json, room.blend, preview_1.png, room.glb}
```

依赖：`pyarrow`（已有）、`requests`（调用 LLM）、标准库 `sqlite3`。CLIP 阶段再加 `torch`、`open_clip_torch`，写进独立依赖文件，不污染 `env/kit.lock`。

## 12. 验收标准（MVP）

- [ ] 输入「4×5 米客厅，沙发、茶几、落地灯」，输出 `room.blend` 与 `preview.png`
- [ ] 3 个组件全部来自数据集，且记录 `id` 与许可证
- [ ] 组件落地（无浮空/穿地），XY 无 AABB 重叠
- [ ] 渲染分辨率 ≥ 768，可辨认三件物体
- [ ] 至少完成 1 轮「看图 → 调整 → 重渲染」
- [ ] 全流程无人工改代码
- [ ] 同一输入重复运行，布局坐标误差 ≤ 1mm（程序重建缓存确定）

## 13. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 数据集无语义标签 | 搜不到想要的东西 | 第二期加 CLIP；先用 family + 程序文本 + 人工别名表 |
| 组件朝向不一致 | 家具背对镜头 | 允许 LLM 按预览图纠正 `rot_z`；后续自动判别 |
| bpy 进程崩溃/卡死 | 流程中断 | 每次重建独立子进程，超时 60s 强杀，记录失败 |
| 10 万条程序重建开销 | 耗时 | 只重建被选中的组件，缓存 `.glb` 复用 |
| LLM 布局不合理 | 穿墙、挡门 | AABB 冲突回传，要求重排；房间边界硬约束 |
| 多轮渲染成本 | token 与时间 | 每轮最多 1 张图，最多 3 轮 |
| API Key 泄露 | 安全 | 只读环境变量，不写文件、不入库、不打印 |
| 许可证 | 合规 | 场景清单附带每个组件的 license 与来源，导出时打印 |

## 14. 里程碑

- **M0 索引**：2GB Parquet → SQLite FTS，`search` 可查中文关键词
- **M1 重建**：单个组件 `exec` → sand3d 对象 → GLB 缓存，含失败隔离
- **M2 布局**：手工写死 3 件家具的 JSON，能拼装并导出 GLB/blend
- **M3 视觉闭环**：接入 LLM 工具调用 + 渲染回喂，自动调整 1 轮以上
- **M4 实时联动**：Blender GUI 轮询场景文件，视口自动刷新（可选）

## 15. 待确认问题

1. LLM 供应商与模型名（影响 function calling 兼容写法）。
2. 是否允许引入 `requests` 之外的第三方依赖（torch/CLIP）。
3. 组件复用选「代码重建」还是「直接用 usda 网格」（后者更快但不可改参数）。
4. 是否需要多人协作/服务化（HTTP API），还是本地 CLI 即可。
5. 场景产物优先 `.blend` 还是 `.glb`。
