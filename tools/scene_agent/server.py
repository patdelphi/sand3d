"""本地网页 UI：提交一句话生成场景、微调组件、再评审一轮。

只用标准库（http.server），渲染等重活交给 jobs.py 子进程，
保证 HTTP 线程不被 bpy 阻塞。
运行：
  python -m tools.scene_agent.server --port 8787
然后浏览器打开 http://127.0.0.1:8787
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import OUT, ROOT, SCENE_DIR, SRC_DIR

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
JOBS = {}          # job_id -> 状态字典
LOCK = threading.Lock()
PROMPT_FILE = os.path.join(OUT, "prompts.json")  # 场景描述历史
MAX_PROMPTS = 50
JOB_TIMEOUT = 600   # 单个任务墙钟上限（秒），超时强杀，避免队列被永久占住
MIME = {
    ".html": "text/html; charset=utf-8",
    ".png": "image/png",
    ".glb": "model/gltf-binary",
    ".json": "application/json; charset=utf-8",
    ".blend": "application/octet-stream",
}


def _load_prompts():
    """读取描述历史；首次没有文件时用已有场景的描述做种子。"""
    if os.path.isfile(PROMPT_FILE):
        try:
            with open(PROMPT_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return [p for p in data if isinstance(p, str) and p.strip()][:MAX_PROMPTS]
        except Exception:
            return []
    seen, seeded = set(), []
    for row in _list_scenes():
        text = (row.get("prompt") or "").strip()
        if text and text not in seen:
            seen.add(text)
            seeded.append(text)
    return seeded[:MAX_PROMPTS]


def _remember_prompt(prompt):
    """记录一条描述，重复的跳过；返回最新列表。"""
    text = " ".join(str(prompt).split())
    if not text:
        return _load_prompts()
    with LOCK:
        prompts = _load_prompts()
        if text in prompts:
            return prompts
        prompts.insert(0, text)
        prompts = prompts[:MAX_PROMPTS]
        os.makedirs(OUT, exist_ok=True)
        tmp = PROMPT_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(prompts, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, PROMPT_FILE)
    return prompts


def _start_job(kind, argv):
    """同一时间只允许一个任务，避免两个 bpy 进程抢 GPU/内存。"""
    with LOCK:
        if any(j["status"] == "running" for j in JOBS.values()):
            raise RuntimeError("上一轮任务还在运行，可点「取消」或等它结束")
        jid = uuid.uuid4().hex[:8]
        JOBS[jid] = {"id": jid, "kind": kind, "status": "running",
                     "logs": [], "result": None, "error": None}
    threading.Thread(target=_run_job, args=(jid, argv), daemon=True).start()
    return jid


def _run_job(jid, argv):
    """起子进程跑 jobs.py，逐行收集日志与 RESULT；超时可自动终止。"""
    job = JOBS[jid]
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC_DIR + os.pathsep + ROOT
    env["PYTHONUNBUFFERED"] = "1"
    cmd = [sys.executable, "-u", "-m", "tools.scene_agent.jobs"] + argv
    log_dir = os.path.join(OUT, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, jid + ".log")
    try:
        with open(log_path, "w", encoding="utf-8") as lf:
            lf.write(" ".join(argv) + "\n")
            proc = subprocess.Popen(
                cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
            job["proc"] = proc

            def _kill():
                """超时或用户取消时强杀子进程，避免任务永远占着队列。"""
                job["killed"] = "超时" if not job.get("cancel") else "已取消"
                try:
                    proc.kill()
                except Exception:
                    pass

            timer = threading.Timer(JOB_TIMEOUT, _kill)
            timer.daemon = True
            timer.start()
            try:
                for line in proc.stdout:
                    line = line.rstrip()
                    if not line:
                        continue
                    lf.write(line + "\n")
                    lf.flush()
                    if line.startswith("RESULT "):
                        try:
                            job["result"] = json.loads(line[7:])
                        except json.JSONDecodeError:
                            job["logs"].append("结果解析失败：" + line[:200])
                        continue
                    job["logs"].append(line)
                    if len(job["logs"]) > 600:
                        del job["logs"][:150]
            finally:
                timer.cancel()
            code = proc.wait()
        if job.get("killed"):
            job["status"] = "failed"
            job["error"] = f"任务{job['killed']}，已终止（日志：{log_path}）"
        elif code == 0 and job["result"]:
            job["status"] = "done"
        else:
            job["status"] = "failed"
            job["error"] = job["error"] or f"子进程退出码 {code}（日志：{log_path}）"
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = f"{type(exc).__name__}: {exc}"


class Handler(BaseHTTPRequestHandler):
    server_version = "SceneAgentUI/0.1"

    # ---------- 基础输出 ----------
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path):
        """只允许读取场景目录与 web 目录内的文件，防目录穿越。"""
        real = os.path.realpath(path)
        allowed = (os.path.realpath(SCENE_DIR), os.path.realpath(WEB_DIR))
        if not any(real.startswith(p) for p in allowed) or not os.path.isfile(real):
            self._json({"error": "not found"}, 404)
            return
        ext = os.path.splitext(real)[1].lower()
        with open(real, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    # ---------- 路由 ----------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path in ("/", "/index.html"):
                self._file(os.path.join(WEB_DIR, "index.html"))
            elif path.startswith("/files/"):
                rel = path[len("/files/"):]
                self._file(os.path.join(SCENE_DIR, rel))
            elif path == "/api/job":
                jid = (parse_qs(parsed.query).get("id") or [""])[0]
                job = JOBS.get(jid)
                if not job:
                    self._json({"error": "unknown job"}, 404)
                else:
                    with LOCK:
                        self._json({"id": job["id"], "kind": job["kind"],
                                    "status": job["status"], "logs": job["logs"][-200:],
                                    "result": job["result"], "error": job["error"]})
            elif path == "/api/scenes":
                self._json({"scenes": _list_scenes()})
            elif path == "/api/prompts":
                self._json({"prompts": _load_prompts()})
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:  # 路由异常统一回报
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            data = self._body()
            if path == "/api/run":
                prompt = (data.get("prompt") or "").strip()
                if not prompt:
                    return self._json({"error": "prompt 不能为空"}, 400)
                # 记录描述历史：重复的自动跳过，失败不影响生成
                try:
                    _remember_prompt(prompt)
                except Exception as exc:
                    print("[ui] 记录描述失败:", exc)
                argv = ["run", "--prompt", prompt,
                        "--rounds", str(int(data.get("rounds", 2))),
                        "--width", str(int(data.get("width", 768))),
                        "--samples", str(int(data.get("samples", 24)))]
                if data.get("clip"):
                    argv.append("--clip")
                if data.get("env"):
                    argv += ["--env", json.dumps(data["env"], ensure_ascii=False)]
                if data.get("seed") not in (None, ""):
                    # 显式种子便于复现同一布局；不传则由后端随机并写入 meta
                    try:
                        argv += ["--seed", str(int(data["seed"]))]
                    except (TypeError, ValueError):
                        return self._json({"error": "seed 必须是整数"}, 400)
                self._json({"job": _start_job("run", argv)})
            elif path == "/api/adjust":
                scene = data.get("scene") or ""
                items = data.get("items") or []
                folder = os.path.realpath(os.path.join(SCENE_DIR, scene))
                if not folder.startswith(os.path.realpath(SCENE_DIR)) or not os.path.isdir(folder):
                    return self._json({"error": "场景不存在"}, 400)
                if not items and not data.get("env"):
                    return self._json({"error": "没有修改"}, 400)
                argv = ["adjust", "--scene", folder, "--items",
                        json.dumps(items, ensure_ascii=False),
                        "--width", str(int(data.get("width", 768))),
                        "--samples", str(int(data.get("samples", 24)))]
                if data.get("env"):
                    argv += ["--env", json.dumps(data["env"], ensure_ascii=False)]
                self._json({"job": _start_job("adjust", argv)})
            elif path == "/api/rerun":
                scene = data.get("scene") or ""
                folder = os.path.realpath(os.path.join(SCENE_DIR, scene))
                if not folder.startswith(os.path.realpath(SCENE_DIR)) or not os.path.isdir(folder):
                    return self._json({"error": "场景不存在"}, 400)
                argv = ["rerun", "--scene", folder,
                        "--width", str(int(data.get("width", 768))),
                        "--samples", str(int(data.get("samples", 24)))]
                self._json({"job": _start_job("rerun", argv)})
            elif path == "/api/reproduce":
                # 原样重建：只读 scene.json，纯本地重渲染，不调 LLM
                scene = data.get("scene") or ""
                folder = os.path.realpath(os.path.join(SCENE_DIR, scene))
                if not folder.startswith(os.path.realpath(SCENE_DIR)) or not os.path.isdir(folder):
                    return self._json({"error": "场景不存在"}, 400)
                argv = ["rerun", "--scene", folder, "--no-review",
                        "--width", str(int(data.get("width", 768))),
                        "--samples", str(int(data.get("samples", 24)))]
                self._json({"job": _start_job("reproduce", argv)})
            elif path == "/api/cancel":
                # 取消当前任务：标记后强杀子进程（日志保留）
                count = 0
                with LOCK:
                    running = [j for j in JOBS.values() if j["status"] == "running"]
                for job in running:
                    job["cancel"] = True
                    job["killed"] = "已取消"
                    proc = job.get("proc")
                    if proc:
                        try:
                            proc.kill()
                            count += 1
                        except Exception:
                            pass
                self._json({"cancelled": count})
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def log_message(self, fmt, *args):
        """只记录 API 请求，静态文件不刷屏。"""
        if "/api/" in (args[0] if args else ""):
            sys.stderr.write("[ui] " + fmt % args + "\n")


def _list_scenes():
    """按时间倒序列出已有场景，供界面切换。"""
    if not os.path.isdir(SCENE_DIR):
        return []
    rows = []
    for name in sorted(os.listdir(SCENE_DIR), reverse=True):
        folder = os.path.join(SCENE_DIR, name)
        scene_path = os.path.join(folder, "scene.json")
        if not os.path.isfile(scene_path):
            continue
        try:
            with open(scene_path, "r", encoding="utf-8-sig") as fh:
                layout = json.load(fh)
        except Exception:
            continue
        rows.append({"scene": name, "prompt": layout.get("prompt", ""),
                     "items": len(layout.get("items", [])),
                     "preview": os.path.basename(layout.get("preview") or "")})
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description="场景 Agent 本地网页 UI")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"UI 已启动：http://{args.host}:{args.port}  (Ctrl+C 停止)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("已停止", flush=True)


if __name__ == "__main__":
    main()
