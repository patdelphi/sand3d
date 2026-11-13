"""下载 5 个 sand3d 数据集的 USDA 网格（约 198GB）。

用 huggingface_hub 的 snapshot_download，只取 usda/ 目录，支持断点续传。
每分钟输出一次进度；连续 10 分钟无增长则判定卡死并退出。
"""
import os
import sys
import threading
import time

from huggingface_hub import snapshot_download

ROOT = r"c:\Users\patde\Documents\GitHub\sand3d\data\sand3d-datasets"
REPOS = ["sand3d-objaverse", "sand3d-articraft", "sand3d-simready",
         "sand3d-cad", "sand3d-automate"]
STALL_SECONDS = 600


def dir_size(path):
    """统计目录总字节数，用于进度显示。"""
    total = 0
    for dirpath, _, names in os.walk(path):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return total


def monitor(stop_event, path):
    """定时打印进度，并在长时间无增长时置位停止。"""
    last_size, last_change = dir_size(path), time.time()
    while not stop_event.wait(60):
        size = dir_size(path)
        mins = (time.time() - last_change) / 60
        print(f"[进度] {size / 2**30:.2f} GiB，已静默 {mins:.0f} 分钟", flush=True)
        if size > last_size:
            last_size, last_change = size, time.time()
        elif time.time() - last_change > STALL_SECONDS:
            print("[错误] 超过 10 分钟无增长，判定卡死", flush=True)
            os._exit(2)


def main():
    started = time.time()
    for repo in REPOS:
        target = os.path.join(ROOT, repo)
        os.makedirs(target, exist_ok=True)
        before = dir_size(target)
        print(f"\n=== {repo} 开始，已有 {before / 2**30:.2f} GiB ===", flush=True)
        stop = threading.Event()
        watcher = threading.Thread(target=monitor, args=(stop, target), daemon=True)
        watcher.start()
        try:
            snapshot_download(
                repo_id=f"Sandflow/{repo}",
                repo_type="dataset",
                local_dir=target,
                allow_patterns=["usda/**"],
                max_workers=8,
                resume_download=True,
            )
        finally:
            stop.set()
        after = dir_size(target)
        print(f"=== {repo} 完成，{after / 2**30:.2f} GiB，"
              f"新增 {(after - before) / 2**30:.2f} GiB ===", flush=True)
    print(f"\n全部完成，总用时 {(time.time() - started) / 60:.1f} 分钟", flush=True)


if __name__ == "__main__":
    sys.exit(main())
