"""校验已下载的 sand3d Parquet 数据集。

逐文件读 footer 取行数、schema 一致性、列类型；再抽样一条记录看内容形态。
只读元数据，不加载全部数据（内存安全）。
"""
import os
from collections import defaultdict

import pyarrow.parquet as pq

from sand3d_paths import DATA_ROOT

ROOT = DATA_ROOT
EXPECTED = {
    "sand3d-objaverse": 66887,
    "sand3d-cad": 24495,
    "sand3d-articraft": 9209,
    "sand3d-automate": 3169,
    "sand3d-simready": 2334,
}


def human(n):
    return f"{n / 1048576:.1f} MiB"


def preview(value, limit=120):
    """把字段值压缩成一行预览。"""
    if isinstance(value, bytes):
        return f"<bytes {len(value)}>"
    text = str(value).replace("\n", "\\n")
    return text[:limit] + ("..." if len(text) > limit else "")


def main():
    rows_by_dataset = defaultdict(int)
    files_by_dataset = defaultdict(int)
    schemas = {}
    samples = {}
    total_bytes = 0

    for dataset in sorted(os.listdir(ROOT)):
        base = os.path.join(ROOT, dataset)
        if not os.path.isdir(base):
            continue
        for dirpath, _, names in os.walk(base):
            for name in sorted(names):
                if not name.endswith(".parquet"):
                    continue
                path = os.path.join(dirpath, name)
                pf = pq.ParquetFile(path)
                total_bytes += os.path.getsize(path)
                rows_by_dataset[dataset] += pf.metadata.num_rows
                files_by_dataset[dataset] += 1
                # 每个数据集只记住第一份 schema，其余比对是否一致
                if dataset not in schemas:
                    schemas[dataset] = pf.schema_arrow
                elif pf.schema_arrow != schemas[dataset]:
                    print(f"[警告] schema 不一致: {path}")
                if dataset not in samples:
                    table = pf.read_row_group(0)
                    samples[dataset] = table.slice(0, 1).to_pylist()[0]

    print(f"根目录: {ROOT}")
    print(f"总大小: {human(total_bytes)}\n")

    print(f"{'数据集':<20}{'文件':>4}{'行数':>9}{'预期':>9}{'状态':>6}")
    for dataset in sorted(rows_by_dataset):
        got = rows_by_dataset[dataset]
        want = EXPECTED.get(dataset)
        ok = "OK" if got == want else f"差异 {got - want:+d}"
        print(f"{dataset:<20}{files_by_dataset[dataset]:>4}{got:>9}{want:>9}{ok:>8}")

    got_total = sum(rows_by_dataset.values())
    want_total = sum(EXPECTED.values())
    print(f"{'合计':<20}{sum(files_by_dataset.values()):>4}{got_total:>9}{want_total:>9}"
          f"{'OK' if got_total == want_total else '不一致':>8}\n")

    for dataset in sorted(samples):
        print(f"== {dataset} 列结构 ==")
        for field in schemas[dataset]:
            print(f"  {field.name}: {field.type}")
        print("  样本:")
        for key, value in samples[dataset].items():
            print(f"    {key}: {preview(value)}")
        print()


if __name__ == "__main__":
    main()
