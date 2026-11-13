"""组件归一化：包围盒计算与「落地、XY 居中」。

约定：程序内部单位是毫米（sand3d 默认），场景 Z 轴向上。
归一化后组件的最低价在 z=0，包围盒 XY 中心在原点，正面默认 +Y。
"""
from mathutils import Vector


def world_bounds(objects):
    """返回 (min, max) 世界坐标包围盒（按求值后的网格顶点计算，含修改器）。

    objects 可以是根对象（空物体），会递归展开其子对象。
    """
    import bpy

    depsgraph = bpy.context.evaluated_depsgraph_get()
    lo = Vector((float("inf"),) * 3)
    hi = Vector((float("-inf"),) * 3)
    found = False
    expanded = []
    for ob in objects:
        expanded.extend([ob] + list(ob.children_recursive))
    for ob in expanded:
        if ob.type != "MESH":
            continue
        evaluated = ob.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            matrix = evaluated.matrix_world
            for vert in mesh.vertices:
                p = matrix @ vert.co
                for i in range(3):
                    lo[i] = min(lo[i], p[i])
                    hi[i] = max(hi[i], p[i])
                found = True
        finally:
            evaluated.to_mesh_clear()
    if not found:
        raise ValueError("归一化失败：场景里没有网格对象")
    return lo, hi


def bbox_mm(lo, hi):
    """包围盒 -> 毫米尺寸字典。"""
    return {
        "w": round(hi.x - lo.x, 3),
        "d": round(hi.y - lo.y, 3),
        "h": round(hi.z - lo.z, 3),
        "min": [round(v, 3) for v in lo],
        "max": [round(v, 3) for v in hi],
    }


def drop_and_center(roots):
    """把根对象整体平移：包围盒 XY 居中、最低点落到 z=0；返回包围盒。"""
    lo, hi = world_bounds(roots)
    delta = Vector((-(lo.x + hi.x) / 2.0, -(lo.y + hi.y) / 2.0, -lo.z))
    for root in roots:
        root.location = root.location + delta
    return bbox_mm(lo + delta, hi + delta)
