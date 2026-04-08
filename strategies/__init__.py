"""
策略模块 - 支持目录式策略加载
"""

import os
import importlib
import logging
from pathlib import Path
from typing import Dict, Type, Optional

from backtest_engine import AShareStrategy

logger = logging.getLogger(__name__)


def discover_strategies() -> Dict[str, Type[AShareStrategy]]:
    """
    自动发现所有策略目录中的策略类。

    返回: {策略名: 策略类} 的字典
    """
    strategies_dir = Path(__file__).parent
    strategies = {}

    # 遍历所有子目录
    for item in strategies_dir.iterdir():
        if item.is_dir() and item.name != "__pycache__":
            try:
                # 尝试导入策略模块
                strategy_module = importlib.import_module(
                    f"strategies.{item.name}.strategy"
                )

                # 查找继承自 AShareStrategy 的类
                for attr_name in dir(strategy_module):
                    attr = getattr(strategy_module, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, AShareStrategy)
                        and attr != AShareStrategy
                    ):
                        strategies[attr_name] = attr
                        logger.info(f"发现策略: {attr_name} from {item.name}")

            except Exception as e:
                logger.warning(f"加载策略目录 {item.name} 失败: {e}")
                continue

    return strategies


def load_strategy_by_name(strategy_name: str) -> Optional[Type[AShareStrategy]]:
    """
    根据策略名称加载策略类。

    支持两种加载方式：
    1. 类名：如 "MAGoldenCross3_16_Complete"、"ConservativeStrategy"
    2. 目录名：如 "ma_golden_cross_3_16_complete"、"conservative"

    Args:
        strategy_name: 策略类名或目录名

    Returns:
        策略类或 None（如果未找到）
    """
    strategies = discover_strategies()

    # 1. 先按类名查找
    if strategy_name in strategies:
        return strategies[strategy_name]

    # 2. 按目录名查找（将下划线转驼峰，或直接匹配目录名）
    strategies_dir = Path(__file__).parent
    for item in strategies_dir.iterdir():
        if item.is_dir() and item.name != "__pycache__":
            # 精确匹配目录名
            if item.name == strategy_name:
                try:
                    strategy_module = importlib.import_module(
                        f"strategies.{item.name}.strategy"
                    )
                    for attr_name in dir(strategy_module):
                        attr = getattr(strategy_module, attr_name)
                        if (
                            isinstance(attr, type)
                            and issubclass(attr, AShareStrategy)
                            and attr != AShareStrategy
                        ):
                            return attr
                except Exception:
                    pass
            # 部分匹配（目录名包含策略名，或策略名包含目录名）
            normalized_dir = item.name.lower().replace("_", "")
            normalized_input = strategy_name.lower().replace("_", "")
            if normalized_dir == normalized_input or normalized_input in normalized_dir:
                try:
                    strategy_module = importlib.import_module(
                        f"strategies.{item.name}.strategy"
                    )
                    for attr_name in dir(strategy_module):
                        attr = getattr(strategy_module, attr_name)
                        if (
                            isinstance(attr, type)
                            and issubclass(attr, AShareStrategy)
                            and attr != AShareStrategy
                        ):
                            return attr
                except Exception:
                    pass

    return None


# 兼容性导入 - 保持现有代码正常工作
try:
    from .conservative.strategy import ConservativeStrategy
except ImportError:
    ConservativeStrategy = None

try:
    from .conservative_monthly.strategy import ConservativeMonthlyStrategy
except ImportError:
    ConservativeMonthlyStrategy = None

try:
    from .diversified_portfolio.strategy import DiversifiedPortfolioFixed
except ImportError:
    DiversifiedPortfolioFixed = None

try:
    from .super_conservative_enhanced.strategy import SuperConservativeEnhancedStrategy
except ImportError:
    SuperConservativeEnhancedStrategy = None

try:
    from .multi_strategy_dynamic.strategy import MultiStrategyDynamicWeightStrategy
except ImportError:
    MultiStrategyDynamicWeightStrategy = None
