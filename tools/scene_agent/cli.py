"""命令行入口：一条命令跑完「检索 -> 布局 -> 构建 -> 渲染 -> 迭代 -> 导出」。

用法：
  .venv\\Scripts\\python -m tools.scene_agent.cli "布置一个 4x5 米的客厅，要有沙发、茶几和落地灯"
"""
import argparse
import json
import os
import sys

# bpy 必须先导入，sand3d 的模块会依赖 Blender 运行时
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "src"))

import bpy  # noqa: E402,F401

from .agent import run  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="自然语言布置房间")
    ap.add_argument("prompt", nargs="*", help="场景描述，如：布置一个 4x5 米的客厅，沙发、茶几、落地灯")
    ap.add_argument("--rounds", type=int, default=3, help="看图迭代轮数上限（默认 3）")
    ap.add_argument("--width", type=int, default=768, help="渲染分辨率（默认 768）")
    ap.add_argument("--samples", type=int, default=32, help="Cycles 采样数（默认 32）")
    ap.add_argument("--out", help="输出根目录（默认 out/scene/scenes）")
    ap.add_argument("--clip", action="store_true", help="用 CLIP 向量重排检索候选（需先建向量库）")
    args = ap.parse_args(argv)
    prompt = " ".join(args.prompt).strip()
    if not prompt:
        ap.print_help()
        return 2
    result = run(prompt, rounds=args.rounds, width=args.width, samples=args.samples, out_root=args.out,
                 use_clip=args.clip)
    print(json.dumps({k: result[k] for k in ("scene_id", "dir", "scene", "blend", "glb", "preview")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
