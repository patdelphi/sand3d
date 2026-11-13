"""DashScope 能力探测脚本。

用途：
  1. 验证 OpenAI 兼容端点的 JSON 模式是否可用
  2. 验证模型是否接受图片输入（视觉反馈环的前提）
  3. 用「客厅三件家具」跑一次布局生成，检查输出能否通过边界/重叠校验

只读取根目录 .env，不打印任何密钥。
运行：.venv\\Scripts\\python scripts\\probe_dashscope.py
"""
import json
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"


def load_env(path):
    """解析 .env 到字典，忽略注释与空行。"""
    env = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*([A-Za-z0-9_]+)\s*=\s*(.*)$", line)
        if m:
            env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return env


def post(base, key, payload, timeout=60):
    """统一 POST 封装，返回 (status, json_or_text)。"""
    try:
        r = requests.post(
            base + "/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json=payload,
            timeout=timeout,
        )
        if r.ok:
            return r.status_code, r.json()
        return r.status_code, r.text[:300]
    except Exception as exc:  # 网络异常必须显式报告
        return None, f"{type(exc).__name__}: {str(exc)[:200]}"


def probe_json_mode(base, key, model):
    """探测 response_format=json_object 是否被支持。"""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": '输出 JSON：{"ok": true}'}],
        "response_format": {"type": "json_object"},
        "max_tokens": 50,
    }
    status, data = post(base, key, payload)
    if status != 200:
        return f"FAIL status={status} {data}"
    try:
        text = data["choices"][0]["message"]["content"]
        json.loads(text)
        return f"PASS 返回可解析 JSON: {text[:60]}"
    except Exception as exc:
        return f"FAIL 返回不可解析: {type(exc).__name__} {str(text)[:80]}"


def _solid_png(w, h, rgb):
    """生成纯色 PNG 的 base64 data URI，用于视觉探测（模型要求宽高 > 10px）。"""
    import base64
    import struct
    import zlib

    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return "data:image/png;base64," + base64.b64encode(png).decode()


def probe_vision(base, key, model):
    """用 64x64 纯红色图探测视觉输入支持。"""
    png = _solid_png(64, 64, (220, 20, 20))
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "这张图是什么颜色？只回答一个词。"},
                    {"type": "image_url", "image_url": {"url": png}},
                ],
            }
        ],
        "max_tokens": 20,
    }
    status, data = post(base, key, payload)
    if status != 200:
        return f"FAIL status={status} {data}"
    return f"PASS {data['choices'][0]['message']['content'][:40]}"


LAYOUT_PROMPT = """你是室内布局设计师。请在 4000mm(宽) x 5000mm(深) 的矩形房间里摆放以下物体。
坐标系：左下角为原点，X 向右，Y 向上（平面图视角），地面 z=0，单位毫米，角度单位为度。
物体：
- sofa: 宽 2000, 深 900, 高 800
- coffee_table: 宽 1000, 深 500, 高 450
- floor_lamp: 宽 400, 深 400, 高 1500
约束：
1. 所有物体必须完整位于房间内（含边界，容差 0）
2. 物体之间不得重叠
3. 沙发靠墙摆放
4. 茶几在沙发正前方，与沙发中心对齐
5. 落地灯在沙发一侧
6. rot_deg 只能是 0/90/180/270
只输出 JSON 数组，不要额外文字。格式：
[{"object": "sofa", "x": 中心x, "y": 中心y, "rot_deg": 0, "reason": "一句理由"}]
"""


def rect_of(item, size):
    """把中心点+尺寸+旋转转换为轴对齐矩形 (x0,y0,x1,y1)。"""
    w, d = size
    if item["rot_deg"] in (90, 270):
        w, d = d, w
    return (
        item["x"] - w / 2,
        item["y"] - d / 2,
        item["x"] + w / 2,
        item["y"] + d / 2,
    )


def overlap(a, b):
    """判断两个矩形是否重叠（含边界接触不算重叠）。"""
    return min(a[2], b[2]) - max(a[0], b[0]) > 0 and min(a[3], b[3]) - max(a[1], b[1]) > 0


def probe_layout(base, key, model):
    """生成布局并做边界/重叠校验。"""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": LAYOUT_PROMPT}],
        "temperature": 0.2,
        "max_tokens": 800,
    }
    status, data = post(base, key, payload, timeout=120)
    if status != 200:
        return f"FAIL status={status} {data}"
    text = data["choices"][0]["message"]["content"]
    m = re.search(r"\[[\s\S]*\]", text)
    if not m:
        return f"FAIL 未找到 JSON 数组: {text[:120]}"
    try:
        items = json.loads(m.group(0))
    except Exception as exc:
        return f"FAIL JSON 解析失败: {exc}"

    sizes = {"sofa": (2000, 900), "coffee_table": (1000, 500), "floor_lamp": (400, 400)}
    issues = []
    rects = {}
    for it in items:
        name = it.get("object")
        if name not in sizes:
            issues.append(f"未知物体 {name}")
            continue
        if it.get("rot_deg") not in (0, 90, 180, 270):
            issues.append(f"{name} 角度非法 {it.get('rot_deg')}")
        r = rect_of(it, sizes[name])
        rects[name] = r
        if r[0] < 0 or r[1] < 0 or r[2] > 4000 or r[3] > 5000:
            issues.append(f"{name} 越界 {tuple(round(v) for v in r)}")
    names = list(rects)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            if overlap(rects[names[i]], rects[names[j]]):
                issues.append(f"{names[i]} 与 {names[j]} 重叠")

    print("布局原始输出:", text.strip()[:300])
    if issues:
        return "FAIL 校验问题: " + "; ".join(issues)
    return f"PASS {len(items)} 件物体，无越界无重叠"


def main():
    env = load_env(ENV_PATH)
    base = env.get("AI_PROVIDER_1_BASE_URL")
    key = env.get("AI_PROVIDER_1_API_KEY")
    model = env.get("AI_MODEL_1_1_NAME")
    if not (base and key and model):
        print("缺少 provider 1 配置，退出")
        return 1

    print(f"provider=dashscope base={base} model={model}")
    print("JSON 模式:", probe_json_mode(base, key, model))
    print("视觉输入:", probe_vision(base, key, model))
    print("布局生成:", probe_layout(base, key, model))
    return 0


if __name__ == "__main__":
    sys.exit(main())
