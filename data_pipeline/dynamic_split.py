# -*- coding: utf-8 -*-
"""
动态数据划分模块 - 每次回测时动态划分训练集和验证集

功能:
    - 每次回测时使用不同的随机种子划分数据
    - 防止策略对固定划分过拟合
    - 支持可复现性（可通过种子固定划分）

用法:
    from data_pipeline.dynamic_split import DynamicDataSplitter

    splitter = DynamicDataSplitter()
    train_data, val_data = splitter.split(all_data, train_ratio=0.7)
"""

import numpy as np
import pandas as pd
from typing import Dict, Tuple, Optional
from pathlib import Path

CACHE_DIR = Path("data_cache")


class DynamicDataSplitter:
    """
    动态数据划分器 - 每次回测时重新划分数据

    优势:
        - 每次回测使用不同划分，防止过拟合
        - 训练集和验证集互不重叠
        - 支持自定义随机种子用于复现
    """

    def __init__(self, seed: Optional[int] = None):
        """
        初始化动态划分器

        Args:
            seed: 随机种子。如果为 None，则使用时间戳（每次不同）
                 如果指定种子，则划分可复现
        """
        self.seed = seed

    def _get_seed(self) -> int:
        """获取随机种子"""
        if self.seed is None:
            import time

            return int(time.time()) % 1000000
        return self.seed

    def split(
        self,
        all_data: Dict[str, pd.DataFrame],
        train_ratio: float = 0.7,
        val_ratio: float = 0.2,
        test_ratio: float = 0.1,
    ) -> Tuple[
        Dict[str, pd.DataFrame], Dict[str, pd.DataFrame], Dict[str, pd.DataFrame]
    ]:
        """
        将数据划分为训练集、验证集、测试集

        Args:
            all_data: 完整数据字典 {code: DataFrame}
            train_ratio: 训练集比例
            val_ratio: 验证集比例
            test_ratio: 测试集比例

        Returns:
            (train_data, val_data, test_data) 元组

        Raises:
            ValueError: 如果比例之和不等于1.0
        """
        if not np.isclose(train_ratio + val_ratio + test_ratio, 1.0):
            raise ValueError(
                f"比例之和必须为1.0，当前: {train_ratio + val_ratio + test_ratio}"
            )

        seed = self._get_seed()
        np.random.seed(seed)
        print(f"[动态划分] 使用随机种子: {seed}")

        codes = list(all_data.keys())
        np.random.shuffle(codes)

        n_total = len(codes)
        n_train = int(n_total * train_ratio)
        n_val = int(n_total * val_ratio)

        train_codes = set(codes[:n_train])
        val_codes = set(codes[n_train : n_train + n_val])
        test_codes = set(codes[n_train + n_val :])

        train_data = {c: all_data[c] for c in train_codes}
        val_data = {c: all_data[c] for c in val_codes}
        test_data = {c: all_data[c] for c in test_codes}

        print(
            f"[动态划分] 训练集: {len(train_data)} 只 ({len(train_data) / n_total * 100:.1f}%)"
        )
        print(
            f"[动态划分] 验证集: {len(val_data)} 只 ({len(val_data) / n_total * 100:.1f}%)"
        )
        print(
            f"[动态划分] 测试集: {len(test_data)} 只 ({len(test_data) / n_total * 100:.1f}%)"
        )

        return train_data, val_data, test_data

    def split_train_val(
        self,
        all_data: Dict[str, pd.DataFrame],
        train_ratio: float = 0.7,
    ) -> Tuple[Dict[str, pd.DataFrame], Dict[str, pd.DataFrame]]:
        """
        将数据划分为训练集和验证集（不含测试集）

        Args:
            all_data: 完整数据字典 {code: DataFrame}
            train_ratio: 训练集比例

        Returns:
            (train_data, val_data) 元组
        """
        val_ratio = 1.0 - train_ratio
        train_data, val_data, _ = self.split(all_data, train_ratio, val_ratio, 0.0)
        return train_data, val_data


def load_all_data() -> Dict[str, pd.DataFrame]:
    """
    加载全量数据（从 all_data_raw.pkl 或 all_data.pkl）

    Returns:
        全量数据字典 {code: DataFrame}
    """
    # 优先使用 all_data.pkl
    all_data_path = CACHE_DIR / "all_data.pkl"
    raw_path = CACHE_DIR / "all_data_raw.pkl"

    if all_data_path.exists():
        print(f"加载全量数据: {all_data_path}")
        return pd.read_pickle(all_data_path)
    elif raw_path.exists():
        print(f"加载全量数据: {raw_path}")
        return pd.read_pickle(raw_path)
    else:
        raise FileNotFoundError(
            f"全量数据不存在。请确认 data_cache/ 目录下有 all_data.pkl 或 all_data_raw.pkl\n"
            f"如需重新生成数据，请运行: python -m data_pipeline.split"
        )


def dynamic_split_and_load(
    train_ratio: float = 0.7,
    seed: Optional[int] = None,
    max_stocks: Optional[int] = None,
) -> Tuple[Dict[str, pd.DataFrame], Dict[str, pd.DataFrame]]:
    """
    动态划分数据并返回训练集和验证集

    这是主要的便捷函数，每次调用都会重新划分数据

    Args:
        train_ratio: 训练集比例（默认0.7）
        seed: 随机种子（None=每次不同，int=固定种子可复现）
        max_stocks: 最大股票数量（用于测试）

    Returns:
        (train_data, val_data) 元组

    Example:
        # 每次回测使用不同划分（防止过拟合）
        train_data, val_data = dynamic_split_and_load()

        # 使用固定种子（可复现）
        train_data, val_data = dynamic_split_and_load(seed=42)

        # 测试模式（少量股票）
        train_data, val_data = dynamic_split_and_load(max_stocks=50)
    """
    all_data = load_all_data()

    all_data = {
        code: df for code, df in all_data.items() if df is not None and not df.empty
    }

    if max_stocks is not None:
        codes = list(all_data.keys())[:max_stocks]
        all_data = {code: all_data[code] for code in codes}
        print(f"[动态划分] 限制股票数量: {max_stocks} 只")

    splitter = DynamicDataSplitter(seed=seed)
    train_data, val_data = splitter.split_train_val(all_data, train_ratio)

    return train_data, val_data


if __name__ == "__main__":
    print("测试动态数据划分...\n")
    train_data, val_data = dynamic_split_and_load(max_stocks=100)
    print(f"\n训练集股票数: {len(train_data)}")
    print(f"验证集股票数: {len(val_data)}")
    print(f"重叠股票数: {len(set(train_data.keys()) & set(val_data.keys()))} (应为0)")
