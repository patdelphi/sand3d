"""LLM 调用（DashScope 的 OpenAI 兼容端点）。

只用 requests，不引入额外 SDK；密钥只从根目录 .env 读取，永不打印。
"""
import base64
import json
import os
import re
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENV_PATH = os.path.join(ROOT, ".env")


def load_env(path=ENV_PATH):
    """解析 .env 为字典（忽略注释与空行）。"""
    env = {}
    if not os.path.exists(path):
        return env
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^\s*([A-Za-z0-9_]+)\s*=\s*(.*)$", line)
            if m:
                env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return env


def extract_json(text):
    """从模型输出里抠出第一个完整 JSON 对象/数组（容忍 ```json 围栏与前后废话）。"""
    if not text:
        raise ValueError("模型返回为空")
    text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text.strip(), flags=re.M)
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0] or [-1])
    if start < 0:
        raise ValueError(f"未找到 JSON：{text[:120]}")
    opening = text[start]
    closing = "}" if opening == "{" else "]"
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == opening:
            depth += 1
        elif ch == closing:
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError(f"JSON 不完整：{text[:120]}")


class Chat:
    """一次会话内的简单封装：文本、JSON、视觉三种调用。"""

    def __init__(self, env=None):
        env = env or load_env()
        self.base = env.get("AI_PROVIDER_1_BASE_URL", "").rstrip("/")
        self.key = env.get("AI_PROVIDER_1_API_KEY", "")
        self.model = env.get("AI_MODEL_1_1_NAME", "")
        if not (self.base and self.key and self.model):
            raise RuntimeError("缺少 AI_PROVIDER_1_BASE_URL / API_KEY / AI_MODEL_1_1_NAME")

    def _post(self, payload, timeout=120, retries=2):
        """POST /chat/completions，失败重试一次。"""
        url = self.base + "/chat/completions"
        last = None
        for attempt in range(retries + 1):
            try:
                resp = requests.post(
                    url, headers={"Authorization": f"Bearer {self.key}"},
                    json=payload, timeout=timeout)
                if resp.ok:
                    return resp.json()["choices"][0]["message"]["content"]
                last = f"HTTP {resp.status_code}: {resp.text[:200]}"
                if resp.status_code < 500:
                    break
            except Exception as exc:  # 网络异常
                last = f"{type(exc).__name__}: {exc}"
            time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"LLM 调用失败：{last}")

    def text(self, prompt, system=None, temperature=0.2, max_tokens=1200, json_mode=False):
        """纯文本 / JSON 输出。"""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = {"model": self.model, "messages": messages,
                   "temperature": temperature, "max_tokens": max_tokens,
                   "enable_thinking": False}   # 关闭思维链，延迟从 ~200s 降到几秒
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return self._post(payload)

    def json(self, prompt, system=None, temperature=0.2, max_tokens=1500):
        """要求 JSON 输出并解析；解析失败时把错误回喂重试一次。"""
        text = self.text(prompt, system, temperature, max_tokens, json_mode=True)
        try:
            return extract_json(text)
        except Exception as exc:
            retry = (f"你的输出无法解析为 JSON（{exc}）。只输出 JSON，不要解释。\n"
                     f"原输出：{text[:600]}")
            text2 = self.text(retry, system, temperature, max_tokens, json_mode=True)
            return extract_json(text2)

    def vision(self, png_path, prompt, system=None, max_tokens=800):
        """把本地 PNG 转 data URI 后提问。"""
        with open(png_path, "rb") as fh:
            data = base64.b64encode(fh.read()).decode()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}},
        ]})
        payload = {"model": self.model, "messages": messages,
                   "temperature": 0.2, "max_tokens": max_tokens,
                   "enable_thinking": False}
        return self._post(payload, timeout=180)
