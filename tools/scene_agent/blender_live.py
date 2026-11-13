"""Blender GUI 视口联动：轮询场景目录里的 live.blend，文件一变就重新加载。

用法（先启动一次生成流程，或已有场景目录）：
  blender --factory-startup --python tools\\scene_agent\\blender_live.py -- "out\\scene\\scenes\\<id>"
不传目录时自动盯最新的场景目录。轮询间隔 1 秒；加载的是 live.blend，没有则退到 room.blend。

注意：open_mainfile 会重载整个文件，场景里未保存的临时修改会丢失，这是联动的预期行为。
"""
import glob
import os
import sys

import bpy

POLL = 1.0

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCENES_ROOT = os.path.join(PROJECT_ROOT, "out", "scene", "scenes")


def latest_scene_dir():
    """out/scene/scenes 下最新的目录。"""
    dirs = [d for d in glob.glob(os.path.join(SCENES_ROOT, "*")) if os.path.isdir(d)]
    return max(dirs, key=os.path.getmtime) if dirs else None


def target_file(folder):
    """优先 live.blend（每轮构建后写），其次 room.blend（最终产物）。"""
    live = os.path.join(folder, "live.blend")
    if os.path.exists(live):
        return live
    final = os.path.join(folder, "room.blend")
    return final if os.path.exists(final) else live


def _stamp(path):
    """文件指纹：修改时间 + 大小，避免半写入时加载。"""
    try:
        st = os.stat(path)
        return (st.st_mtime, st.st_size)
    except OSError:
        return None


class Watcher:
    """盯住一个场景目录，文件变化就重新加载。"""

    def __init__(self, folder):
        self.folder = folder
        self.stamp = _stamp(target_file(folder))
        self.loads = 0

    def reload_if_changed(self, force=False):
        path = target_file(self.folder)
        stamp = _stamp(path)
        if stamp is None or (stamp == self.stamp and not force):
            return False
        if stamp[1] == 0:          # 正在写入，等下一轮
            return False
        self.stamp = stamp
        try:
            bpy.ops.wm.open_mainfile(filepath=path)
            self.loads += 1
            print(f"[live] 已加载 {os.path.basename(path)}（第 {self.loads} 次）")
            return True
        except Exception as exc:   # 文件被占用或尚未写完，下一轮再试
            print(f"[live] 加载失败，稍后重试：{exc}")
            return False


_WATCHER = None


def _tick():
    """定时器回调：返回轮询间隔（秒）。"""
    global _WATCHER
    if _WATCHER is None:
        return None
    try:
        _WATCHER.reload_if_changed()
    except Exception as exc:
        print(f"[live] 轮询异常：{exc}")
    return POLL


def start(folder=None):
    """开始联动；返回监听的目录。"""
    global _WATCHER
    folder = os.path.abspath(folder or latest_scene_dir() or "")
    if not folder or not os.path.isdir(folder):
        raise RuntimeError(f"场景目录不存在：{folder!r}")
    _WATCHER = Watcher(folder)
    if not _WATCHER.stamp:
        print(f"[live] 目录里还没有 .blend，等待生成：{folder}")
    bpy.app.timers.register(_tick, first_interval=POLL, persistent=True)
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)
    print(f"[live] 开始监视 {folder}（每 {POLL:.0f} 秒检查一次）")
    return folder


def _on_load(_scene):
    """文件重载后重新挂上定时器（重载会清空定时器）。"""
    if _WATCHER is not None:
        bpy.app.timers.register(_tick, first_interval=POLL, persistent=True)


def _folder_from_argv():
    """取命令行 `--` 之后的目录参数。"""
    if "--" in sys.argv:
        rest = [a for a in sys.argv[sys.argv.index("--") + 1:] if a.strip()]
        return rest[0] if rest else None
    return None


if __name__ == "__main__":
    start(_folder_from_argv())
