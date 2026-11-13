"""验收脚本：检查一个场景目录的产物是否齐全、布局是否合法。

用法：
  .venv\\Scripts\\python -m tools.scene_agent.verify out\\scene\\scenes\\<id>
  .venv\\Scripts\\python -m tools.scene_agent.verify --all
退出码：0 全部通过，1 有问题。
"""
import argparse
import glob
import json
import os
import struct
import sys

from . import SCENE_DIR
from . import builder


def png_size(path):
    """读 PNG 头部拿宽高，避免为验收引入图像库。"""
    with open(path, "rb") as fh:
        head = fh.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是 PNG 文件")
    return struct.unpack(">II", head[16:24])


def check_scene(folder, min_px=768, min_items=1):
    """返回问题列表（空表示通过）。"""
    problems = []
    scene_path = os.path.join(folder, "scene.json")
    if not os.path.exists(scene_path):
        return [f"缺少 scene.json：{folder}"]
    with open(scene_path, encoding="utf-8-sig") as fh:
        data = json.load(fh)

    room = data.get("room") or {}
    items = data.get("items") or []
    if not room.get("w") or not room.get("d"):
        problems.append("scene.json 缺少房间尺寸")
    if len(items) < min_items:
        problems.append(f"组件数量 {len(items)} 少于 {min_items}")

    # 每个组件必须有来源与许可证
    for it in items:
        if not it.get("component_id"):
            problems.append(f"{it.get('label')} 缺少 component_id")
        if not it.get("license"):
            problems.append(f"{it.get('label')} 缺少 license")
        if "bbox" not in it:
            problems.append(f"{it.get('label')} 缺少 bbox")

    # 布局合法性（越界 / 重叠 / 落地）
    if room.get("w") and all("bbox" in it for it in items):
        issues = builder.validate(items, room)
        problems += [f"布局问题：{i}" for i in issues]

    # 产物文件
    for name in ("room.blend", "room.glb"):
        path = os.path.join(folder, name)
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            problems.append(f"缺少或为空：{name}")

    previews = sorted(glob.glob(os.path.join(folder, "preview_*.png")))
    if not previews:
        problems.append("没有 preview_*.png")
    else:
        try:
            w, h = png_size(previews[-1])
            if min(w, h) < min_px:
                problems.append(f"预览图分辨率 {w}x{h} 小于 {min_px}")
        except Exception as exc:
            problems.append(f"预览图无法读取：{exc}")

    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description="场景产物验收")
    ap.add_argument("folders", nargs="*", help="场景目录；不填配合 --all")
    ap.add_argument("--all", action="store_true", help="检查 out/scene/scenes 下全部场景")
    ap.add_argument("--min-px", type=int, default=768, help="预览图最小边长（默认 768）")
    args = ap.parse_args(argv)

    folders = list(args.folders)
    if args.all:
        folders += sorted(d for d in glob.glob(os.path.join(SCENE_DIR, "*")) if os.path.isdir(d))
    if not folders:
        ap.print_help()
        return 2

    failed = 0
    for folder in folders:
        problems = check_scene(folder, args.min_px)
        name = os.path.basename(os.path.normpath(folder))
        if problems:
            failed += 1
            print(f"FAIL {name}")
            for p in problems:
                print(f"     - {p}")
        else:
            print(f"PASS {name}")
    print(f"共 {len(folders)} 个场景，通过 {len(folders) - failed}，失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
