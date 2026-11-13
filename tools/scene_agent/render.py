"""无头渲染：Cycles CPU + 三点布光 + 等距相机。

硬件无关：不依赖显示设备，EEVEE 不用。
"""
import math
import os
import time


def _look_at(camera, target):
    """让相机朝向 target（用 TRACK_TO 约束，避免手算欧拉角）。"""
    import bpy
    from mathutils import Vector
    empty = bpy.data.objects.new("cam_target", None)
    bpy.context.scene.collection.objects.link(empty)
    empty.location = Vector(target)
    con = camera.constraints.new("TRACK_TO")
    con.target = empty
    con.track_axis = "TRACK_NEGATIVE_Z"
    con.up_axis = "UP_Y"


def add_camera(room, preset="iso", lens=35):
    """按预设放相机：iso 俯视、front 正视、top 俯视。"""
    import bpy
    w = room["w"] / 1000.0
    d = room["d"] / 1000.0
    h = room.get("h", 2800) / 1000.0
    cam_data = bpy.data.cameras.new("camera")
    cam_data.lens = lens
    cam = bpy.data.objects.new("camera", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    bpy.context.scene.camera = cam
    target = (0.0, 0.0, h * 0.28)
    if preset == "top":
        cam.location = (0.0, 0.0, max(w, d) * 1.6)
    elif preset == "front":
        cam.location = (0.0, -max(w, d) * 1.35, h * 0.9)
    else:  # iso：从 +X -Y 方向斜上方看（两面墙在 +Y 与 -X，不会挡镜头）
        cam.location = (max(w, d) * 0.95, -max(w, d) * 1.15, max(w, d) * 1.0)
    _look_at(cam, target)
    return cam


def add_lights(room, energy=1200.0):
    """三点布光：主光 / 补光 / 轮廓光，外加低强度环境光。"""
    import bpy
    w = room["w"] / 1000.0
    d = room["d"] / 1000.0
    h = room.get("h", 2800) / 1000.0
    specs = [
        ("key", (-w * 0.6, -d * 0.5, h * 1.25), energy),
        ("fill", (w * 0.7, -d * 0.6, h * 0.9), energy * 0.35),
        ("rim", (w * 0.2, d * 0.8, h * 1.1), energy * 0.5),
    ]
    for name, loc, power in specs:
        light = bpy.data.lights.new(name, "AREA")
        light.energy = power
        light.size = max(w, d) * 0.8
        ob = bpy.data.objects.new(name, light)
        ob.location = loc
        ob.visible_camera = False   # 面光不能被相机直接看到，否则会挡住整个画面
        bpy.context.scene.collection.objects.link(ob)
        _look_at(ob, (0.0, 0.0, 0.4))
    world = bpy.data.worlds.new("world") if not bpy.context.scene.world else bpy.context.scene.world
    bpy.context.scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs["Color"].default_value = (0.92, 0.92, 0.95, 1.0)
        bg.inputs["Strength"].default_value = 0.35


def setup(room, preset="iso", width=768, height=768, samples=32):
    """相机 + 灯光 + Cycles CPU 参数。"""
    import bpy
    scene = bpy.context.scene
    add_camera(room, preset)
    add_lights(room)
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = samples
    scene.cycles.use_denoising = True
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    scene.render.filepath = ""
    scene.render.image_settings.file_format = "PNG"
    # 色彩管理：新版用 AgX，旧版退回 Filmic
    try:
        scene.view_settings.view_transform = "AgX"
    except TypeError:
        scene.view_settings.view_transform = "Filmic"
    return scene


def render(path, scene=None):
    """渲染到 PNG，返回 (路径, 秒数)。"""
    import bpy
    scene = scene or bpy.context.scene
    os.makedirs(os.path.dirname(path), exist_ok=True)
    scene.render.filepath = path
    t0 = time.time()
    bpy.ops.render.render(write_still=True)
    return path, round(time.time() - t0, 1)
