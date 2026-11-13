"""Google Colab：把 sand3d 数据集的 USDA 模型下载到 Google Drive（带实时进度）。

前提：你自己已在 Colab 挂载 Drive（drive.mount('/content/drive')）。
用法：
  1. 上传本文件后运行：!python colab_download_sand3d.py
  2. token 自动读取 Colab 密钥 HF_TOKEN（需开启 Notebook access），否则读环境变量或提示输入

与旧版 snapshot_download 的差异：
  旧版每次启动都要逐个检查已下载文件（几万个小文件在 Drive 上极慢）。
  本脚本改为「实时进度」：
    - 每个文件下载成功后立刻（攒一批）写入 Drive 上的进度分片；
    - 下次启动只读进度分片（几百个几十 KB 的小文本），直接跳过已完成文件；
    - 远程文件清单通过 list_repo_tree 一次拉取，不再扫描本地目录。

参数：
  --scan   首次接管一个已有目录时用一次：全量扫描本地目录生成进度，之后不再扫描
  --reset  忽略已保存的进度（慎用，会重新检查全部文件）
  --repos  指定仓库（默认取脚本内的 REPOS），如 --repos sand3d-cad sand3d-automate

说明：
  - objaverse 的 USDA 约 181GB，请确认 Drive 空间足够
"""
import os
import getpass
import argparse
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from huggingface_hub import HfApi, RepoFile, hf_hub_download, whoami

# ===== 可按需修改 =====
REPOS = ["sand3d-objaverse"]                            # 可改为多个仓库
DEST_ROOT = "/content/drive/MyDrive/sand3d-datasets"    # Drive 上的新建目录
WORKERS = 8                                             # 并发文件数
BATCH_SIZE = 200                                        # 每攒多少条完成记录写一次进度分片
MAX_RETRY = 5                                           # 单文件最大重试次数
# =====================


def get_token():
    """按优先级取 token：Colab 密钥 → 环境变量 → 交互输入。"""
    try:
        from google.colab import userdata
        token = userdata.get("HF_TOKEN")
        if token:
            return token
    except Exception:
        pass                                   # 非 Colab 环境或未开启 Notebook access
    token = os.environ.get("HF_TOKEN")
    if token:
        return token
    return getpass.getpass("HF token: ")


class ProgressStore:
    """已完成文件清单，分片存放在 Drive 上。

    分片策略：每攒满 batch 条就新写一个 part-xxxxx.txt（先写 .tmp 再原子改名），
    这样单次只写几十 KB，崩掉最多丢失不足 batch 条记录，重跑时这几个文件会被
    hf_hub_download 快速跳过，代价可以忽略。
    """

    def __init__(self, root, batch=BATCH_SIZE):
        self.root = root
        self.batch = batch
        self.done = set()          # 已完成文件的仓库内相对路径
        self._pending = []         # 尚未落盘的新完成记录
        self._lock = threading.Lock()
        self._seq = 0              # 下一个分片序号

    def load(self):
        """读取全部分片，得到已完成集合与下一个分片序号。"""
        try:
            os.makedirs(self.root, exist_ok=True)
            names = sorted(n for n in os.listdir(self.root)
                           if n.startswith("part-") and n.endswith(".txt"))
        except OSError as exc:
            print(f"[警告] 读取进度目录失败：{exc}", flush=True)
            return
        for name in names:
            path = os.path.join(self.root, name)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    for line in fh:
                        item = line.strip()
                        if item:
                            self.done.add(item)
            except OSError as exc:
                print(f"[警告] 进度分片 {name} 读取失败：{exc}", flush=True)
        for name in names:
            try:
                self._seq = max(self._seq, int(name[5:10]) + 1)
            except ValueError:
                pass

    def add(self, rel_path):
        """记录一个已完成文件；攒满一批才落盘，避免频繁写 Drive。"""
        with self._lock:
            self.done.add(rel_path)
            self._pending.append(rel_path)
            if len(self._pending) >= self.batch:
                self._flush_locked()

    def flush(self):
        """把剩余记录落盘（正常结束、异常或 Ctrl+C 时调用）。"""
        with self._lock:
            self._flush_locked()

    def _flush_locked(self):
        """调用方需持有 self._lock。"""
        if not self._pending:
            return
        batch = self._pending
        self._pending = []
        name = f"part-{self._seq:05d}.txt"
        tmp = os.path.join(self.root, name + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write("\n".join(batch) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, os.path.join(self.root, name))
            self._seq += 1
        except OSError as exc:
            # 写失败则放回缓冲，下一批一起重试，避免丢记录
            print(f"[警告] 写入进度分片失败：{exc}", flush=True)
            self._pending[0:0] = batch


def scan_existing(remote_sizes, dest):
    """全量扫描本地目录（只在接管旧目录时执行一次），按文件大小匹配远程清单。

    返回大小一致的相对路径集合。跳过 hub 的 .cache 元数据目录。
    """
    local_sizes = {}
    try:
        for dirpath, dirnames, filenames in os.walk(dest):
            dirnames[:] = [d for d in dirnames if d != ".cache"]
            for name in filenames:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, dest).replace(os.sep, "/")
                try:
                    local_sizes[rel] = os.path.getsize(full)
                except OSError:
                    pass
    except OSError as exc:
        print(f"[警告] 扫描目录出错：{exc}", flush=True)
    return {rel for rel, size in local_sizes.items() if remote_sizes.get(rel) == size}


def download_one(repo_id, filename, dest, token):
    """下载单个文件，失败按指数退避重试；最终失败抛出最后一次异常。"""
    last_error = None
    for attempt in range(1, MAX_RETRY + 1):
        try:
            hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                repo_type="dataset",
                local_dir=dest,
                token=token,
            )
            return
        except KeyboardInterrupt:
            raise
        except Exception as exc:       # 网络错误、429、Drive 写失败都在这里
            last_error = exc
            wait = min(2 ** attempt, 60)
            print(f"[重试] {filename} 第 {attempt} 次失败（{exc}），{wait}s 后重试",
                  flush=True)
            time.sleep(wait)
    raise last_error


def sync_repo(repo, args, token):
    """同步单个仓库；返回是否全部成功。"""
    repo_id = f"Sandflow/{repo}"
    dest = os.path.join(DEST_ROOT, repo)
    os.makedirs(dest, exist_ok=True)

    store = ProgressStore(os.path.join(args.progress_root, repo))
    store.load()
    if args.reset:
        store.done.clear()

    # 远程文件清单：一次分页拉取，比逐个检查本地文件快得多
    api = HfApi()
    try:
        remote = [f for f in api.list_repo_tree(
            repo_id=repo_id, path_in_repo="usda", recursive=True,
            repo_type="dataset", token=token,
        ) if isinstance(f, RepoFile)]
    except Exception as exc:
        print(f"[错误] 获取 {repo} 文件清单失败：{exc}", flush=True)
        return False

    sizes = {f.path: (f.size or 0) for f in remote}
    total_bytes = sum(sizes.values())
    print(f"[{repo}] 远程 {len(remote)} 个文件（{total_bytes / 2**30:.2f} GiB），"
          f"进度记录 {len(store.done)} 个", flush=True)

    # 进度为空但目录里已有文件（例如旧版脚本下的半成品）：扫一次作为基线
    need_scan = args.scan or (not store.done and any(os.listdir(dest)))
    if not args.reset and need_scan:
        print(f"[{repo}] 扫描已有文件生成进度（仅此一次）…", flush=True)
        t0 = time.time()
        existing = scan_existing(sizes, dest)
        for rel in existing:
            store.add(rel)
        store.flush()
        print(f"[{repo}] 扫描完成，确认完整 {len(existing)} 个，"
              f"用时 {(time.time() - t0) / 60:.1f} 分钟", flush=True)

    todo = [path for path in sizes if path not in store.done]
    print(f"[{repo}] 待下载 {len(todo)} 个", flush=True)
    if not todo:
        print(f"[{repo}] 已全部完成", flush=True)
        return True

    todo_bytes = sum(sizes[p] for p in todo)
    done_bytes = 0
    done_count = 0
    failed = []
    start = time.time()

    def report():
        """按固定节奏打印进度与速度。"""
        elapsed = max(time.time() - start, 1e-6)
        speed_files = done_count / elapsed
        speed_bytes = done_bytes / elapsed
        remain = todo_bytes - done_bytes
        eta = remain / speed_bytes / 60 if speed_bytes > 0 else float("inf")
        print(f"[{repo}] 完成 {done_count}/{len(todo)}，"
              f"{done_bytes / 2**30:.2f}/{todo_bytes / 2**30:.2f} GiB，"
              f"{speed_files:.2f} 文件/秒，{speed_bytes / 2**20:.1f} MiB/s，"
              f"已用 {elapsed / 60:.1f} 分钟，预计剩余 {eta:.0f} 分钟", flush=True)

    pool = ThreadPoolExecutor(max_workers=WORKERS)
    try:
        futures = {pool.submit(download_one, repo_id, p, dest, token): p for p in todo}
        for idx, fut in enumerate(as_completed(futures), 1):
            path = futures[fut]
            try:
                fut.result()
            except Exception as exc:
                failed.append(path)
                print(f"[失败] {path}：{exc}", flush=True)
            else:
                done_count += 1
                done_bytes += sizes[path]
                store.add(path)          # 实时记录进度（攒批落盘）
            if idx % 20 == 0 or idx == len(todo):
                report()
    except KeyboardInterrupt:
        print(f"\n[{repo}] 收到中断，正在停止…", flush=True)
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    finally:
        pool.shutdown(wait=True)
        store.flush()                    # 把最后不足一批的记录补写

    # 清理 hub 元数据，避免 Drive 上多出几万个 .cache 文件
    shutil.rmtree(os.path.join(dest, ".cache"), ignore_errors=True)

    if failed:
        fail_log = os.path.join(args.progress_root, f"{repo}_failed.txt")
        try:
            with open(fail_log, "w", encoding="utf-8") as fh:
                fh.write("\n".join(failed) + "\n")
            print(f"[{repo}] 失败 {len(failed)} 个，已写入 {fail_log}，重跑会自动重试",
                  flush=True)
        except OSError as exc:
            print(f"[警告] 写入失败清单出错：{exc}", flush=True)
        return False

    print(f"[{repo}] 全部完成，用时 {(time.time() - start) / 60:.1f} 分钟", flush=True)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description="下载 sand3d USDA 到 Drive（实时进度、秒级续传）")
    parser.add_argument("--scan", action="store_true",
                        help="强制全量扫描目标目录生成进度（首次接管旧目录时用一次）")
    parser.add_argument("--reset", action="store_true",
                        help="忽略已保存的进度（慎用）")
    parser.add_argument("--repos", nargs="*", default=REPOS,
                        help="要下载的仓库名，默认取脚本内 REPOS")
    args = parser.parse_args(argv)
    args.progress_root = os.path.join(DEST_ROOT, "_progress")

    token = get_token()
    os.environ["HF_TOKEN"] = token          # 避免匿名请求触发 429 限流
    try:
        print("登录账号:", whoami()["name"])
    except Exception as exc:
        print(f"[错误] 无法验证 token：{exc}", flush=True)
        return 1
    # 注意：Colab 上 disk_usage 读的是虚拟机磁盘，不是 Drive 配额，故不打印空间
    print("目标目录:", DEST_ROOT, "（请确认是已挂载的目标账号）")

    ok = True
    for repo in args.repos:
        t0 = time.time()
        print(f"\n=== {repo} 开始 ===", flush=True)
        try:
            if not sync_repo(repo, args, token):
                ok = False
        except KeyboardInterrupt:
            print("\n[中断] 已完成进度已保存在 Drive，下次运行会从断点继续", flush=True)
            return 130
        except Exception as exc:
            print(f"[错误] {repo} 同步异常：{exc}", flush=True)
            ok = False
        print(f"=== {repo} 结束，用时 {(time.time() - t0) / 60:.1f} 分钟 ===", flush=True)

    print("\n全部完成。Drive 路径:", DEST_ROOT)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
