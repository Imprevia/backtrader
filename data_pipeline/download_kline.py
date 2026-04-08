# -*- coding: utf-8 -*-
"""
Step 2: 下载所有A股K线数据（baostock 3进程并行）

功能:
    根据 stock_list.pkl 中的股票列表，逐个下载 2020-2025 年日K线数据。

特性:
    - 3 进程并行下载（baostock 服务器对单IP有限流）
    - 每 100 只自动保存断点到 all_data_raw.pkl
    - Ctrl+C 中断后再次运行自动续传（跳过已下载的股票）

字段:
    datetime, code, open, high, low, close, volume, amount, turn, pct_chg

用法:
    python -m data_pipeline.download_kline        # 全量下载
"""

import sys

sys.path.insert(0, ".")

import time
import pandas as pd
from pathlib import Path
from tqdm import tqdm
import baostock as bs
from concurrent.futures import ProcessPoolExecutor, as_completed

CACHE_DIR = Path("data_cache")
OUT_FILE = CACHE_DIR / "all_data_raw.pkl"

TRAIN_START = "2020-01-02"
VAL_END = "2025-12-31"
MAX_WORKERS = 3


def fetch_one(bs_code):
    """下载单只股票K线，返回 (pure_code, df) 或 None。"""
    pure = bs_code.replace("sh.", "").replace("sz.", "")
    try:
        bs.login()
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,code,open,high,low,close,volume,amount,turn,pctChg",
            start_date=TRAIN_START,
            end_date=VAL_END,
            frequency="d",
            adjustflag="2",
        )
        if rs.error_code != "0":
            bs.logout()
            return None
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        bs.logout()
        if not rows or len(rows) < 50:
            return None
        df = pd.DataFrame(
            rows,
            columns=[
                "datetime",
                "code",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "turn",
                "pct_chg",
            ],
        )
        df["datetime"] = pd.to_datetime(df["datetime"])
        for col in [
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "turn",
            "pct_chg",
        ]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["datetime", "close"])
        df = df.sort_values("datetime").reset_index(drop=True)
        return (pure, df)
    except Exception:
        try:
            bs.logout()
        except Exception:
            pass
        return None


def main():
    t0 = time.time()
    print("读取股票列表...")
    sl = pd.read_pickle(CACHE_DIR / "stock_list.pkl")
    codes = sl["code"].tolist()
    print(f"  股票总数: {len(codes)} 只")

    # 断点续传：检查哪些股票已经下载过
    if OUT_FILE.exists():
        existing = pd.read_pickle(OUT_FILE)
        downloaded = set(existing.keys())
        remaining = [
            c
            for c in codes
            if c.replace("sh.", "").replace("sz.", "") not in downloaded
        ]
        print(f"  已下载: {len(downloaded)} 只")
        print(f"  待下载: {len(remaining)} 只")
    else:
        existing = {}
        remaining = codes
        print(f"  待下载: {len(remaining)} 只（全新下载）")

    if not remaining:
        print("全部下载完成。")
        return

    print(f"\n开始下载 {len(remaining)} 只（{MAX_WORKERS} 进程并行）...")
    print("  每100只自动保存断点，Ctrl+C 中断后再次运行可续传")
    results = dict(existing)
    failed = 0

    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_one, c): c for c in remaining}
        done = 0
        for future in tqdm(as_completed(futures), total=len(futures), desc="下载K线"):
            r = future.result()
            if r is not None:
                results[r[0]] = r[1]
            else:
                failed += 1
            done += 1
            if done % 100 == 0:
                pd.to_pickle(results, OUT_FILE)
                elapsed = time.time() - t0
                speed = done / elapsed
                eta = (len(remaining) - done) / speed
                print(
                    f"\n  断点: {len(results)}/{len(remaining)} | "
                    f"失败{failed} | {speed:.1f}只/秒 | 剩余约{eta / 60:.0f}分钟"
                )

    pd.to_pickle(results, OUT_FILE)
    sz = OUT_FILE.stat().st_size / 1024 / 1024
    elapsed = time.time() - t0
    print(f"\n完成! {len(results)}只成功 {failed}只失败 | {elapsed:.0f}秒 | {sz:.1f}MB")


if __name__ == "__main__":
    main()
