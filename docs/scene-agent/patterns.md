# 源码逻辑拆解（用于重写，不复制代码）

时间：2026-11-13
范围：`research/` 下 9 个仓库中与「检索 → 布局 → 执行 → 视觉反馈」相关的实现。
原则：只提取流程、协议、参数与坑，代码由我们按自己的数据结构重写。

## 1. Agent 主循环

参考：`research/blender-langgraph-agent/src/graph.py:97-151`、`src/state.py:4-54`

线性阶段 + 修复回环：

```
intent_parser → scene_planner → tool_action_planner → blender_executor
→ scene_exporter → scene_validator → object_rule_validator → vision_critic
→ 若未通过且 repair_count < max_repairs：repair_planner → blender_executor → …（回环）
→ final_response
```

可学之处：

1. **三类校验分开**：文本/场景完整性（scene_validator）、业务规则（object_rule_validator）、视觉质量（vision_critic）。我们的对应物是：JSON schema 校验、AABB/越界检查、渲染图回看。
2. **修复走同一套执行器**：repair_planner 只产出动作列表，不直接改场景，复用 blender_executor，避免两套写场景逻辑。
3. **状态集中**：`AgentState` 保存原请求、计划、动作、错误列表、`preview_image_path`、`vision_report`、`repair_count`。我们的 scene.json 也应按此设计，便于中断续跑。
4. **视觉节点可降级**：没有预览图或视觉模型不可用时，记录错误并跳过，不阻断流程（`vision_critic_node.py:22-48`）。

## 2. 组件检索

参考：`research/Holodeck/ai2holodeck/generation/objaverse_retriever.py:83-137`

打分流程：

```
q = normalize(CLIP_text(query))                  # L2 归一化
clip_sim = 100 * max_over_views(q · img_feat)    # 多视角取最大，放大到 0-100
sbert_sim = q_sbert · text_feat                  # 已归一化
score = clip_sim + sbert_sim
filter: clip_sim > threshold(default 28)
sort desc
```

尺寸重排：把候选包围盒三边排序后与目标尺寸求绝对差均值，越小越优先（`:118-137`）。

对照 `AssetRetrieval3D/backend/vector_search.py:28-80`：余弦用 `1 - (a <=> b)`，按语言分列，`high_quality_only` 做前置过滤，top_k 直接 `ORDER BY ... LIMIT`。

我们的重写方案：

- 向量存 SQLite BLOB（float32），查询时 numpy 批量点积；10 万 × 512 维约 200MB，可全量载入内存。
- 阈值不能照搬 28，需用我们自己的特征分布重新标定（先看 top-20 分数分布）。
- 文本塔与图像塔必须来自同一模型（Holodeck 用了 CLIP + SBERT 两套，是为了弥补 CLIP 文本弱；我们先用单一多模态模型，效果不足再加第二塔）。

## 3. LLM 选物与关系约束

参考：`research/Holodeck/ai2holodeck/generation/prompts.py:57-113`

两段式设计：

```
第一段：room type | object category | description | quantity
第二段：object | global constraint | near/far/in front of/side of/... 
```

其中约束词汇表固定：edge/middle、near/far、in front of/around/side of/left of/right of、center aligned、face to。Guidelines 明确要求：先放锚点大件、同类对齐、优先贴墙、椅子围绕并面向桌子。

**这是最值得借鉴的设计**：LLM 不直接给坐标，只给「关系约束」，由本地求解器算位置，数值幻觉大幅减少。我们第一版可以让 LLM 同时给「关系约束 + 粗略坐标」，本地用约束修正坐标。

## 4. 布局 prompt 与输出格式

参考：`research/OptiScene/prompt.py:1-47`、`research/LayoutGPT/run_layoutgpt_3d.py:165-204`

OptiScene 的 schema：

```json
{"object": "sofa", "coordinates": [{"x": 0, "y": 0, "z": 0}], "rotate": [{"angle": 0}]}
```

- 单位：米；角度：**弧度**；Z 向上，地面 `z=0`；房间中心为原点。
- 先输出 `<reasoning>`，再输出 `<answer>`，并要求自检边界、重叠、通道、旋转单位。

LayoutGPT 的输出是 CSS 风格字符串：`类别 {length; width; height; left; top; depth; orientation}`，角度用**度**。

坑：两者角度单位不同（弧度 vs 度），坐标系原点也不同（中心 vs 左下角）。我们必须在 prompt 里写死一套：**毫米、Z 向上、左下角为原点、角度用度、正面 +Y**，并让校验器拒绝不符合的输出。

## 5. 输出解析与容错

参考：`research/LayoutGPT/parse_llm_output.py:51-72`

做法：按 `{` 切分 → 去单位后缀 → 校验 7 个 key 是否齐全 → 不合法返回 `None`（不抛异常）。

我们的做法：要求模型输出 JSON；解析失败 → 一次重试（附错误信息）→ 仍失败则降级为「仅生成模板布局」，保证流程不中断。

## 6. LLM 代码执行安全

参考：`research/mcp-for-blender/src/blender_mcp/safe_mode.py:94-194`

四层策略：

1. **模块白名单**：仅 `bpy`、`bmesh`、`mathutils` + 纯计算 stdlib；numpy 被刻意排除（反序列化可执行代码）。
2. **内置函数白名单**（默认拒绝）：只放行构造、数值、迭代、类型判断、可抛异常；`exec/eval/open/input/globals/__import__` 一律不在表内。
3. **导入名黑名单**：`system`、`popen`、`environ`、`modules`、`builtins`、`reload`、`import_module` 等。
4. **属性访问限制**：禁止 `bpy` 中可再执行代码的路径（如 `expression`、`script`）。

我们若无头执行，还需要加：子进程隔离、60 秒超时强杀、禁写文件（可用目录白名单）、禁网络。

## 7. 重写伪代码

```python
# agent 主循环
plan = llm_plan(prompt, catalog=search(query))        # 物体清单 + 关系约束
layout = llm_layout(plan, room)                       # 坐标 + 旋转
for round in range(3):
    issues = validate(layout)                         # 越界 / 重叠 / 未落地
    scene = build(layout)                             # exec program + 归一化 + assembly
    png = render(scene)
    report = vision_llm(png, layout, issues)
    if report.ok: break
    layout = apply(report.actions, layout)
export(scene, "room.blend")
```

```python
# 检索
v = normalize(encode_text(query))
scores = 100 * (matrix @ v)          # matrix: N×D
scores[scores <= THRESHOLD] = -inf
candidates = topk(scores, 20)
rank_by_size(candidates, target_bbox)
```

```python
# 校验
assert 0 <= x ± w/2 <= room_w and 0 <= y ± d/2 <= room_d
assert z == 0
assert not aabb_overlap(a, b, tol=5)   # 单位 mm
```

## 8. 必须避免的坑

1. 不要照搬检索阈值：28 是针对对方 CLIP 特征标定的。
2. 不要把角度单位混用：OptiScene 弧度、LayoutGPT 度，我们统一度并在校验器里拒绝弧度。
3. 不要假设包围盒原点在中心：数据集组件原点各异，必须先归一化再算 AABB。
4. 视觉模型不可用时要降级：记录错误、跳过 critic，而不是让整轮失败。
5. 多轮修复必须限次：无上限会持续烧 token。
6. 执行 LLM 代码时禁止 `numpy`、`os`、`pathlib`，否则白名单形同虚设。
