"""
并行回测运行器 - 支持多进程加速回测
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Type, Tuple, Any
import pandas as pd
import backtrader as bt
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing as mp
import os

from backtest_engine import AShareStrategy, AShareData
from log_manager import LogManager
from run_backtest import (
    create_cerebro,
    add_stock_data,
    BacktestReport,
    load_cached_data,
)

try:
    from web_visualizer.visualizer import generate_trade_charts_from_trades

    WEB_VISUALIZER_AVAILABLE = True
except ImportError:
    WEB_VISUALIZER_AVAILABLE = False


logger = logging.getLogger(__name__)


def run_single_stock_group(
    stock_data: Dict[str, pd.DataFrame],
    strategy_class_name: str,
    strategy_params: Optional[dict],
    initial_cash: float,
    commission_rate: float,
    stamp_duty: float,
    name: str,
    verbose: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    在单独进程中运行一组股票的回测

    Args:
        stock_data: 股票数据字典
        strategy_class_name: 策略类名（字符串，因为需要跨进程传递）
        strategy_params: 策略参数
        initial_cash: 初始资金
        commission_rate: 佣金费率
        stamp_duty: 印花税率
        name: 回测名称
        verbose: 是否详细输出

    Returns:
        回测结果字典或 None
    """
    # 在子进程中重新导入策略类
    try:
        if strategy_class_name == "SMACrossStrategy":
            from run_backtest import SMACrossStrategy

            strategy_class = SMACrossStrategy
        else:
            from strategies import load_strategy_by_name

            strategy_class = load_strategy_by_name(strategy_class_name)
            if strategy_class is None:
                logger.error(f"无法在子进程中加载策略: {strategy_class_name}")
                return None
    except Exception as e:
        logger.error(f"策略加载失败: {e}")
        return None

    if not stock_data:
        logger.warning("股票数据为空，跳过: %s", name)
        return None

    cerebro = create_cerebro(
        initial_cash=initial_cash,
        commission_rate=commission_rate,
        stamp_duty=stamp_duty,
        verbose=verbose,
    )

    n_added = add_stock_data(cerebro, stock_data)
    if n_added == 0:
        logger.warning("未添加任何股票: %s", name)
        return None

    logger.info(
        "开始回测: %s | 股票数: %d | 初始资金: %.2f 元",
        name,
        n_added,
        initial_cash,
    )

    cerebro.addstrategy(strategy_class, **(strategy_params or {}))
    try:
        strats = cerebro.run()
        strat = strats[0]
    except Exception as e:
        logger.error("回测异常: %s", e)
        return None

    # 提取回测结果为可序列化的字典
    ta = strat.analyzers.trades.get_analysis()
    total_trades = ta.get("total", {}).get("total", 0)
    closed_trades = ta.get("total", {}).get("closed", 0)
    won = ta.get("won", {}).get("total", 0)
    lost = ta.get("lost", {}).get("total", 0)
    win_rate_pct = won / (won + lost) * 100 if (won + lost) > 0 else 0.0
    gross_pnl = ta.get("pnl", {}).get("gross", {}).get("total", 0)
    net_pnl = ta.get("pnl", {}).get("net", {}).get("total", 0)

    ret_a = strat.analyzers.returns.get_analysis()
    total_return_pct = (strat.broker.getvalue() - initial_cash) / initial_cash * 100

    dd_a = strat.analyzers.drawdown.get_analysis()
    max_drawdown_pct = dd_a.get("max", {}).get("drawdown", 0)

    final_value = strat.broker.getvalue()

    result = {
        "name": name,
        "total_trades": total_trades,
        "closed_trades": closed_trades,
        "won": won,
        "lost": lost,
        "win_rate_pct": win_rate_pct,
        "gross_pnl": gross_pnl,
        "net_pnl": net_pnl,
        "total_return_pct": total_return_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "final_value": final_value,
        "initial_cash": initial_cash,
        "stock_count": n_added,
        "closed_trades_data": list(strat._closed_trades)
        if hasattr(strat, "_closed_trades")
        else [],
    }

    # 尝试获取策略的诊断数据（如果策略支持）
    try:
        if hasattr(strat, "get_diagnostics_data"):
            result["diagnostics"] = strat.get_diagnostics_data()
    except Exception as e:
        logger.warning(f"无法获取诊断数据: {e}")

    if verbose:
        print(f"\n{name} 完成 | 股票数: {n_added} | 收益率: {total_return_pct:+.2f}%")

    return result


class ParallelBacktestRunner:
    """并行回测运行器"""

    def __init__(
        self,
        initial_cash: float = 20000.0,
        commission_rate: float = 0.0003,
        stamp_duty: float = 0.001,
        logs_dir: str = "logs",
        max_workers: Optional[int] = None,
    ):
        self.initial_cash = initial_cash
        self.commission_rate = commission_rate
        self.stamp_duty = stamp_duty
        self.log_manager = LogManager(logs_dir)
        self.max_workers = max_workers or (mp.cpu_count() - 1)

    def _split_stock_data(
        self, stock_data: Dict[str, pd.DataFrame], n_groups: int
    ) -> List[Dict[str, pd.DataFrame]]:
        """将股票数据分成n组（按股票代码排序确保顺序一致性）"""
        if n_groups <= 1:
            return [stock_data]

        # 按股票代码排序确保分组顺序一致
        items = sorted(stock_data.items())
        group_size = len(items) // n_groups
        groups = []

        for i in range(n_groups):
            start_idx = i * group_size
            if i == n_groups - 1:
                # 最后一组包含剩余的所有股票
                end_idx = len(items)
            else:
                end_idx = (i + 1) * group_size

            group_items = items[start_idx:end_idx]
            groups.append(dict(group_items))

        return groups

    def run_parallel_backtest(
        self,
        train_data: Dict[str, pd.DataFrame],
        val_data: Dict[str, pd.DataFrame],
        strategy_class: Type[AShareStrategy],
        strategy_params: Optional[dict] = None,
        verbose: bool = True,
    ) -> tuple:
        """
        并行运行训练集和验证集回测

        Args:
            train_data: 训练集数据
            val_data: 验证集数据
            strategy_class: 策略类
            strategy_params: 策略参数
            verbose: 是否详细输出

        Returns:
            (train_results, val_results) 元组
        """
        # 确定工作进程数
        train_groups = max(1, min(self.max_workers, len(train_data)))
        val_groups = max(1, min(self.max_workers, len(val_data)))

        if verbose:
            print(f"并行回测配置:")
            print(f"  CPU核心数: {mp.cpu_count()}")
            print(f"  工作进程数: {self.max_workers}")
            print(f"  训练集分组: {train_groups} 组 ({len(train_data)} 只股票)")
            print(f"  验证集分组: {val_groups} 组 ({len(val_data)} 只股票)")

        # 获取策略类名（用于跨进程传递）
        strategy_class_name = strategy_class.__name__

        # 并行运行训练集
        print("\n开始并行训练集回测...")
        train_groups_list = self._split_stock_data(train_data, train_groups)
        train_futures = []

        with ProcessPoolExecutor(max_workers=train_groups) as executor:
            for i, group_data in enumerate(train_groups_list):
                if not group_data:
                    continue
                # 计算该组的资金分配（按股票数量比例）
                group_stock_count = len(group_data)
                total_stock_count = len(train_data)
                group_initial_cash = (
                    self.initial_cash * (group_stock_count / total_stock_count)
                    if total_stock_count > 0
                    else self.initial_cash
                )

                future = executor.submit(
                    run_single_stock_group,
                    group_data,
                    strategy_class_name,
                    strategy_params,
                    group_initial_cash,
                    self.commission_rate,
                    self.stamp_duty,
                    f"训练集_组{i + 1}",
                    verbose,
                )
                train_futures.append(future)

            train_results = []
            for future in as_completed(train_futures):
                result = future.result()
                if result is not None:
                    train_results.append(result)

        # 并行运行验证集
        print("\n开始并行验证集回测...")
        val_groups_list = self._split_stock_data(val_data, val_groups)
        val_futures = []

        with ProcessPoolExecutor(max_workers=val_groups) as executor:
            for i, group_data in enumerate(val_groups_list):
                if not group_data:
                    continue
                # 计算该组的资金分配（按股票数量比例）
                group_stock_count = len(group_data)
                total_stock_count = len(val_data)
                group_initial_cash = (
                    self.initial_cash * (group_stock_count / total_stock_count)
                    if total_stock_count > 0
                    else self.initial_cash
                )

                future = executor.submit(
                    run_single_stock_group,
                    group_data,
                    strategy_class_name,
                    strategy_params,
                    group_initial_cash,
                    self.commission_rate,
                    self.stamp_duty,
                    f"验证集_组{i + 1}",
                    verbose,
                )
                val_futures.append(future)

            val_results = []
            for future in as_completed(val_futures):
                result = future.result()
                if result is not None:
                    val_results.append(result)

        return train_results, val_results

    def merge_results(self, results: List[Dict[str, Any]]) -> Optional[BacktestReport]:
        """合并多个回测结果（包含完整诊断数据）"""
        if not results:
            return None

        # 合并统计数据 - 使用原始初始资金（不是各组资金之和）
        original_initial_cash = self.initial_cash  # 原始指定的总初始资金
        total_final_value = sum(r["final_value"] for r in results)
        total_trades = sum(r["total_trades"] for r in results)
        total_won = sum(r["won"] for r in results)
        total_lost = sum(r["lost"] for r in results)
        total_gross_pnl = sum(r["gross_pnl"] for r in results)
        total_net_pnl = sum(r["net_pnl"] for r in results)
        total_stock_count = sum(r["stock_count"] for r in results)

        # 合并策略特定的诊断数据
        merged_diagnostics = {
            "morphology_stats": {
                "hammer": {"signals": 0, "wins": 0, "total_pnl": 0.0},
                "engulfing": {"signals": 0, "wins": 0, "total_pnl": 0.0},
                "morning_star": {"signals": 0, "wins": 0, "total_pnl": 0.0},
            },
            "three_elements": {
                "planned_stop_loss": 0,
                "actual_stop_loss": 0,
                "planned_take_profit": 0,
                "actual_take_profit": 0,
                "planned_risk_reward": 0,
                "actual_risk_reward": 0,
                "compliance_rate": 0,
            },
            "key_position": {
                "historical_support": 0,
                "volume_profile_support": 0,
                "indicator_support": 0,
                "full_compliance": 0,
                "total_signals": 0,
            },
            "market_performance": {
                "bull_signals": 0,
                "bull_pnl": 0.0,
                "bear_signals": 0,
                "bear_pnl": 0.0,
                "sideways_signals": 0,
                "sideways_pnl": 0.0,
            },
            "candidate_management": {"added": 0, "removed": 0, "immediate_removal": 0},
            "total_signals": 0,
            "valid_signals": 0,
            "invalid_signals": 0,
        }

        for result in results:
            if "diagnostics" in result:
                diag = result["diagnostics"]
                # 合并形态统计
                for pattern in ["hammer", "engulfing", "morning_star"]:
                    if pattern in diag.get("morphology_stats", {}):
                        src = diag["morphology_stats"][pattern]
                        dst = merged_diagnostics["morphology_stats"][pattern]
                        dst["signals"] += src.get("signals", 0)
                        dst["wins"] += src.get("wins", 0)
                        dst["total_pnl"] += src.get("total_pnl", 0.0)

                # 合并三要素执行跟踪
                if "three_elements" in diag:
                    for key, value in diag["three_elements"].items():
                        merged_diagnostics["three_elements"][key] += value

                # 合并关键位验证统计
                if "key_position" in diag:
                    for key, value in diag["key_position"].items():
                        merged_diagnostics["key_position"][key] += value

                # 合并市场环境分析
                if "market_performance" in diag:
                    for key, value in diag["market_performance"].items():
                        merged_diagnostics["market_performance"][key] += value

                # 合并候选股管理
                if "candidate_management" in diag:
                    for key, value in diag["candidate_management"].items():
                        merged_diagnostics["candidate_management"][key] += value

                # 合并信号统计
                merged_diagnostics["total_signals"] += diag.get("total_signals", 0)
                merged_diagnostics["valid_signals"] += diag.get("valid_signals", 0)
                merged_diagnostics["invalid_signals"] += diag.get("invalid_signals", 0)

        # 创建虚拟策略实例用于报告
        class DummyBroker:
            def __init__(self, cash, value):
                self._cash = cash
                self._value = value

            def getcash(self):
                return self._cash

            def getvalue(self):
                return self._value

        class DummyStrategy:
            def __init__(self, final_value, initial_cash):
                self.broker = DummyBroker(final_value, final_value)
                self.analyzers = type(
                    "Analyzers",
                    (),
                    {
                        "trades": type(
                            "Trades",
                            (),
                            {
                                "get_analysis": lambda: {
                                    "total": {
                                        "total": total_trades,
                                        "closed": total_trades,
                                    },
                                    "won": {"total": total_won},
                                    "lost": {"total": total_lost},
                                    "pnl": {
                                        "gross": {"total": total_gross_pnl},
                                        "net": {"total": total_net_pnl},
                                    },
                                }
                            },
                        ),
                        "returns": type(
                            "Returns", (), {"get_analysis": lambda: {"rnorm100": 0}}
                        ),
                        "drawdown": type(
                            "DrawDown",
                            (),
                            {
                                "get_analysis": lambda: {
                                    "max": {
                                        "drawdown": max(
                                            r["max_drawdown_pct"] for r in results
                                        )
                                    }
                                }
                            },
                        ),
                        "sharpe": type(
                            "Sharpe",
                            (),
                            {"get_analysis": lambda: {"sharperatio": None}},
                        ),
                        "sqn": type("SQN", (), {"get_analysis": lambda: {"sqn": 0}}),
                    },
                )()

        dummy_strat = DummyStrategy(total_final_value, original_initial_cash)
        report = BacktestReport(dummy_strat, original_initial_cash, "合并结果")

        # 手动设置合并后的值
        report.total_trades = total_trades
        report.won = total_won
        report.lost = total_lost
        report.win_rate_pct = (
            total_won / (total_won + total_lost) * 100
            if (total_won + total_lost) > 0
            else 0.0
        )
        report.gross_pnl = total_gross_pnl
        report.net_pnl = total_net_pnl
        report.total_return_pct = (
            (total_final_value - original_initial_cash) / original_initial_cash * 100
        )
        report.max_drawdown_pct = max(r["max_drawdown_pct"] for r in results)
        report.final_value = total_final_value
        report.initial_cash = original_initial_cash

        # 将合并的诊断数据附加到报告中（供策略stop()方法使用）
        if hasattr(report, "__dict__"):
            report.diagnostics = merged_diagnostics

        return report


def run_parallel_backtest_with_config(
    config_path: Optional[str] = None,
    strategy_name: str = "sma",
    train_only: bool = False,
    val_only: bool = False,
    max_stocks: Optional[int] = None,
    initial_cash: float = 20000.0,
    quiet: bool = False,
    max_workers: Optional[int] = None,
):
    """
    并行回测运行入口函数
    """
    from strategies import load_strategy_by_name
    from run_backtest import load_strategy_from_config, SMACrossStrategy

    # 选择策略
    if config_path:
        strategy_class = load_strategy_from_config(config_path)
        strategy_name_for_log = "config"
    else:
        strategy_class = load_strategy_by_name(strategy_name)
        if strategy_class is None:
            if strategy_name == "sma":
                strategy_class = SMACrossStrategy
            elif strategy_name == "momentum":
                from strategies import MomentumStrategy

                strategy_class = MomentumStrategy or SMACrossStrategy
            else:
                strategy_class = (
                    load_strategy_by_name("ConservativeStrategy") or SMACrossStrategy
                )
        strategy_name_for_log = strategy_name

    # 加载数据
    train_data, val_data = load_cached_data(max_stocks=max_stocks)

    if train_only:
        val_data = {}
    elif val_only:
        train_data = {}

    # 创建并行回测运行器
    runner = ParallelBacktestRunner(
        initial_cash=initial_cash,
        logs_dir="logs",
        max_workers=max_workers,
    )

    verbose = not quiet

    # 运行并行回测
    train_results, val_results = runner.run_parallel_backtest(
        train_data, val_data, strategy_class, verbose=verbose
    )

    # 合并结果
    train_report = runner.merge_results(train_results) if train_results else None
    val_report = runner.merge_results(val_results) if val_results else None

    # 打印汇总报告
    if train_report and val_report:
        print("\n" + "#" * 70)
        print("#  并行回测汇总报告")
        print("#" * 70)
        print(f"  {'指标':<20} {'训练集':>15} {'验证集':>15}")
        print("-" * 50)
        print(
            f"  {'收益率':<20} {train_report.total_return_pct:+.2f}% {val_report.total_return_pct:+.2f}%"
        )
        print(
            f"  {'胜率':<20} {train_report.win_rate_pct:.2f}% {val_report.win_rate_pct:.2f}%"
        )
        print(
            f"  {'总交易次数':<20} {train_report.total_trades:>15} {val_report.total_trades:>15}"
        )
        print(
            f"  {'最大回撤':<20} {train_report.max_drawdown_pct:.2f}% {val_report.max_drawdown_pct:.2f}%"
        )
        print(
            f"  {'最终资金':<20} {train_report.final_value:>15,.2f} {val_report.final_value:>15,.2f}"
        )

    elif train_report:
        print("\n" + "#" * 70)
        print("#  训练集回测报告")
        print("#" * 70)
        train_report.print_summary()

    elif val_report:
        print("\n" + "#" * 70)
        print("#  验证集回测报告")
        print("#" * 70)
        val_report.print_summary()

    if WEB_VISUALIZER_AVAILABLE:
        from datetime import datetime

        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        if train_results:
            all_train_trades = []
            for r in train_results:
                all_train_trades.extend(r.get("closed_trades_data", []))
            if all_train_trades:
                output_file = (
                    log_dir / f"并行_训练集_{strategy_name_for_log}_{ts}_trades.html"
                )
                generate_trade_charts_from_trades(
                    all_train_trades,
                    output_file=str(output_file),
                    strategy_name=f"并行训练集 - {strategy_name_for_log}",
                    data_cache_dir="data_cache",
                )

        if val_results:
            all_val_trades = []
            for r in val_results:
                all_val_trades.extend(r.get("closed_trades_data", []))
            if all_val_trades:
                output_file = (
                    log_dir / f"并行_验证集_{strategy_name_for_log}_{ts}_trades.html"
                )
                generate_trade_charts_from_trades(
                    all_val_trades,
                    output_file=str(output_file),
                    strategy_name=f"并行验证集 - {strategy_name_for_log}",
                    data_cache_dir="data_cache",
                )
