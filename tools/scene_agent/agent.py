"""场景 Agent 主循环：一句话 -> 选组件 -> 布局 -> 构建 -> 渲染 -> 看图调整 -> 导出。

LLM 只在三处出现：
  1. 解析需求（房间尺寸 + 需要哪些物体）
  2. 给出布局坐标（已知每个组件的真实包围盒）
  3. 看渲染图，给结构化修改动作
所有几何与碰撞都由本地代码负责，模型不写代码、不直接改场景。
"""
import copy
import json
import os
import re
import time
from datetime import datetime

from . import SCENE_DIR
from . import builder, render, search
from .llm import Chat, extract_json
from .rebuild import ensure

SYSTEM = (
    "你是室内布置助手，懂家具尺寸与空间动线。"
    "你只输出 JSON，不输出解释性文字。"
)

PLAN_PROMPT = """把用户需求解析成结构化计划。
用户需求：{prompt}

规则：
- 房间尺寸若未给出，用默认 4000mm(宽 x) x 5000mm(深 y) x 2800mm(高)。
- 每个物体给一个用于数据集检索的英文关键词 query（尽量用具体名词，如 sofa / coffee table / floor lamp）。
- quantity 默认 1；同款重复摆放时 quantity > 1。
输出 JSON：
{{"room": {{"w": 4000, "d": 5000, "h": 2800}},
  "objects": [{{"label": "沙发", "query": "sofa", "quantity": 1}}],
  "style": "一句风格描述"}}
"""

LAYOUT_PROMPT = """为下列物体在房间里排布坐标。
房间：宽 {w}mm（X 方向），深 {d}mm（Y 方向），高 {h}mm。
坐标系：房间左下角为原点，X 向右，Y 向上（俯视），单位毫米，角度单位为度（0/90/180/270）。
物体（label、编号、占地宽 w、占地深 d、高 h，均为毫米）：
{objects}

约束：
1. 物体必须完整在房间内：x±w/2 落在 [0,{w}]，y±d/2 落在 [0,{d}]。
2. 任意两个物体的占地矩形不得重叠（贴边可以）。
3. 大件先靠墙：沙发/柜子/床的背面贴墙（房间四周指 x=0、x={w}、y=0、y={d}）。
4. 茶几放在沙发正前方，与沙发中心大致对齐，与沙发留出 350~500mm 使用距离（即不重叠且间隙合理）。
5. 落地灯放在沙发端头或角落，不挡通道。
6. 主要通道（房间中央）至少留 600mm 宽。
7. 物体之间不要挤在一起，分布要均衡。
8. 能放在别的物体上的小件（电脑/显示器/台灯/书本）必须给 "on" 字段，值为支撑物的
   "label#instance"（如 "书桌#1"），坐标仍按俯视中心给，本地会把高度设为支撑物顶面；
   没有合适的支撑物就省略该字段，物体落地。

只输出 JSON：
{{"items": [{{"label": "沙发", "instance": 1, "x": 中心x, "y": 中心y, "rot_z": 0, "scale": 1.0}},
            {{"label": "电脑", "instance": 1, "x": 中心x, "y": 中心y, "rot_z": 0, "scale": 1.0, "on": "书桌#1"}}],
  "reason": "一句话说明布局思路"}}
"""

REPAIR_PROMPT = """你上一版布局有以下问题：
{issues}

当前布局：
{items}

房间 {w}x{d}mm，原点左下角，单位毫米，角度为度。请修正后重新输出完整布局 JSON（格式同上），
只输出 JSON：{{"items": [...], "reason": "..."}}
"""

CRITIC_PROMPT = """这是一张室内渲染图（房间 {w}x{d}mm，相机在房间前右方斜上方，两面墙在 +Y 与 -X）。
当前摆放：
{items}
校验问题：{issues}

朝向约定：组件正面默认 +Y。靠 y=0 墙放时 rot_z=0（面向房间），靠 y={d} 墙放时 rot_z=180，
靠 x=0 墙时 rot_z=90，靠 x={w} 墙时 rot_z=270。从背后看到沙发/柜子的背面属于正常现象，不要因此旋转。
请以室内设计师视角检查：物体是否落地、是否互相穿插、是否穿墙、朝向是否朝向房间中央、构图是否可辨认。
如果需要调整，只允许以下动作：
- {{"type": "move", "label": "沙发", "instance": 1, "x": 2000, "y": 600}}
- {{"type": "rotate", "label": "沙发", "instance": 1, "rot_z": 180}}
- {{"type": "scale", "label": "落地灯", "instance": 1, "scale": 0.9}}
若已可以接受，输出 {{"done": true, "actions": [], "reason": "一句话"}}。
只输出 JSON：{{"done": false, "actions": [...], "reason": "一句话"}}
"""


PICK_PROMPT = """从候选里挑一个最符合「{label}」的组件。
{label} 的合理尺寸大致是：{hint}
候选（编号 / 名称 / family / 宽x深x高 mm）：
{candidates}
只输出 JSON：{{"pick": 编号, "reason": "一句话"}}
"""


def _log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _plausible(bbox):
    """尺寸合理性粗筛：任何一边小于 50mm 或大于 4000mm 的组件不适合放进房间。"""
    dims = (bbox["w"], bbox["d"], bbox["h"])
    return 50 <= max(dims) <= 4000 and min(dims) >= 20


def choose_component(chat, label, candidates, max_try=5):
    """重建候选并让 LLM 从名称与尺寸里挑一个（失败则退回第一个可用）。"""
    usable = []
    for hit in candidates[:max_try]:
        t0 = time.time()
        try:
            info = ensure(hit["id"])
        except Exception as exc:
            _log(f"  跳过 {hit['id'][:40]}：{exc}")
            continue
        b = info["bbox"]
        if not _plausible(b):
            _log(f"  跳过 {hit['name']}：尺寸不合理 {b['w']:.0f}x{b['d']:.0f}x{b['h']:.0f}mm")
            continue
        _log(f"  候选 {hit['name']}（{hit['family']}）{b['w']:.0f}x{b['d']:.0f}x{b['h']:.0f}mm"
             f"，重建 {time.time() - t0:.1f}s")
        usable.append({**hit, "bbox": b})
    if not usable:
        return None
    if len(usable) == 1:
        return usable[0]
    lines = "\n".join(f"{i}. {c['name']} / {c['family']} / {c['bbox']['w']:.0f}x{c['bbox']['d']:.0f}"
                      f"x{c['bbox']['h']:.0f}" for i, c in enumerate(usable))
    try:
        reply = chat.json(PICK_PROMPT.format(label=label, hint="家具，通常 400~2400mm 宽、300~1100mm 深",
                                             candidates=lines), SYSTEM, temperature=0.1, max_tokens=200)
        idx = int(reply.get("pick", 0))
        if 0 <= idx < len(usable):
            _log(f"  选中 {usable[idx]['name']}：{reply.get('reason', '')[:60]}")
            return usable[idx]
    except Exception as exc:
        _log(f"  选型失败，取第一个：{exc}")
    return usable[0]


def _strip_label(text):
    """去掉模型可能带的编号后缀，便于匹配。"""
    return re.sub(r"[\s_#-]*\d+$", "", (text or "").strip())


def plan_request(chat, prompt):
    """LLM 解析需求 -> 房间尺寸与物体清单（失败时退回关键词兜底）。"""
    try:
        plan = chat.json(PLAN_PROMPT.format(prompt=prompt), SYSTEM, temperature=0.2, max_tokens=800)
        room = plan.get("room") or {}
        room = {
            "w": float(room.get("w") or 4000),
            "d": float(room.get("d") or 5000),
            "h": float(room.get("h") or 2800),
        }
        objects = [o for o in (plan.get("objects") or []) if o.get("query")]
        if objects:
            plan["room"], plan["objects"] = room, objects
            return plan
        raise ValueError("计划里没有物体")
    except Exception as exc:
        _log(f"需求解析失败，改用别名兜底：{exc}")
        found = [{"label": k, "query": k, "quantity": 1} for k in search.ALIASES if k in prompt]
        return {"room": {"w": 4000, "d": 5000, "h": 2800},
                "objects": found or [{"label": "桌子", "query": "table", "quantity": 1}],
                "style": ""}


def pick_components(chat, objects, use_clip=False):
    """按关键词检索、重建并选型；返回布局条目（每个实例一条）。"""
    picked = []
    for obj in objects:
        label, query = obj["label"], obj["query"]
        quantity = max(1, int(obj.get("quantity") or 1))
        hits = search.search(query, limit=10)
        if use_clip and hits:
            try:
                from . import clipidx
                hits = clipidx.rerank(query, hits, limit=10)
                _log("  CLIP 重排完成")
            except Exception as exc:
                _log(f"  CLIP 重排不可用，按关键词排序：{exc}")
        _log(f"检索「{label}」({query})：{len(hits)} 个候选")
        chosen = choose_component(chat, label, hits)
        if not chosen:
            _log(f"  「{label}」没有可用组件，忽略")
            continue
        for n in range(1, quantity + 1):
            picked.append({
                "label": label, "instance": n,
                "component_id": chosen["id"], "name": chosen["name"],
                "family": chosen["family"], "license": chosen["license"],
                "bbox": chosen["bbox"],
            })
    return picked


def _object_lines(items):
    """给 LLM 看的物体清单。"""
    return "\n".join(
        f"- {it['label']}#{it['instance']}：宽 {it['bbox']['w']:.0f}，深 {it['bbox']['d']:.0f}，"
        f"高 {it['bbox']['h']:.0f}" for it in items)


def make_layout(chat, room, items):
    """LLM 出坐标，本地夹紧 + 校验；有问题回喂一次修正，仍不行则用兜底布局。"""
    prompt = LAYOUT_PROMPT.format(w=room["w"], d=room["d"], h=room["h"], objects=_object_lines(items))
    layout = {"room": room, "items": _merge(items, chat.json(prompt, SYSTEM, temperature=0.3, max_tokens=1200))}
    builder.clamp(layout["items"], room)
    builder.resolve_support(layout["items"])
    issues = builder.validate(layout["items"], room)
    if issues:
        _log(f"布局校验问题：{issues}")
        repair = REPAIR_PROMPT.format(
            issues="\n".join(f"- {i}" for i in issues),
            items=json.dumps(layout["items"], ensure_ascii=False),
            w=room["w"], d=room["d"])
        layout["items"] = _merge(items, chat.json(repair, SYSTEM, temperature=0.3, max_tokens=1200))
        builder.clamp(layout["items"], room)
        builder.resolve_support(layout["items"])
        issues = builder.validate(layout["items"], room)
        _log(f"修正后问题：{issues or '无'}")
    if issues:
        _log("改用兜底排布（沿墙排序摆放）")
        layout["items"] = builder.fallback_layout(layout["items"], room)
        issues = builder.validate(layout["items"], room)
        _log(f"兜底后问题：{issues or '无'}")
    layout["issues"] = issues
    return layout


def _merge(items, reply):
    """把 LLM 的坐标合并回本地条目（component_id / bbox / license 以本地为准）。"""
    by_key = {(it["label"], it["instance"]): it for it in items}
    by_label = {}
    for it in items:
        by_label.setdefault(it["label"], []).append(it)
    merged = []
    for raw in (reply.get("items") or []):
        label = _strip_label(raw.get("label"))
        inst = int(raw.get("instance") or 1)
        src = by_key.get((label, inst))
        if src is None:
            group = by_label.get(label) or []
            src = group[min(inst, len(group)) - 1] if group else None
        if src is None:
            continue
        merged.append({
            **src,
            "x": float(raw.get("x", 0)), "y": float(raw.get("y", 0)),
            "rot_z": int(round(float(raw.get("rot_z", 0)))) % 360,
            "scale": float(raw.get("scale") or 1.0),
            **({"on": str(raw["on"])} if raw.get("on") else {}),
        })
    # LLM 漏掉的条目补一个靠墙默认位，保证不丢物体
    for it in items:
        if not any(m["component_id"] == it["component_id"] and m["label"] == it["label"]
                   and m["instance"] == it["instance"] for m in merged):
            merged.append({**it, "x": it["bbox"]["w"] / 2 + 50, "y": it["bbox"]["d"] / 2 + 50,
                           "rot_z": 0, "scale": 1.0})
    return merged


def apply_actions(layout, actions):
    """执行 critic 的 move/rotate/scale 动作。"""
    changed = 0
    for act in actions or []:
        label = _strip_label(act.get("label"))
        inst = int(act.get("instance") or 1)
        target = None
        for it in layout["items"]:
            if it["label"] == label and int(it.get("instance", 1)) == inst:
                target = it
                break
        if target is None:
            continue
        kind = act.get("type")
        if kind == "move":
            target["x"] = float(act.get("x", target["x"]))
            target["y"] = float(act.get("y", target["y"]))
        elif kind == "rotate":
            target["rot_z"] = int(round(float(act.get("rot_z", target["rot_z"])))) % 360
        elif kind == "scale":
            target["scale"] = float(act.get("scale") or target.get("scale", 1.0))
        else:
            continue
        changed += 1
    return changed


def _item_lines(items):
    """给 critic 看的当前摆放（含高度与支撑关系）。"""
    return "\n".join(
        f"- {it['label']}#{it['instance']} 位置({it['x']:.0f},{it['y']:.0f}) 旋转{it['rot_z']}度 "
        f"缩放{it.get('scale', 1.0):.2f} 占地{it['bbox']['w']:.0f}x{it['bbox']['d']:.0f} "
        f"高{it['bbox']['h'] * it.get('scale', 1.0):.0f} 底面z={it.get('z', 0):.0f}"
        + (f" 放在{it['on']}上" if it.get("on") else "")
        for it in items)


def review_scene(chat, layout, room, png):
    """把渲染图 + 当前布局回喂多模态模型，返回结构化评审。"""
    issues = builder.validate(layout["items"], room)
    prompt = CRITIC_PROMPT.format(
        w=room["w"], d=room["d"], items=_item_lines(layout["items"]),
        issues="\n".join(issues) or "无")
    if png and os.path.exists(png):
        text = chat.vision(png, prompt, SYSTEM, max_tokens=700)
    else:
        text = chat.text(prompt, SYSTEM, temperature=0.2, max_tokens=700, json_mode=True)
    return extract_json(text)


def apply_review(layout, review, room):
    """应用评审动作，并在引入新冲突时回滚。

    返回 (改动数, 原因)：原因取 ok / done / noop / rejected。
    回滚规则：原来没有校验问题、改完出现问题时，视为评审方案不可用。
    """
    if review.get("done") or not review.get("actions"):
        return 0, "done"
    before = copy.deepcopy(layout["items"])
    changed = apply_actions(layout, review["actions"])
    if not changed:
        return 0, "noop"
    builder.clamp(layout["items"], room)
    builder.resolve_support(layout["items"])
    if not builder.validate(before, room) and builder.validate(layout["items"], room):
        layout["items"] = before
        return 0, "rejected"
    return changed, "ok"


def run(prompt, rounds=3, width=768, samples=32, out_root=None, chat=None, use_clip=False,
        env=None, seed=None):
    """完整流程。返回结果字典（含产物路径）。env 控制墙色/地色/光照强度；seed 固定采样便于复现。"""
    import random
    chat = chat or Chat()
    # 未指定种子时随机生成并记录，保证每个场景事后都能用同一 seed 重放
    seed = int(seed) if seed is not None else random.randint(1, 2 ** 31 - 1)
    chat.seed = seed
    out_root = out_root or SCENE_DIR
    scene_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    work = os.path.join(out_root, scene_id)
    os.makedirs(work, exist_ok=True)

    plan = plan_request(chat, prompt)
    room = plan["room"]
    _log(f"房间 {room['w']:.0f}x{room['d']:.0f}x{room['h']:.0f}mm，物体：{[o['label'] for o in plan['objects']]}")

    items = pick_components(chat, plan["objects"], use_clip)
    if not items:
        raise RuntimeError("没有找到任何可用组件")
    layout = make_layout(chat, room, items)
    layout["env"] = {**builder.DEFAULT_ENV, **(env or {})}
    # 归档元数据：用于追溯这次结果由哪个模型、什么参数生成
    import bpy
    layout["meta"] = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": getattr(chat, "model", ""),
        "rounds": rounds, "width": width, "samples": samples,
        "clip": bool(use_clip), "bpy": bpy.app.version_string,
        "seed": seed,
    }

    builder.build_scene(layout)
    # 每轮构建后写 live.blend，供 Blender GUI 联动脚本轮询
    builder.export_scene(os.path.join(work, "live.blend"))
    scene = render.setup(room, "iso", width, width, samples, env=layout.get("env"))
    png, secs = render.render(os.path.join(work, "preview_1.png"), scene)
    _log(f"渲染完成：{os.path.basename(png)}（{secs}s）")

    history = []
    for rnd in range(1, rounds + 1):
        try:
            review = review_scene(chat, layout, room, png)
        except Exception as exc:
            _log(f"视觉评审解析失败，停止迭代：{exc}")
            history.append({"round": rnd, "error": str(exc)})
            break
        history.append({"round": rnd, "review": review})
        _log(f"第 {rnd} 轮评审：done={review.get('done')} reason={review.get('reason', '')[:80]}")
        changed, why = apply_review(layout, review, room)
        if why != "ok":
            _log(f"评审动作未采用（{why}）")
            break
        builder.build_scene(layout)
        builder.export_scene(os.path.join(work, "live.blend"))
        scene = render.setup(room, "iso", width, width, samples, env=layout.get("env"))
        png, secs = render.render(os.path.join(work, f"preview_{rnd + 1}.png"), scene)
        _log(f"重渲染：{os.path.basename(png)}（{secs}s）")

    # 最终产物
    layout["room"] = room
    layout["issues"] = builder.validate(layout["items"], room)
    layout["plan"] = plan
    layout["prompt"] = prompt
    layout["review_history"] = history
    layout["preview"] = png
    layout["components"] = [
        {"label": it["label"], "instance": it["instance"], "component_id": it["component_id"],
         "name": it["name"], "family": it["family"], "license": it["license"]}
        for it in layout["items"]]

    scene_path = builder.write_layout(layout, os.path.join(work, "scene.json"))
    blend = builder.export_scene(os.path.join(work, "room.blend"))
    glb = builder.export_scene(os.path.join(work, "room.glb"))
    _log(f"产物：{work}")
    return {"scene_id": scene_id, "dir": work, "scene": scene_path, "blend": blend,
            "glb": glb, "preview": png, "layout": layout}
