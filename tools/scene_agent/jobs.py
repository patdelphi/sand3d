"""网页 UI 的后台任务进程：生成 / 微调 / 再评审。

由 server.py 以子进程方式启动，固定单进程执行 bpy，避免阻塞 HTTP 线程。
标准输出按行协议回报：
  - 普通行  -> 日志
  - "RESULT {json}" -> 任务结果
用法：
  python -m tools.scene_agent.jobs run --prompt "..." --rounds 2 --width 768 --samples 32
  python -m tools.scene_agent.jobs adjust --scene <dir> --items '[{"label":"sofa","x":800}]'
  python -m tools.scene_agent.jobs rerun --scene <dir>
"""
import argparse
import glob
import json
import os
import sys

# bpy 必须先于几何相关模块导入（无头模式）
import bpy  # noqa: F401

from . import builder, render
from . import agent as agent_mod
from .llm import Chat


def _log(msg):
    print(msg, flush=True)


def _emit(obj):
    """把结果打成单行，server 侧按前缀解析。"""
    print("RESULT " + json.dumps(obj, ensure_ascii=False), flush=True)


def _load_scene(folder):
    path = os.path.join(folder, "scene.json")
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def _save_scene(folder, layout):
    builder.write_layout(layout, os.path.join(folder, "scene.json"))
    builder.export_scene(os.path.join(folder, "live.blend"))
    builder.export_scene(os.path.join(folder, "room.glb"))


def _rebuild(layout, folder, tag, width, samples):
    """重建几何 + 导出 live.blend + 渲染一张图。"""
    room = layout["room"]
    builder.build_scene(layout)
    builder.export_scene(os.path.join(folder, "live.blend"))
    scene = render.setup(room, "iso", width, width, samples, env=layout.get("env"))
    return render.render(os.path.join(folder, f"preview_{tag}.png"), scene)


def _next_index(folder, prefix):
    return len(glob.glob(os.path.join(folder, f"preview_{prefix}*.png"))) + 1


def _summary(layout, folder, png, secs, changed):
    """回给前端的最小结果集。"""
    return {
        "scene_id": os.path.basename(folder),
        "dir": folder,
        "preview": png,
        "preview_url": "/files/" + os.path.basename(folder) + "/" + os.path.basename(png),
        "glb_url": "/files/" + os.path.basename(folder) + "/room.glb",
        "render_s": round(secs, 1),
        "changed": changed,
        "seed": (layout.get("meta") or {}).get("seed"),   # 回传种子，前端可复用复现
        "issues": layout.get("issues", []),
        "env": layout.get("env", {}),
        "items": [
            {"label": it["label"], "instance": it.get("instance", 1),
             "component_id": it["component_id"], "name": it.get("name"),
             "license": it.get("license"), "x": round(it["x"], 1), "y": round(it["y"], 1),
             "rot_z": it.get("rot_z", 0), "scale": it.get("scale", 1.0),
             "bbox": it.get("bbox")}
            for it in layout["items"]],
    }


def cmd_run(args):
    env = json.loads(args.env) if args.env else {}
    res = agent_mod.run(args.prompt, rounds=args.rounds, width=args.width,
                        samples=args.samples, use_clip=args.clip, env=env,
                        seed=getattr(args, "seed", None))
    layout = res["layout"]
    out = _summary(layout, res["dir"], res["preview"], 0.0, 0)
    out.update({"scene": res["scene"], "blend": res["blend"], "glb": res["glb"],
                "review_history": layout.get("review_history", [])})
    _emit(out)


def cmd_adjust(args):
    """按前端提交的坐标/旋转/缩放改布局，夹紧后重渲染。"""
    folder = os.path.abspath(args.scene)
    layout = _load_scene(folder)
    room = layout["room"]
    overrides = json.loads(args.items)
    index = {(it["label"], int(it.get("instance", 1))): it for it in layout["items"]}
    changed = 0
    for ov in overrides:
        target = index.get((ov.get("label"), int(ov.get("instance", 1) or 1)))
        if target is None:
            _log(f"跳过未知组件：{ov.get('label')}")
            continue
        for key in ("x", "y"):
            if key in ov:
                target[key] = float(ov[key])
        if "rot_z" in ov:
            target["rot_z"] = int(round(float(ov["rot_z"]))) % 360
        if "scale" in ov:
            target["scale"] = max(0.2, min(5.0, float(ov["scale"])))
        changed += 1
    if not changed and not args.env:
        raise SystemExit("没有可应用的修改")

    if args.env:
        layout["env"] = {**builder.DEFAULT_ENV, **layout.get("env", {}), **json.loads(args.env)}
    builder.clamp(layout["items"], room)
    n = _next_index(folder, "edit")
    _log(f"重建并渲染第 {n} 次微调…")
    png, secs = _rebuild(layout, folder, f"edit_{n}", args.width, args.samples)
    layout["issues"] = builder.validate(layout["items"], room)
    layout["preview"] = png
    layout.setdefault("edit_history", []).append(
        {"edit": n, "overrides": overrides, "png": os.path.basename(png)})
    _save_scene(folder, layout)
    _log(f"微调完成：{os.path.basename(png)}（{secs}s）")
    _emit(_summary(layout, folder, png, secs, changed))


def cmd_rerun(args):
    """用当前布局重渲染；--no-review 时纯本地重建，不调 LLM（可逐像素复现）。"""
    folder = os.path.abspath(args.scene)
    layout = _load_scene(folder)
    room = layout["room"]
    n = _next_index(folder, "r")
    png, secs = _rebuild(layout, folder, f"r{n}", args.width, args.samples)
    _log(f"重渲染：{os.path.basename(png)}（{secs}s）")

    if args.no_review:
        layout["issues"] = builder.validate(layout["items"], room)
        layout["preview"] = png
        layout.setdefault("rebuild_history", []).append(
            {"png": os.path.basename(png), "secs": secs, "width": args.width,
             "samples": args.samples})
        _save_scene(folder, layout)
        _log("原样重建完成（未调用 LLM）")
        _emit(_summary(layout, folder, png, secs, 0))
        return

    chat = Chat()
    # 复用归档里的种子，评审这一轮也能重放
    chat.seed = (layout.get("meta") or {}).get("seed")
    review = agent_mod.review_scene(chat, layout, room, png)
    _log(f"评审：done={review.get('done')} reason={review.get('reason', '')[:100]}")
    history = layout.setdefault("review_history", [])
    history.append({"round": len(history) + 1, "review": review, "source": "ui-rerun"})

    changed, why = agent_mod.apply_review(layout, review, room)
    if why == "rejected":
        _log("评审方案会引入新冲突，已回滚")
    if changed:
        png, secs = _rebuild(layout, folder, f"r{n + 1}", args.width, args.samples)
        _log(f"按评审重渲染：{os.path.basename(png)}（{secs}s）")

    layout["issues"] = builder.validate(layout["items"], room)
    layout["preview"] = png
    _save_scene(folder, layout)
    out = _summary(layout, folder, png, secs, changed)
    out["review"] = review
    _emit(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description="场景 Agent 后台任务")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="从一句话生成场景")
    p_run.add_argument("--prompt", required=True)
    p_run.add_argument("--rounds", type=int, default=3)
    p_run.add_argument("--width", type=int, default=768)
    p_run.add_argument("--samples", type=int, default=32)
    p_run.add_argument("--clip", action="store_true")
    p_run.add_argument("--env", default="", help='如 {"wall":"#dddcda","floor":"#8b6c50","light":1200}')
    p_run.add_argument("--seed", type=int, default=None, help="采样种子，相同种子便于复现布局")
    p_run.set_defaults(func=cmd_run)

    p_adj = sub.add_parser("adjust", help="微调组件位置")
    p_adj.add_argument("--scene", required=True)
    p_adj.add_argument("--items", required=True, help="JSON 数组")
    p_adj.add_argument("--env", default="", help="环境色，可选")
    p_adj.add_argument("--width", type=int, default=768)
    p_adj.add_argument("--samples", type=int, default=32)
    p_adj.set_defaults(func=cmd_adjust)

    p_re = sub.add_parser("rerun", help="再评审一轮 / 原样重建")
    p_re.add_argument("--scene", required=True)
    p_re.add_argument("--width", type=int, default=768)
    p_re.add_argument("--samples", type=int, default=32)
    p_re.add_argument("--no-review", action="store_true",
                      help="纯本地重建，不调用 LLM（用于逐像素复现）")
    p_re.set_defaults(func=cmd_rerun)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # 子进程异常要能被前端看到
        print(f"任务失败：{type(exc).__name__}: {exc}", flush=True)
        sys.exit(1)
