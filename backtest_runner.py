"""
回测运行器 - 封装回测运行逻辑，支持多种测试模式
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Type, Union

import pandas as pd
import backtrader as bt

from backtest_engine import AShareStrategy, AShareData
from log_manager import LogManager
from run_backtest import (
    create_cerebro,
    add_stock_data,
    BacktestReport,
    load_cached_data,
)

try:
    from web_visualizer.visualizer import generate_trade_charts

    WEB_VISUALIZER_AVAILABLE = True
except ImportError:
    WEB_VISUALIZER_AVAILABLE = False

logger = logging.getLogger(__name__)


class BacktestRunner:
    """回测运行器，支持全量测试和简单测试模式"""

    def __init__(
        self,
        initial_cash: float = 20000.0,
        commission_rate: float = 0.0003,
        stamp_duty: float = 0.001,
        logs_dir: str = "logs",
    ):
        self.initial_cash = initial_cash
        self.commission_rate = commission_rate
        self.stamp_duty = stamp_duty
        self.log_manager = LogManager(logs_dir)

    def run_single_backtest(
        self,
        stock_data: Dict[str, pd.DataFrame],
        strategy_class: Type[AShareStrategy],
        strategy_params: Optional[dict] = None,
        name: str = "",
        verbose: bool = True,
        strategy_name: str = "",
    ) -> Optional[BacktestReport]:
        """
        运行单次回测

        Args:
            stock_data: 股票数据字典
            strategy_class: 策略类
            strategy_params: 策略参数
            name: 回测名称
            verbose: 是否详细输出

        Returns:
            BacktestReport 对象或 None
        """
        if not stock_data:
            logger.warning("股票数据为空，跳过: %s", name)
            return None

        cerebro = create_cerebro(
            initial_cash=self.initial_cash,
            commission_rate=self.commission_rate,
            stamp_duty=self.stamp_duty,
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
            self.initial_cash,
        )

        if verbose:
            print(f"\n开始回测: {name}")
            print(f"  股票数量: {n_added}")
            print(f"  初始资金: {self.initial_cash:.2f} 元")
            print(
                f"  预计处理时间: {'较短' if n_added < 100 else '中等' if n_added < 500 else '较长'}"
            )
            print("  正在运行回测引擎...")

        cerebro.addstrategy(strategy_class, **(strategy_params or {}))
        try:
            strats = cerebro.run()
            strat = strats[0]
        except Exception as e:
            logger.error("回测异常: %s", e)
            import traceback

            traceback.print_exc()
            return None

        report = BacktestReport(strat, self.initial_cash, name)
        if verbose:
            report.print_summary()

        if WEB_VISUALIZER_AVAILABLE:
            from datetime import datetime

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            strategy_label = strategy_name or strategy_class.__name__
            output_file = (
                self.log_manager.logs_dir / f"{name}_{strategy_label}_{ts}_trades.html"
            )
            generate_trade_charts(
                strat,
                output_file=str(output_file),
                strategy_name=f"{name} - {strategy_label}",
                data_cache_dir="data_cache",
            )

        return report

    def run_full_test(
        self,
        strategy_class: Type[AShareStrategy],
        strategy_params: Optional[dict] = None,
        max_stocks: Optional[int] = None,
        verbose: bool = True,
        dynamic_split: bool = False,
        seed: Optional[int] = None,
    ) -> tuple:
        """
        运行全量测试（训练集+验证集）

        Args:
            strategy_class: 策略类
            strategy_params: 策略参数
            max_stocks: 最大股票数量（用于测试模式）
            verbose: 是否详细输出
            dynamic_split: 是否动态划分数据（防过拟合）
            seed: 动态划分随机种子

        Returns:
            (train_report, val_report) 元组
        """
        train_data, val_data = load_cached_data(
            max_stocks=max_stocks,
            dynamic_split=dynamic_split,
            seed=seed,
        )

        # 训练集回测
        print("\n" + "#" * 70)
        print("#  训练集回测 (70% 股票，完整时间序列)")
        print("#" * 70)
        train_report = self.run_single_backtest(
            train_data,
            strategy_class,
            strategy_params=strategy_params,
            name="训练集",
            verbose=verbose,
            strategy_name=strategy_class.__name__,
        )

        # 验证集回测
        print("\n" + "#" * 70)
        print("#  验证集回测 (20% 股票，完整时间序列)")
        print("#" * 70)
        val_report = self.run_single_backtest(
            val_data,
            strategy_class,
            strategy_params=strategy_params,
            name="验证集",
            verbose=verbose,
            strategy_name=strategy_class.__name__,
        )

        # 保存结果
        if train_report and val_report:
            from log_manager import get_backtest_results_from_report

            results = get_backtest_results_from_report(train_report, val_report)
            strategy_name = strategy_class.__name__
            test_mode = "small" if max_stocks else "full"
            self.log_manager.save_backtest_results(results, strategy_name, test_mode)

        return train_report, val_report

    def run_train_only(
        self,
        strategy_class: Type[AShareStrategy],
        strategy_params: Optional[dict] = None,
        max_stocks: Optional[int] = None,
        verbose: bool = True,
        dynamic_split: bool = False,
        seed: Optional[int] = None,
    ) -> Optional[BacktestReport]:
        """运行仅训练集回测"""
        train_data, _ = load_cached_data(
            max_stocks=max_stocks,
            dynamic_split=dynamic_split,
            seed=seed,
        )
        report = self.run_single_backtest(
            train_data,
            strategy_class,
            strategy_params=strategy_params,
            name="训练集 (70%股票)",
            verbose=verbose,
            strategy_name=strategy_class.__name__,
        )

        # 保存结果
        if report:
            results = {
                "performance_metrics": {
                    "total_trades": report.total_trades,
                    "winning_trades": report.won,
                    "losing_trades": report.lost,
                    "win_rate": report.win_rate_pct,
                    "avg_win": report.avg_win,
                    "avg_loss": report.avg_loss,
                    "profit_factor": (report.gross_pnl / abs(report.net_pnl))
                    if report.net_pnl != 0
                    else 0,
                    "total_return": report.total_return_pct,
                    "max_drawdown": report.max_drawdown_pct,
                    "sqn": report.sqn,
                    "sharpe_ratio": report.sharpe_ratio,
                    "final_value": report.final_value,
                }
            }
            strategy_name = strategy_class.__name__
            test_mode = "train_small" if max_stocks else "train"
            self.log_manager.save_backtest_results(results, strategy_name, test_mode)

        return report

    def run_val_only(
        self,
        strategy_class: Type[AShareStrategy],
        strategy_params: Optional[dict] = None,
        max_stocks: Optional[int] = None,
        verbose: bool = True,
        dynamic_split: bool = False,
        seed: Optional[int] = None,
    ) -> Optional[BacktestReport]:
        """运行仅验证集回测"""
        _, val_data = load_cached_data(
            max_stocks=max_stocks,
            dynamic_split=dynamic_split,
            seed=seed,
        )
        report = self.run_single_backtest(
            val_data,
            strategy_class,
            strategy_params=strategy_params,
            name="验证集 (20%股票)",
            verbose=verbose,
            strategy_name=strategy_class.__name__,
        )

        # 保存结果
        if report:
            results = {
                "performance_metrics": {
                    "total_trades": report.total_trades,
                    "winning_trades": report.won,
                    "losing_trades": report.lost,
                    "win_rate": report.win_rate_pct,
                    "avg_win": report.avg_win,
                    "avg_loss": report.avg_loss,
                    "profit_factor": (report.gross_pnl / abs(report.net_pnl))
                    if report.net_pnl != 0
                    else 0,
                    "total_return": report.total_return_pct,
                    "max_drawdown": report.max_drawdown_pct,
                    "sqn": report.sqn,
                    "sharpe_ratio": report.sharpe_ratio,
                    "final_value": report.final_value,
                }
            }
            strategy_name = strategy_class.__name__
            test_mode = "val_small" if max_stocks else "val"
            self.log_manager.save_backtest_results(results, strategy_name, test_mode)

        return report


def run_backtest_with_config(
    config_path: Optional[str] = None,
    strategy_name: str = "sma",
    train_only: bool = False,
    val_only: bool = False,
    max_stocks: Optional[int] = None,
    initial_cash: float = 20000.0,
    quiet: bool = False,
    dynamic_split: bool = False,
    seed: Optional[int] = None,
):
    """
    统一的回测运行入口函数

    Args:
        config_path: YAML配置文件路径
        strategy_name: 策略名称
        train_only: 仅运行训练集
        val_only: 仅运行验证集
        max_stocks: 最大股票数量（简单测试模式）
        initial_cash: 初始资金
        quiet: 静默模式
        dynamic_split: 是否动态划分数据（防过拟合，默认False保持兼容）
        seed: 动态划分随机种子（None=每次不同，int=固定可复现）
    """
    from strategies import load_strategy_by_name
    from run_backtest import load_strategy_from_config, SMACrossStrategy

    # 初始化日志管理器
    log_manager = LogManager()
    if not quiet:
        log_manager.clear_old_logs()

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

    # 创建回测运行器
    runner = BacktestRunner(initial_cash=initial_cash, logs_dir="logs")

    # 添加文件日志
    test_mode = "small" if max_stocks else "full"
    if train_only:
        test_mode = "train_small" if max_stocks else "train"
    elif val_only:
        test_mode = "val_small" if max_stocks else "val"

    file_handler = log_manager.create_log_handler(
        strategy_name_for_log, test_mode, use_json=False, use_rotating=True
    )
    # 同时配置根 logger 和 strategy logger，确保所有日志都能写入文件
    logging.getLogger().addHandler(file_handler)
    logging.getLogger("strategy").addHandler(file_handler)

    # 设置日志级别（根logger默认INFO，strategy logger可通过参数控制）
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger("strategy").setLevel(logging.DEBUG)

    # 运行回测
    verbose = not quiet
    if train_only:
        runner.run_train_only(
            strategy_class,
            max_stocks=max_stocks,
            verbose=verbose,
            dynamic_split=dynamic_split,
            seed=seed,
        )
    elif val_only:
        runner.run_val_only(
            strategy_class,
            max_stocks=max_stocks,
            verbose=verbose,
            dynamic_split=dynamic_split,
            seed=seed,
        )
    else:
        runner.run_full_test(
            strategy_class,
            max_stocks=max_stocks,
            verbose=verbose,
            dynamic_split=dynamic_split,
            seed=seed,
        )
