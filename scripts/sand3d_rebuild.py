"""抽样验证：按数据集 README 的方法重建 5 个样本，导出 USDA/GLB。

只依赖 bpy + 本地 src/sand3d；统计网格顶点、三角面与包围盒。
"""
import hashlib
import json
import os
import sys
import time

import bpy

sys.path.insert(0, r"c:\Users\patde\Documents\GitHub\sand3d\src")
import sand3d as s3d

SAMPLES = r"C:\Users\patde\AppData\Local\Temp\sand3d_samples"
OUT = r"C:\Users\patde\AppData\Local\Temp\sand3d_rebuild"


def mesh_stats(root):
    """遍历层级收集网格统计（单位：米）。"""
    verts = tris = 0
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    stack = [root]
    while stack:
        ob = stack.pop()
        stack.extend(ob.children)
        if ob.type != "MESH":
            continue
        me = ob.data
        verts += len(me.vertices)
        tris += sum(len(p.vertices) - 2 for p in me.polygons)
        for v in me.vertices:
            world = ob.matrix_world @ v.co
            for i in range(3):
                lo[i] = min(lo[i], world[i])
                hi[i] = max(hi[i], world[i])
    return {"verts": verts, "tris": tris,
            "size": [round(hi[i] - lo[i], 4) for i in range(3)] if verts else []}


def main():
    os.makedirs(OUT, exist_ok=True)
    report = {}
    for name in ["sand3d-objaverse", "sand3d-cad", "sand3d-articraft",
                 "sand3d-automate", "sand3d-simready"]:
        program = open(os.path.join(SAMPLES, name + ".py"), encoding="utf-8").read()
        entry = {"sand3d-objaverse": "scalloped_slab", "sand3d-cad": None,
                 "sand3d-articraft": "wall_task_lamp",
                 "sand3d-automate": "adjustable_rail_assembly",
                 "sand3d-simready": "triangle_ruler"}[name]
        t0 = time.time()
        try:
            bpy.ops.wm.read_factory_settings(use_empty=True)
            ns = {}
            exec(compile(program, name + ".py", "exec"), ns)
            if entry:
                obj = ns[entry]()
            else:
                roots = [o for o in bpy.context.scene.objects if o.parent is None]
                obj = roots[0] if len(roots) == 1 else s3d.group("object", *roots)
            stats = mesh_stats(obj.root if hasattr(obj, "root") else obj)
            usda = os.path.join(OUT, name + ".usda")
            glb = os.path.join(OUT, name + ".glb")
            s3d.export(obj, usda)
            s3d.export(obj, glb)
            with open(usda, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
            report[name] = {"ok": True, "stats": stats, "usda_sha256": digest,
                            "usda_bytes": os.path.getsize(usda),
                            "glb_bytes": os.path.getsize(glb),
                            "seconds": round(time.time() - t0, 1)}
        except Exception as exc:  # 任何程序失败都要记录，不能中断其他样本
            report[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                            "seconds": round(time.time() - t0, 1)}
        print(name, json.dumps(report[name], ensure_ascii=False), flush=True)
    with open(os.path.join(OUT, "report.json"), "w", encoding="utf-8-sig") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
