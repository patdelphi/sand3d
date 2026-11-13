"""布局校验与场景拼装。

坐标约定（与 LLM 协议一致）：
  房间左下角为原点，X 向右、Y 向上（俯视），单位毫米；rot_z 单位为度。
  Blender 场景里统一用米：房间中心在原点，物体位置 = ((x - w/2)/1000, (y - d/2)/1000, 0)。

校验是纯 Python 的（不依赖 bpy），构建才需要 bpy。
"""
import json
import math
import os

TOL = 5.0          # 边界容差，毫米
OVERLAP_MIN = 5.0  # 小于 5mm 的相交视为贴边，不算冲突


def footprint(bbox, rot_deg, scale=1.0):
    """按旋转与缩放算占地尺寸 (w, d)，毫米。"""
    w = bbox["w"] * scale
    d = bbox["d"] * scale
    if int(round(rot_deg)) % 180 == 90:
        w, d = d, w
    return w, d


def rect_of(item, bbox):
    """物体占地矩形 (x0, y0, x1, y1)，毫米。"""
    w, d = footprint(bbox, item.get("rot_z", 0), item.get("scale", 1.0))
    x, y = item["x"], item["y"]
    return (x - w / 2, y - d / 2, x + w / 2, y + d / 2)


def overlap_area(a, b):
    """两个轴对齐矩形的重叠面积（不重叠为 0）。"""
    dx = min(a[2], b[2]) - max(a[0], b[0])
    dy = min(a[3], b[3]) - max(a[1], b[1])
    if dx <= 0 or dy <= 0:
        return 0.0
    return dx * dy


def validate(items, room):
    """检查越界与重叠；返回问题列表（空列表即通过）。"""
    issues = []
    rects = {}
    for it in items:
        label = it.get("label") or it.get("component_id", "?")[:16]
        if "bbox" not in it:
            issues.append(f"{label}: 缺少包围盒，无法校验")
            continue
        r = rect_of(it, it["bbox"])
        rects[label] = r
        if r[0] < -TOL or r[1] < -TOL or r[2] > room["w"] + TOL or r[3] > room["d"] + TOL:
            issues.append(
                f"{label}: 越界 x[{r[0]:.0f},{r[2]:.0f}] y[{r[1]:.0f},{r[3]:.0f}]，"
                f"房间 {room['w']:.0f}x{room['d']:.0f}mm")
        if it.get("z", 0) != 0:
            issues.append(f"{label}: z 必须为 0（落地），当前 {it.get('z')}")
    labels = list(rects)
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            area = overlap_area(rects[labels[i]], rects[labels[j]])
            if area > OVERLAP_MIN:
                issues.append(f"{labels[i]} 与 {labels[j]} 重叠 {area / 1e6:.2f} 平方米")
    return issues


def clamp(items, room):
    """把每个物体夹回房间内部（贴边），保证不越界。"""
    for it in items:
        w, d = footprint(it["bbox"], it.get("rot_z", 0), it.get("scale", 1.0))
        lo_x, hi_x = w / 2, room["w"] - w / 2
        lo_y, hi_y = d / 2, room["d"] - d / 2
        it["x"] = lo_x if hi_x < lo_x else min(max(it["x"], lo_x), hi_x)
        it["y"] = lo_y if hi_y < lo_y else min(max(it["y"], lo_y), hi_y)
    return items


def fallback_layout(items, room, gap=150.0, margin=80.0):
    """兜底布局：按面积从大到小，沿墙一排排摆放，保证不越界、不重叠。"""
    order = sorted(items, key=lambda it: -(it["bbox"]["w"] * it["bbox"]["d"]))
    x, y, row_depth = margin, room["d"] - margin, 0.0
    for it in order:
        it["rot_z"], it["scale"] = 0, 1.0
        w, d = footprint(it["bbox"], 0, 1.0)
        if x + w > room["w"] - margin and x > margin:
            y -= row_depth + gap
            x, row_depth = margin, 0.0
        it["x"] = x + w / 2
        it["y"] = y - d / 2
        x += w + gap
        row_depth = max(row_depth, d)
    return clamp(order, room)


# ---------------------------------------------------------------------------
# 以下需要 bpy
# ---------------------------------------------------------------------------

def _clear_scene():
    """清空场景，回到空工程。"""
    import bpy
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _make_mesh_object(name, verts, faces, color, roughness=0.8):
    """由顶点/面直接建一个带材质的网格对象。"""
    import bpy
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    ob = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(ob)
    mat = bpy.data.materials.new(f"{name}_mat")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    ob.data.materials.append(mat)
    return ob


def _add_room(room, wall_color=(0.86, 0.84, 0.79), floor_color=(0.55, 0.42, 0.32)):
    """地面 + 背墙 + 左墙（米制，房间中心为原点）。"""
    w = room["w"] / 1000.0
    d = room["d"] / 1000.0
    h = room.get("h", 2800) / 1000.0
    # 地板：略低于 0，避免与家具共面闪烁
    _make_mesh_object(
        "room_floor",
        [(-w / 2, -d / 2, -0.02), (w / 2, -d / 2, -0.02), (w / 2, d / 2, -0.02), (-w / 2, d / 2, -0.02)],
        [(0, 1, 2, 3)], floor_color, roughness=0.9)
    # 背墙（y = +d/2）与左墙（x = -w/2），单面即可
    _make_mesh_object(
        "room_wall_back",
        [(-w / 2, d / 2, 0), (w / 2, d / 2, 0), (w / 2, d / 2, h), (-w / 2, d / 2, h)],
        [(0, 1, 2, 3)], wall_color, roughness=0.95)
    _make_mesh_object(
        "room_wall_left",
        [(-w / 2, -d / 2, 0), (-w / 2, d / 2, 0), (-w / 2, d / 2, h), (-w / 2, -d / 2, h)],
        [(0, 3, 2, 1)], wall_color, roughness=0.95)


def _import_glb(path):
    """导入 GLB，返回新加入的根对象列表。"""
    import bpy
    before = {ob.name for ob in bpy.context.scene.objects}
    bpy.ops.import_scene.gltf(filepath=str(path))
    new = [ob for ob in bpy.context.scene.objects if ob.name not in before]
    roots = [ob for ob in new if ob.parent is None]
    return roots or new


def build_scene(layout):
    """按布局 JSON 建场景，返回放置结果（含实际世界包围盒）。"""
    import bpy
    from .rebuild import ensure

    room = layout["room"]
    _clear_scene()
    _add_room(room)

    placed = []
    for it in layout["items"]:
        info = ensure(it["component_id"])
        roots = _import_glb(info["glb"])
        x = (it["x"] - room["w"] / 2) / 1000.0
        y = (it["y"] - room["d"] / 2) / 1000.0
        scale = it.get("scale", 1.0)
        for root in roots:
            root.location = (x, y, 0.0)
            root.rotation_euler = (0.0, 0.0, math.radians(it.get("rot_z", 0)))
            # GLB 根节点自带 0.001（毫米 -> 米），必须相乘而不是覆盖
            base = root.scale.copy()
            root.scale = (base.x * scale, base.y * scale, base.z * scale)
            root.name = f"{it.get('label', 'item')}_{root.name}"
        placed.append({
            "label": it.get("label"),
            "component_id": it["component_id"],
            "roots": [r.name for r in roots],
            "bbox_mm": info["bbox"],
            "glb": info["glb"],
        })
    bpy.context.view_layer.update()
    return placed


def export_scene(path):
    """保存 .blend 或导出 .glb（按扩展名）。"""
    import bpy
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if path.lower().endswith(".blend"):
        bpy.ops.wm.save_as_mainfile(filepath=path)
    elif path.lower().endswith(".glb"):
        bpy.ops.export_scene.gltf(filepath=path, export_format="GLB")
    else:
        raise ValueError(f"不支持的导出格式: {path}")
    return path


def write_layout(layout, path):
    """把布局 JSON 落盘（UTF-8，CRLF）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = json.dumps(layout, ensure_ascii=False, indent=2).replace("\n", "\r\n")
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(text)
    return path
