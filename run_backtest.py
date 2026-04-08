"""
A 股量化回测系统 - 主入口
用法:
    python run_backtest.py --config strategy_template.yaml
    python run_backtest.py --train              # 仅训练集
    python run_backtest.py --val                # 仅验证集
    python run_backtest.py --max-stocks 50      # 测试模式
"""

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Type

import pandas as pd
import backtrader as bt
from backtest_engine import (
    AShareStrategy,
    AShareData,
    create_ashare_commission,
    calc_sell_cost,
    get_limit_rate,
)
from strategies import discover_strategies, load_strategy_by_name
from log_manager import LogManager, get_backtest_results_from_report

CACHE_DIR = Path("data_cache")


def load_cached_data(
    max_stocks: Optional[int] = None,
    require_full_range: bool = True,
    dynamic_split: bool = False,
    seed: Optional[int] = None,
):
    """
    从缓存加载训练集和验证集数据。

    Args:
        max_stocks: 最大股票数量
        require_full_range: 是否要求2020年前有数据
        dynamic_split: 是否每次回测动态划分（防过拟合）
        seed: 动态划分时的随机种子（None=每次不同，int=固定可复现）
    """
    if dynamic_split:
        from data_pipeline.dynamic_split import dynamic_split_and_load

        return dynamic_split_and_load(
            train_ratio=0.7,
            seed=seed,
            max_stocks=max_stocks,
        )

    train_path = CACHE_DIR / "train_data.pkl"
    val_path = CACHE_DIR / "val_data.pkl"

    if not train_path.exists() or not val_path.exists():
        raise FileNotFoundError(
            f"数据缓存不存在。请先运行: python -m data_pipeline.split"
        )

    print("正在加载训练集数据...")
    train_data = pd.read_pickle(train_path)
    print(f"  训练集: {len(train_data)} 只股票")

    print("正在加载验证集数据...")
    val_data = pd.read_pickle(val_path)
    print(f"  验证集: {len(val_data)} 只股票")

    if require_full_range and max_stocks is not None:
        common_date = pd.Timestamp("2020-01-02")
        train_filtered = {}
        for code, df in train_data.items():
            if df is None or df.empty:
                continue
            start = df["datetime"].min()
            if start <= common_date:
                train_filtered[code] = df
        val_filtered = {}
        for code, df in val_data.items():
            if df is None or df.empty:
                continue
            start = df["datetime"].min()
            if start <= common_date:
                val_filtered[code] = df
        print(
            f"  2020年前股票: 训练集 {len(train_filtered)} 只 / 验证集 {len(val_filtered)} 只"
        )
        train_data = dict(list(train_filtered.items())[:max_stocks])
        val_data = dict(list(val_filtered.items())[:max_stocks])
        print(f"  限制股票数量: {max_stocks} 只")
    elif max_stocks is not None:
        train_data = dict(list(train_data.items())[:max_stocks])
        val_data = dict(list(val_data.items())[:max_stocks])
        print(f"  限制股票数量: {max_stocks} 只")

    return train_data, val_data


logger = logging.getLogger(__name__)


# ============================================================================
# 报告生成器
# ============================================================================


class BacktestReport:
    """回测报告生成器，汇总所有 analyzer 结果。"""

    def __init__(self, strategy_instance, initial_cash: float, name: str = ""):
        self.strat = strategy_instance
        self.initial_cash = initial_cash
        self.name = name
        self.final_cash = strategy_instance.broker.getcash()
        self.final_value = strategy_instance.broker.getvalue()

        # 预计算所有指标（存储为实例属性，供对比报告直接访问）
        ta = strategy_instance.analyzers.trades.get_analysis()
        self.total_trades = ta.get("total", {}).get("total", 0)
        self.closed_trades = ta.get("total", {}).get("closed", 0)
        self.open_trades = ta.get("total", {}).get("open", 0)
        won = ta.get("won", {}).get("total", 0)
        lost = ta.get("lost", {}).get("total", 0)
        self.won = won
        self.lost = lost
        self.win_rate_pct = won / (won + lost) * 100 if (won + lost) > 0 else 0.0
        self.gross_pnl = ta.get("pnl", {}).get("gross", {}).get("total", 0)
        self.net_pnl = ta.get("pnl", {}).get("net", {}).get("total", 0)
        self.avg_win = ta.get("won", {}).get("pnl", {}).get("average", 0)
        self.avg_loss = ta.get("lost", {}).get("pnl", {}).get("average", 0)
        self.max_win = ta.get("won", {}).get("pnl", {}).get("max", 0)
        self.max_loss = ta.get("lost", {}).get("pnl", {}).get("max", 0)

        ret_a = strategy_instance.analyzers.returns.get_analysis()
        self.annual_return_pct = ret_a.get("rnorm100", 0)
        self.total_return_pct = (self.final_value - initial_cash) / initial_cash * 100
        self.absolute_return = self.final_value - initial_cash

        dd_a = strategy_instance.analyzers.drawdown.get_analysis()
        self.max_drawdown_pct = dd_a.get("max", {}).get("drawdown", 0)
        self.max_money_dd = dd_a.get("max", {}).get("moneydown", 0)

        sr_a = strategy_instance.analyzers.sharpe.get_analysis()
        self.sharpe_ratio = sr_a.get("sharperatio")

        sqn_a = strategy_instance.analyzers.sqn.get_analysis()
        self.sqn = sqn_a.get("sqn")
        self.sqn_trades = sqn_a.get("trades", 0)

    def print_summary(self):
        """打印回测报告。"""
        print("\n" + "=" * 70)
        print(f"  回测报告: {self.name}")
        print("=" * 70)

        # ---- 收益 & 资金 ----
        print(f"\n  【资金概况】")
        print(f"  初始资金: {self.initial_cash:>15,.2f} 元")
        print(f"  最终资金: {self.final_value:>15,.2f} 元")
        print(f"  绝对收益: {(self.final_value - self.initial_cash):>+15,.2f} 元")
        print(
            f"  收益率:  {(self.final_value - self.initial_cash) / self.initial_cash * 100:>+14.2f}  %"
        )

        # ---- 交易统计 ----
        print(f"\n  【交易统计】")
        print(
            f"  总交易次数: {self.total_trades:>10d}  (平仓: {self.closed_trades}  持仓中: {self.open_trades})"
        )
        print(f"  盈利次数:   {self.won:>10d}  亏损次数: {self.lost}")
        print(f"  胜率:       {self.win_rate_pct:>10.2f}  %")

        # ---- 盈亏统计 ----
        print(f"\n  【盈亏统计】")
        print(f"  总盈亏(毛): {self.gross_pnl:>+15,.2f} 元")
        print(f"  总盈亏(净): {self.net_pnl:>+15,.2f} 元")
        print(
            f"  盈利均值:   {self.avg_win:>+14,.2f} 元  最大单次盈利: {self.max_win:>+12,.2f} 元"
        )
        print(
            f"  亏损均值:   {self.avg_loss:>+14,.2f} 元  最大单次亏损: {self.max_loss:>+12,.2f} 元"
        )

        # ---- 年化收益 ----
        print(f"\n  【年化收益】")
        print(f"  年化收益率: {self.annual_return_pct:>+14.2f}  %")

        # ---- 风险指标 ----
        print(f"\n  【风险指标】")
        print(f"  最大回撤:   {self.max_drawdown_pct:>+14.2f}  %")
        print(f"  最大浮亏:   {self.max_money_dd:>+14,.2f} 元")
        print(f"  夏普比率:   {self.sharpe_ratio if self.sharpe_ratio else 'N/A':>14}")
        print(
            f"  SQN:        {self.sqn if self.sqn is not None else 'N/A':>14}  (交易数: {self.sqn_trades})"
        )

        # ---- 持仓统计 ----
        print(f"\n  【持仓状态】")
        positions = []
        for data in self.strat.datas:
            pos = self.strat.getposition(data)
            if pos.size > 0:
                code = data._name
                market_value = pos.size * data.close[0]
                cost = pos.price * pos.size
                pnl = market_value - cost
                positions.append(
                    (code, pos.size, data.close[0], pos.price, pnl, market_value)
                )

        if positions:
            for code, size, close, cost_price, pnl, mv in positions:
                pct = pnl / (cost_price * size) * 100 if cost_price > 0 else 0
                print(
                    f"  {code} 持仓:{size:>6d}股 "
                    f"现价:{close:>8.2f} 成本:{cost_price:>8.2f} "
                    f"盈亏:{pnl:>+8.2f} ({pct:+.2f}%) "
                    f"市值:{mv:>10.2f}"
                )
        else:
            print(f"  当前无持仓")

        print("\n" + "=" * 70)


# ============================================================================
# 回测核心运行函数
# ============================================================================


def create_cerebro(
    initial_cash: float = 20000.0,
    commission_rate: float = 0.0003,
    stamp_duty: float = 0.001,
    verbose: bool = True,
) -> bt.Cerebro:
    """创建配置好的 Cerebro 实例。"""
    cerebro = bt.Cerebro(preload=True, runonce=False, stdstats=False)

    # 设置初始资金
    cerebro.broker.setcash(initial_cash)

    # 设置 A 股手续费
    create_ashare_commission(
        cerebro,
        commission_rate=commission_rate,
        stamp_duty=stamp_duty,
    )

    # 添加 analyzer
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe")
    cerebro.addanalyzer(bt.analyzers.SQN, _name="sqn")
    cerebro.addanalyzer(bt.analyzers.AnnualReturn, _name="annual")

    # 添加观察者
    cerebro.addobserver(bt.observers.Broker)
    cerebro.addobserver(bt.observers.Trades)
    cerebro.addobserver(bt.observers.BuySell)

    return cerebro


def add_stock_data(
    cerebro: bt.Cerebro,
    stock_data: Dict[str, pd.DataFrame],
) -> int:
    """
    将股票数据添加到 cerebro。

    Args:
        cerebro: Cerebro 实例
        stock_data: {代码: DataFrame}

    Returns:
        成功添加的股票数量
    """
    added = 0
    for code, df in stock_data.items():
        try:
            df_copy = df.copy()
            if "datetime" not in df_copy.columns:
                logger.warning("股票 %s 数据缺少 datetime 列，跳过", code)
                continue

            date_max = df_copy["datetime"].max()

            data = AShareData(
                dataname=df_copy,
                name=code,
                datetime=0,
                fromdate=None,
                todate=pd.Timestamp(date_max),
            )
            cerebro.adddata(data)
            added += 1
        except Exception as e:
            logger.debug("添加股票 %s 失败: %s", code, e)
            continue

    return added


def run_backtest(
    stock_data: Dict[str, pd.DataFrame],
    strategy_class: Type[AShareStrategy],
    initial_cash: float = 20000.0,
    strategy_params: Optional[dict] = None,
    name: str = "",
    commission_rate: float = 0.0003,
    stamp_duty: float = 0.001,
    verbose: bool = True,
) -> Optional[BacktestReport]:
    """运行单次回测。"""
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
        "开始回测: %s | 股票数: %d | 初始资金: %.2f 元", name, n_added, initial_cash
    )

    cerebro.addstrategy(strategy_class, **(strategy_params or {}))
    try:
        strats = cerebro.run()
        strat = strats[0]
    except Exception as e:
        logger.error("回测异常: %s", e)
        import traceback

        traceback.print_exc()
        return None

    report = BacktestReport(strat, initial_cash, name)
    report.print_summary()
    return report


# ============================================================================
# 训练/验证集对比
# ============================================================================


def run_train_val_comparison(
    train_data: Dict[str, pd.DataFrame],
    val_data: Dict[str, pd.DataFrame],
    strategy_class: Type[AShareStrategy],
    initial_cash: float = 20000.0,
    strategy_params: Optional[dict] = None,
    commission_rate: float = 0.0003,
    stamp_duty: float = 0.001,
    verbose: bool = True,
    log_manager: Optional[LogManager] = None,
):
    """分别对训练集和验证集运行回测，并输出对比报告。"""

    print("\n" + "#" * 70)
    print("#  训练集回测 (70% 股票，完整时间序列)")
    print("#" * 70)
    train_report = run_backtest(
        train_data,
        strategy_class,
        initial_cash=initial_cash,
        strategy_params=strategy_params,
        name="训练集",
        commission_rate=commission_rate,
        stamp_duty=stamp_duty,
        verbose=verbose,
    )

    print("\n" + "#" * 70)
    print("#  验证集回测 (20% 股票，完整时间序列)")
    print("#" * 70)
    val_report = run_backtest(
        val_data,
        strategy_class,
        initial_cash=initial_cash,  # 用相同初始资金
        strategy_params=strategy_params,
        name="验证集",
        commission_rate=commission_rate,
        stamp_duty=stamp_duty,
        verbose=verbose,
    )

    # ---- 对比汇总 ----
    if train_report and val_report:
        print("\n" + "#" * 70)
        print("#  策略对比汇总")
        print("#" * 70)
        print(f"  {'指标':<20} {'训练集':>15} {'验证集':>15} {'差异':>15}")
        print("-" * 70)

        tr = train_report
        vr = val_report

        def fmt_pct(v):
            return f"{v:+.2f}%" if v is not None else "N/A"

        def fmt_val(v):
            return f"{v:+,.2f}" if v is not None else "N/A"

        def fmt_int(v):
            return str(int(v)) if v is not None else "N/A"

        print(
            f"  {'收益率':<20} {fmt_pct(tr.total_return_pct):>15} {fmt_pct(vr.total_return_pct):>15} "
            f"{fmt_pct(vr.total_return_pct - tr.total_return_pct):>15}"
        )
        print(
            f"  {'年化收益率':<20} {fmt_pct(tr.annual_return_pct):>15} {fmt_pct(vr.annual_return_pct):>15} "
            f"{fmt_pct(vr.annual_return_pct - tr.annual_return_pct):>15}"
        )
        print(
            f"  {'胜率':<20} {fmt_pct(tr.win_rate_pct):>15} {fmt_pct(vr.win_rate_pct):>15} "
            f"{fmt_pct(vr.win_rate_pct - tr.win_rate_pct):>15}"
        )
        print(
            f"  {'总盈亏(净)':<20} {fmt_val(tr.net_pnl):>15} {fmt_val(vr.net_pnl):>15} "
            f"{fmt_val(vr.net_pnl - tr.net_pnl):>15}"
        )
        print(
            f"  {'总交易次数':<20} {fmt_int(tr.total_trades):>15} {fmt_int(vr.total_trades):>15} "
            f"{fmt_int(vr.total_trades - tr.total_trades):>15}"
        )
        print(
            f"  {'最大回撤':<20} {fmt_pct(tr.max_drawdown_pct):>15} {fmt_pct(vr.max_drawdown_pct):>15} "
            f"{fmt_pct(vr.max_drawdown_pct - tr.max_drawdown_pct):>15}"
        )
        print(
            f"  {'夏普比率':<20} {str(tr.sharpe_ratio):>15} {str(vr.sharpe_ratio):>15} "
        )
        print(f"  {'SQN':<20} {str(tr.sqn):>15} {str(vr.sqn):>15}")
        print("-" * 70)
        print(f"  {'初始资金':<20} {tr.initial_cash:>15,.2f} {vr.initial_cash:>15,.2f}")
        print(f"  {'最终资金':<20} {tr.final_value:>15,.2f} {vr.final_value:>15,.2f}")
        print(
            f"  {'绝对收益':<20} {(tr.final_value - tr.initial_cash):>+15,.2f} {(vr.final_value - vr.initial_cash):>+15,.2f}"
        )
        print("#" * 70)

        # 保存结果到日志
        if log_manager:
            results = get_backtest_results_from_report(train_report, val_report)
            strategy_name = strategy_class.__name__
            log_manager.save_backtest_results(results, strategy_name, "full")

        return tr, vr

    return train_report, val_report


# ============================================================================
# 策略加载器（从 YAML 配置）
# ============================================================================


def load_strategy_from_config(config_path: str) -> Type[AShareStrategy]:
    """
    从 YAML 配置加载策略类。
    配置文件示例见 strategy_template.yaml
    """
    import yaml

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    strategy_name = config.get("strategy", {}).get("name", "CustomStrategy")

    # 动态构建策略类
    class ConfiguredStrategy(AShareStrategy):
        # 设置策略参数
        params = tuple((k, v) for k, v in config.get("params", {}).items())

        def should_buy(self, data):
            """基于配置的执行逻辑。"""
            signals = config.get("signals", {})
            return self._eval_signal(data, signals.get("buy", []))

        def should_sell(self, data):
            """基于配置的执行逻辑。"""
            signals = config.get("signals", {})
            return self._eval_signal(data, signals.get("sell", []))

        def _eval_signal(self, data, conditions):
            """
            评估信号条件列表。
            conditions 示例:
              - type: cross_over
                indicator1: sma_fast
                indicator2: sma_slow
              - type: rsi_gt
                indicator: rsi
                threshold: 50
            """
            if not conditions:
                return False

            # 全部条件必须满足（AND）
            for cond in conditions:
                cond_type = cond.get("type", "")
                ind1 = cond.get("indicator1", "")
                ind2 = cond.get("indicator2", "")
                threshold = cond.get("threshold", 0)

                if cond_type == "cross_over":
                    v1 = getattr(data, ind1, None)
                    v2 = getattr(data, ind2, None)
                    if v1 is None or v2 is None:
                        continue
                    if not (v1[0] > v2[0] and v1[-1] <= v2[-1]):
                        return False

                elif cond_type == "cross_below":
                    v1 = getattr(data, ind1, None)
                    v2 = getattr(data, ind2, None)
                    if v1 is None or v2 is None:
                        continue
                    if not (v1[0] < v2[0] and v1[-1] >= v2[-1]):
                        return False

                elif cond_type == "gt":
                    v = getattr(data, ind, None)
                    if v is None or not (v[0] > threshold):
                        return False

                elif cond_type == "lt":
                    v = getattr(data, ind, None)
                    if v is None or not (v[0] < threshold):
                        return False

                elif cond_type == "value_gt":
                    v = getattr(data, ind, None)
                    if v is None or not (v[0] > threshold):
                        return False

            return True

    ConfiguredStrategy.__name__ = strategy_name
    return ConfiguredStrategy


# ============================================================================
# 内置示例策略（CLI --strategy 参数）
# SMACrossStrategy: 5/20日均线金叉死叉（独立实现，区别于 strategies/sma_golden_cross）
# MomentumStrategy: 从 strategies.momentum 导入（已修复多股票支持）
# ============================================================================


class SMACrossStrategy(AShareStrategy):
    """
    示例策略: SMA 金叉死叉（纯均线版，无 RSI）
    - 快速均线(5日) 上穿 慢速均线(20日) → 买入
    - 快速均线(5日) 下穿 慢速均线(20日) → 卖出
    - 仅在均线多头排列时做多（趋势过滤）
    """

    params = (
        ("fast_period", 5),
        ("slow_period", 20),
        ("trend_period", 20),  # 改为20日均线，信号更多
        ("max_position_ratio", 0.1),
        ("verbose", False),
    )

    def __init__(self):
        super().__init__()
        # 为每只股票创建独立指标
        for data in self.datas:
            sma_fast = bt.ind.SMA(data, period=self.p.fast_period)
            sma_slow = bt.ind.SMA(data, period=self.p.slow_period)
            sma_trend = bt.ind.SMA(data, period=self.p.trend_period)
            crossover = bt.ind.CrossOver(sma_fast, sma_slow)
            self._reg(data, "sma_fast", sma_fast)
            self._reg(data, "sma_slow", sma_slow)
            self._reg(data, "sma_trend", sma_trend)
            self._reg(data, "crossover", crossover)

    def should_buy(self, data):
        # 过滤价格为 0 或 NaN
        if data.close[0] <= 0 or data.close[0] != data.close[0]:
            return False
        sma_fast = self._get(data, "sma_fast")
        sma_slow = self._get(data, "sma_slow")
        sma_trend = self._get(data, "sma_trend")
        if sma_fast is None or sma_slow is None or sma_trend is None:
            return False
        # 过滤零值
        if not (sma_fast[0] > 0 and sma_slow[0] > 0 and sma_trend[0] > 0):
            return False
        # 趋势过滤：价格 > 20日均线（只做多）
        if data.close[0] <= sma_trend[0]:
            return False
        crossover = self._get(data, "crossover")
        return crossover[0] > 0

    def get_buy_reason(self, data):
        """获取买入理由"""
        sma_fast = self._get(data, "sma_fast")
        sma_slow = self._get(data, "sma_slow")
        sma_trend = self._get(data, "sma_trend")
        if sma_fast is not None and sma_slow is not None and sma_trend is not None:
            return f"5日均线({sma_fast[0]:.2f})上穿20日均线({sma_slow[0]:.2f})，且价格({data.close[0]:.2f})>趋势线({sma_trend[0]:.2f})"
        return "SMA金叉买入信号"

    def should_sell(self, data):
        # 过滤价格为 0 或 NaN
        if data.close[0] <= 0 or data.close[0] != data.close[0]:
            return False
        crossover = self._get(data, "crossover")
        if crossover is None:
            return False
        if crossover[0] == 0:
            return False
        return crossover[0] < 0

    def get_sell_reason(self, data):
        """获取卖出理由"""
        sma_fast = self._get(data, "sma_fast")
        sma_slow = self._get(data, "sma_slow")
        if sma_fast is not None and sma_slow is not None:
            return f"5日均线({sma_fast[0]:.2f})下穿20日均线({sma_slow[0]:.2f})"
        return "SMA死叉卖出信号"


# 动态加载 MomentumStrategy（如果存在）
MomentumStrategy = load_strategy_by_name("MomentumStrategy")
if MomentumStrategy is None:
    # 如果没有找到 MomentumStrategy，使用内置的 SMACrossStrategy 作为备选
    MomentumStrategy = SMACrossStrategy


# ============================================================================
# 命令行入口
# ============================================================================


def parse_args():
    parser = argparse.ArgumentParser(description="A 股量化回测系统")
    parser.add_argument(
        "--config", "-c", type=str, default=None, help="策略配置文件路径（YAML）"
    )
    parser.add_argument(
        "--strategy",
        "-s",
        type=str,
        default="sma",
        help="策略名称或类型: sma(SMA金叉) / momentum(动量策略) / 或具体策略类名",
    )
    parser.add_argument("--train", "-t", action="store_true", help="仅运行训练集回测")
    parser.add_argument("--val", "-v", action="store_true", help="仅运行验证集回测")
    parser.add_argument(
        "--cash", type=float, default=20000.0, help="初始资金（默认 20000 元）"
    )
    parser.add_argument(
        "--max-stocks", type=int, default=None, help="最大股票数量（测试用，None=全部）"
    )
    parser.add_argument(
        "--no-cache", action="store_true", help="忽略缓存，强制重新获取数据"
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true", help="静默模式（不打印详细交易日志）"
    )
    parser.add_argument(
        "--parallel", "-p", action="store_true", help="启用多进程并行回测"
    )
    parser.add_argument(
        "--workers", type=int, default=None, help="并行工作进程数（默认为CPU核心数-1）"
    )
    parser.add_argument(
        "--dynamic-split", action="store_true", help="每次回测动态划分数据（防过拟合）"
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="动态划分随机种子（None=每次不同，int=固定可复现）",
    )
    return parser.parse_args()


def main():
    """主入口函数 - 使用新的回测运行器"""
    args = parse_args()

    if args.parallel:
        from parallel_backtest_runner import run_parallel_backtest_with_config

        run_parallel_backtest_with_config(
            config_path=args.config,
            strategy_name=args.strategy,
            train_only=args.train and not args.val,
            val_only=args.val and not args.train,
            max_stocks=args.max_stocks,
            initial_cash=args.cash,
            quiet=args.quiet,
            max_workers=args.workers,
            dynamic_split=args.dynamic_split,
            seed=args.seed,
        )
    else:
        from backtest_runner import run_backtest_with_config

        run_backtest_with_config(
            config_path=args.config,
            strategy_name=args.strategy,
            train_only=args.train and not args.val,
            val_only=args.val and not args.train,
            max_stocks=args.max_stocks,
            initial_cash=args.cash,
            quiet=args.quiet,
            dynamic_split=args.dynamic_split,
            seed=args.seed,
        )

    print("\n回测完成。")


if __name__ == "__main__":
    main()
