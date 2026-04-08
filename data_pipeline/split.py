# -*- coding: utf-8 -*-
"""
Step 3: 按股票随机划分 7:2:1（训练:验证:测试）

规则:
    - 每只股票的完整数据（2020-2025）只属于一个集合
    - 不切分时间，不按年段拆分
    - 随机种子=42，保证可复现

输出:
    train_data.pkl  - 70% 股票，完整K线
    val_data.pkl    - 20% 股票，完整K线
    test_data.pkl   - 10% 股票，完整K线
    split_map.pkl   - 划分映射记录（含股票代码列表）

用法:
    python -m data_pipeline.split
"""

import sys

sys.path.insert(0, ".")

import pandas as pd
import numpy as np
from pathlib import Path

CACHE_DIR = Path("data_cache")
SEED = 42


def main():
    np.random.seed(SEED)

    # 1. 加载全量数据
    raw = CACHE_DIR / "all_data_raw.pkl"
    if not raw.exists():
        print(f"错误: {raw} 不存在，请先运行 download_kline")
        return

    print("加载全量数据...")
    all_data = pd.read_pickle(raw)
    print(f"  全量股票: {len(all_data)} 只")

    # 2. 完全随机按股票划分 7:2:1
    print("\n按股票随机划分 7:2:1...")
    codes = list(all_data.keys())
    np.random.shuffle(codes)

    n_total = len(codes)
    n_train = int(n_total * 0.7)
    n_val = int(n_total * 0.2)

    train_codes = set(codes[:n_train])
    val_codes = set(codes[n_train : n_train + n_val])
    test_codes = set(codes[n_train + n_val :])

    train_data = {c: all_data[c] for c in train_codes}
    val_data = {c: all_data[c] for c in val_codes}
    test_data = {c: all_data[c] for c in test_codes}

    print(f"  训练集: {len(train_data)} 只 ({len(train_data) / n_total * 100:.1f}%)")
    print(f"  验证集: {len(val_data)} 只 ({len(val_data) / n_total * 100:.1f}%)")
    print(f"  测试集: {len(test_data)} 只 ({len(test_data) / n_total * 100:.1f}%)")

    # 3. 保存
    print("\n保存数据...")
    pd.to_pickle(train_data, CACHE_DIR / "train_data.pkl")
    print(f"  train_data.pkl: {len(train_data)} 只")

    pd.to_pickle(val_data, CACHE_DIR / "val_data.pkl")
    print(f"  val_data.pkl:   {len(val_data)} 只")

    pd.to_pickle(test_data, CACHE_DIR / "test_data.pkl")
    print(f"  test_data.pkl:  {len(test_data)} 只")

    pd.to_pickle(all_data, CACHE_DIR / "all_data.pkl")
    print(f"  all_data.pkl:   {len(all_data)} 只")

    split_map = {
        "train_codes": train_codes,
        "val_codes": val_codes,
        "test_codes": test_codes,
        "seed": SEED,
    }
    pd.to_pickle(split_map, CACHE_DIR / "split_map.pkl")
    print(f"  split_map.pkl:  seed={SEED}")

    # 4. 验证：确认每只股票时间范围完整
    print("\n数据范围（每只股票应包含完整时间）:")
    for name, data in [
        ("训练集", train_data),
        ("验证集", val_data),
        ("测试集", test_data),
    ]:
        dm = min(df["datetime"].min() for df in data.values())
        dM = max(df["datetime"].max() for df in data.values())
        avg = np.mean([len(df) for df in data.values()])
        print(
            f"  {name}: {dm.date()} ~ {dM.date()} (平均 {avg:.0f}天/股, {len(data)}只)"
        )


if __name__ == "__main__":
    main()
