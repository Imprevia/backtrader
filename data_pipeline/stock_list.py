# -*- coding: utf-8 -*-
"""
Step 1: 获取全部A股股票列表（沪深两市）

调用 baostock.query_stock_industry() 获取最新股票列表，
过滤后保存为 stock_list.pkl 供后续下载使用。

用法:
    python -m data_pipeline.stock_list
"""

import sys

sys.path.insert(0, ".")

import baostock as bs
import pandas as pd
from pathlib import Path

CACHE_DIR = Path("data_cache")
CACHE_DIR.mkdir(exist_ok=True)


def main():
    print("从 baostock 获取全部A股列表...")
    bs.login()
    rs = bs.query_stock_industry()
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    bs.logout()

    df = pd.DataFrame(rows, columns=rs.fields)
    print(f"获取到 {len(df)} 只股票")

    # 过滤：只保留 sh. 和 sz. 开头的股票（排除ETF、新三板等）
    df = df[df["code"].str.startswith("sh.") | df["code"].str.startswith("sz.")].copy()
    print(f"过滤后（仅沪深A股）: {len(df)} 只")

    # 去重
    df = df.drop_duplicates(subset=["code"])
    print(f"去重后: {len(df)} 只")

    # 保存
    out = CACHE_DIR / "stock_list.pkl"
    pd.to_pickle(df, out)
    print(f"\n已保存: {out}")
    print(f"  字段: {list(df.columns)}")
    print(f"  数量: {len(df)} 只")

    # 展示
    print(f"\n示例（前10只）:")
    print(df[["code", "code_name", "industry", "industryClassification"]].head(10))


if __name__ == "__main__":
    main()
