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
DEFAULT_ENV = {"wall": "#d0d0ce", "floor": "#6f4a2e", "light": 600}  # 淡灰墙 / 棕地板 / 布光强度


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


def z_range(item):
    """物体在 Z 方向的占据区间 (底, 顶)，毫米。"""
    z0 = float(item.get("z", 0.0) or 0.0)
    return z0, z0 + item["bbox"]["h"] * item.get("scale", 1.0)


def validate(items, room):
    """检查越界、悬空与重叠（3D：Z 区间不重叠则不算穿插）。"""
    issues = []
    boxes = []
    for it in items:
        label = it.get("label") or it.get("component_id", "?")[:16]
        name = f"{label}#{it.get('instance', 1)}"
        if "bbox" not in it:
            issues.append(f"{label}: 缺少包围盒，无法校验")
            continue
        r = rect_of(it, it["bbox"])
        z0, z1 = z_range(it)
        boxes.append((name, r, z0, z1))
        if r[0] < -TOL or r[1] < -TOL or r[2] > room["w"] + TOL or r[3] > room["d"] + TOL:
            issues.append(
                f"{label}: 越界 x[{r[0]:.0f},{r[2]:.0f}] y[{r[1]:.0f},{r[3]:.0f}]，"
                f"房间 {room['w']:.0f}x{room['d']:.0f}mm")
        if z0 < -TOL:
            issues.append(f"{label}: 低于地面 {z0:.0f}mm")
        if z1 > room.get("h", 2800) + TOL:
            issues.append(f"{label}: 超过层高 {z1:.0f}mm")
        if z0 > TOL and not it.get("on"):
            issues.append(f"{label}: 悬空 {z0:.0f}mm（未声明支撑物）")

    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            ni, ri, zi0, zi1 = boxes[i]
            nj, rj, zj0, zj1 = boxes[j]
            # Z 方向没有交集（允许一件坐在另一件上）
            if min(zi1, zj1) - max(zi0, zj0) <= OVERLAP_MIN:
                continue
            area = overlap_area(ri, rj)
            if area > OVERLAP_MIN:
                issues.append(f"{ni} 与 {nj} 重叠 {area / 1e6:.2f} 平方米")
    return issues


def resolve_support(items):
    """按 on 关系算出每个物体的底面高度 z（毫米）。

    规则：只允许一层支撑；支撑物必须是同一布局里的其它物体，且两者 XY 有交集。
    不合法（找不到、自己撑自己、无交集）时按落地处理，避免悬空。
    """
    index = {f"{it.get('label')}#{it.get('instance', 1)}": it for it in items}
    for it in items:
        it["z"] = 0.0
    for it in items:
        ref = it.get("on")
        target = index.get(str(ref)) if ref else None
        if target is None or target is it:
            it.pop("on", None)
            continue
        if overlap_area(rect_of(it, it["bbox"]), rect_of(target, target["bbox"])) <= OVERLAP_MIN:
            it.pop("on", None)
            continue
        # 支撑物自身不能再被支撑，保证只有一层
        if target.get("on"):
            target.pop("on", None)
            target["z"] = 0.0
        it["z"] = z_range(target)[1]
    return items


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
        it["rot_z"], it["scale"], it["z"] = 0, 1.0, 0.0
        it.pop("on", None)
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


def _srgb_to_linear(v):
    """sRGB 0-255 -> Blender 线性颜色分量。"""
    c = v / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _hex_color(text, default):
    """'#rrggbb' -> 线性 RGB；非法或为空时用默认值。"""
    if not text:
        return default
    t = str(text).lstrip("#")
    if len(t) != 6:
        return default
    try:
        rgb = tuple(int(t[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return default
    return tuple(_srgb_to_linear(v) for v in rgb)


def _add_room(room, env=None, wall_color=(0.86, 0.84, 0.79), floor_color=(0.55, 0.42, 0.32)):
    """地面 + 背墙 + 左墙（米制，房间中心为原点）；颜色可由 env 覆盖。"""
    env = env or {}
    wall_color = _hex_color(env.get("wall") or DEFAULT_ENV["wall"], wall_color)
    floor_color = _hex_color(env.get("floor") or DEFAULT_ENV["floor"], floor_color)
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
    """按布局 JSON 建场景，返回放置结果（含实际世界包围盒）。

    每个组件导入后先挂到一个「枢轴空物体」上，枢轴原点放在组件包围盒的底面中心，
    再整体平移/旋转/缩放。这样既能保留 GLB 里的归一化偏移，也保证旋转围绕自身中心，
    不会甩到房间外或陷进地面。
    """
    import bpy
    from .normalize import world_bounds
    from .rebuild import ensure

    room = layout["room"]
    _clear_scene()
    _add_room(room, layout.get("env"))

    placed = []
    for it in layout["items"]:
        info = ensure(it["component_id"])
        roots = _import_glb(info["glb"])
        bpy.context.view_layer.update()
        lo, hi = world_bounds(roots)
        scale = it.get("scale", 1.0)
        x = (it["x"] - room["w"] / 2) / 1000.0
        y = (it["y"] - room["d"] / 2) / 1000.0
        z = float(it.get("z", 0.0) or 0.0) / 1000.0

        # 枢轴放在包围盒底面中心：之后旋转/缩放都绕这个点
        pivot = bpy.data.objects.new(f"{it.get('label', 'item')}_{it.get('instance', 1)}_pivot", None)
        bpy.context.scene.collection.objects.link(pivot)
        pivot.location = ((lo.x + hi.x) / 2.0, (lo.y + hi.y) / 2.0, lo.z)
        bpy.context.view_layer.update()
        for root in roots:
            root.parent = pivot
            root.matrix_parent_inverse = pivot.matrix_world.inverted()
            root.name = f"{it.get('label', 'item')}_{root.name}"

        pivot.location = (x, y, z)
        pivot.rotation_euler = (0.0, 0.0, math.radians(it.get("rot_z", 0)))
        pivot.scale = (scale, scale, scale)
        placed.append({
            "label": it.get("label"),
            "component_id": it["component_id"],
            "roots": [r.name for r in roots],
            "pivot": pivot.name,
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
