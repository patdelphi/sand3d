"""Google Colab：把 sand3d 数据集的 USDA 模型直接下载到 Google Drive。

前提：你自己已在 Colab 挂载 Drive（drive.mount('/content/drive')）。
用法：
  1. 上传本文件后运行：!python colab_download_sand3d.py
  2. token 自动读取 Colab 密钥 HF_TOKEN（需开启 Notebook access），否则读环境变量或提示输入

说明：
  - 直接写入 Drive，断点续传（重跑跳过已存在文件）
  - objaverse 的 USDA 约 181GB，请确认 Drive 空间足够
  - 只想下小数据集，把 REPOS 改成 ["sand3d-cad"] 等
"""
import getpass
import os
import shutil
import sys
import time

from huggingface_hub import snapshot_download, whoami

# ===== 可按需修改 =====
REPOS = ["sand3d-objaverse"]                       # 可改为多个仓库
DEST_ROOT = "/content/drive/MyDrive/sand3d-datasets"   # Drive 上的新建目录
WORKERS = 8                                        # 并发文件数
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


def main():
    os.environ["HF_TOKEN"] = get_token()       # 避免匿名请求触发 429 限流
    print("登录账号:", whoami()["name"])

    os.makedirs(DEST_ROOT, exist_ok=True)
    free = shutil.disk_usage(DEST_ROOT).free / 2**30
    print(f"Drive 剩余空间 {free:.1f} GiB")

    for repo in REPOS:
        dest = os.path.join(DEST_ROOT, repo)
        os.makedirs(dest, exist_ok=True)
        t0 = time.time()
        print(f"\n=== {repo} 开始 ===", flush=True)
        snapshot_download(
            repo_id=f"Sandflow/{repo}",
            repo_type="dataset",
            local_dir=dest,
            allow_patterns=["usda/**"],
            max_workers=WORKERS,
        )
        # 清理 hub 元数据，避免 Drive 上多出几万个 .cache 文件
        shutil.rmtree(os.path.join(dest, ".cache"), ignore_errors=True)
        print(f"=== {repo} 完成，用时 {(time.time() - t0) / 60:.1f} 分钟 ===", flush=True)

    print("\n全部完成。Drive 路径:", DEST_ROOT)


if __name__ == "__main__":
    sys.exit(main())
