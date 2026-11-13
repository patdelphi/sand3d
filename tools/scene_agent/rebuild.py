"""组件重建：执行数据集里的 sand3d 程序，产出 GLB + 包围盒，并落缓存。

设计：
  父进程只做调度与缓存判断；每个组件在独立子进程里用 bpy 执行，60~120 秒超时强杀，
  避免一个坏程序拖垮整个流程。
  缓存键 = (id, program_sha256, sand3d_version)，命中且文件存在就不重建。

用法：
  .venv\\Scripts\\python -m tools.scene_agent.rebuild --worker <component_id>
  from tools.scene_agent.rebuild import ensure
  ensure("objaverse-...")
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime

from . import CACHE_DIR, DB_PATH, ROOT, SRC_DIR

# 程序静态安全门：数据集里只出现 sand3d 与 math，其余 import 一律拒绝
RE_IMPORT = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][\w.]*)", re.M)
ALLOWED_IMPORTS = {"sand3d", "math"}
BANNED_CALLS = ("__import__(", "eval(", "exec(", "compile(", "open(", "input(")

RESULT_MARK = "S3D_RESULT "


def check_program(program):
    """静态检查程序文本，不通过则抛 ValueError。"""
    for name in RE_IMPORT.findall(program or ""):
        top = name.split(".")[0]
        if top not in ALLOWED_IMPORTS:
            raise ValueError(f"不允许的 import: {name}")
    for token in BANNED_CALLS:
        if token in program:
            raise ValueError(f"不允许的调用: {token}")
    return True


def connect():
    """打开索引库（WAL）。"""
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;")
    return con


def load_component(component_id, con=None):
    """从索引库取程序与元数据。"""
    own = con is None
    con = con or connect()
    try:
        row = con.execute(
            "SELECT id, program, entry, program_sha256, sand3d_version, license, attribution, source"
            " FROM components WHERE id = ?", (component_id,)).fetchone()
    finally:
        if own:
            con.close()
    if not row:
        raise KeyError(f"未知组件: {component_id}")
    keys = ("id", "program", "entry", "program_sha256", "sand3d_version", "license", "attribution", "source")
    return dict(zip(keys, row))


def cache_row(component_id, con=None):
    """读缓存记录（可能为 None）。"""
    own = con is None
    con = con or connect()
    try:
        row = con.execute(
            "SELECT glb_path, blend_path, bbox_json, status, error, program_sha256, sand3d_version, seconds"
            " FROM rebuild_cache WHERE id = ?", (component_id,)).fetchone()
    finally:
        if own:
            con.close()
    if not row:
        return None
    keys = ("glb_path", "blend_path", "bbox_json", "status", "error", "program_sha256", "sand3d_version", "seconds")
    return dict(zip(keys, row))


def cache_fresh(comp, cache):
    """缓存是否可直接复用：程序指纹一致、状态 ok、文件还在。"""
    return bool(
        cache and cache["status"] == "ok"
        and cache["program_sha256"] == comp["program_sha256"]
        and cache["sand3d_version"] == comp["sand3d_version"]
        and cache["glb_path"] and os.path.exists(cache["glb_path"])
        and cache["bbox_json"]
    )


def write_cache(component_id, comp, payload):
    """写缓存行；索引进程占用写锁时重试，避免整轮失败。"""
    sql = (
        "INSERT OR REPLACE INTO rebuild_cache"
        " (id, program_sha256, sand3d_version, glb_path, blend_path, bbox_json, status, error, seconds, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)"
    )
    params = (
        component_id, comp.get("program_sha256"), comp.get("sand3d_version"),
        payload.get("glb"), payload.get("blend"), payload.get("bbox_json"),
        payload.get("status", "error"), payload.get("error"), payload.get("seconds"),
        datetime.now().isoformat(timespec="seconds"),
    )
    for attempt in range(6):
        con = connect()
        try:
            with con:
                con.execute(sql, params)
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) or attempt == 5:
                raise
            time.sleep(3 * (attempt + 1))
        finally:
            con.close()


def ensure(component_id, force=False, timeout=180):
    """确保组件已重建；返回 {glb, bbox, status, cached}。"""
    comp = load_component(component_id)
    if not force:
        cache = cache_row(component_id)
        if cache_fresh(comp, cache):
            return {"glb": cache["glb_path"], "bbox": json.loads(cache["bbox_json"]),
                    "status": "ok", "cached": True}
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC_DIR + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    t0 = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "tools.scene_agent.rebuild", "--worker", component_id],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout,
        )
        out = proc.stdout or ""
        line = next((ln for ln in reversed(out.splitlines()) if ln.startswith(RESULT_MARK)), None)
        if not line:
            raise RuntimeError(f"子进程未返回结果（exit={proc.returncode}）：{(proc.stderr or out)[-500:]}")
        payload = json.loads(line[len(RESULT_MARK):])
    except subprocess.TimeoutExpired:
        payload = {"status": "error", "error": f"重建超时 {timeout}s"}
    except Exception as exc:  # 子进程崩溃、解析失败等
        payload = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    payload["seconds"] = round(time.time() - t0, 1)
    write_cache(component_id, comp, payload)
    if payload.get("status") != "ok":
        raise RuntimeError(f"重建 {component_id} 失败：{payload.get('error')}")
    return {"glb": payload["glb"], "bbox": json.loads(payload["bbox_json"]), "status": "ok", "cached": False}


def worker(component_id):
    """子进程入口：执行程序、归一化、导出 GLB 与 .blend。"""
    sys.path.insert(0, SRC_DIR)
    import bpy  # noqa: F401  bpy 必须在其他模块前导入

    import sand3d as s3d
    from . import normalize

    comp = load_component(component_id)
    check_program(comp["program"])
    os.makedirs(CACHE_DIR, exist_ok=True)
    glb_path = os.path.join(CACHE_DIR, f"{component_id}.glb")
    blend_path = os.path.join(CACHE_DIR, f"{component_id}.blend")

    t0 = time.time()
    s3d.empty_scene()
    namespace = {"__name__": "sand3d_program", "__file__": f"<dataset:{component_id}>"}
    exec(compile(comp["program"], f"<{component_id}>", "exec"), namespace)

    # entry 优先；没有 entry 的程序（如 cad）把顶层对象当作结果
    if comp["entry"] and callable(namespace.get(comp["entry"])):
        result = namespace[comp["entry"]]()
        root = result.root if hasattr(result, "root") else result
    else:
        roots = [ob for ob in bpy.context.scene.objects if ob.parent is None]
        if not roots:
            raise RuntimeError("程序没有产出任何根对象")
        root = roots[0] if len(roots) == 1 else s3d.group(component_id, *roots)

    bbox = normalize.drop_and_center([root])
    s3d.export(root, glb_path)
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)
    payload = {
        "status": "ok",
        "glb": glb_path,
        "blend": blend_path,
        "bbox_json": json.dumps(bbox, ensure_ascii=False),
        "seconds": round(time.time() - t0, 1),
    }
    print(RESULT_MARK + json.dumps(payload, ensure_ascii=False))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="组件重建与缓存")
    ap.add_argument("--worker", help="内部使用：在子进程里重建单个组件")
    ap.add_argument("--id", action="append", help="批量重建指定组件 id")
    ap.add_argument("--force", action="store_true", help="忽略缓存强制重建")
    args = ap.parse_args(argv)
    if args.worker:
        return worker(args.worker)
    if args.id:
        for cid in args.id:
            try:
                info = ensure(cid, force=args.force)
                print(f"OK   {cid}  {info['bbox']}  cached={info['cached']}")
            except Exception as exc:
                print(f"FAIL {cid}  {exc}")
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
