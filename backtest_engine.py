"""
A 股量化回测系统 - 回测引擎
包含:
  1. AShareStrategy: A 股专用策略基类（T+1 追踪 + 涨跌停过滤）
  2. CommInfoAShare: A 股手续费体系（佣金 + 印花税）
  3. DataFeedAShare: Backtrader PandasData 包装（支持涨跌停字段）
"""

import backtrader as bt
import logging
from typing import Dict, Optional

# 获取策略日志器
_logger = logging.getLogger("strategy")


# ============================================================================
# 涨跌停相关常量
# ============================================================================

LIMIT_RATE_MAINBOARD = 0.10
LIMIT_RATE_STAR = 0.20


def get_limit_rate(code: str) -> float:
    """根据股票代码判断涨跌停幅度。"""
    code = str(code)
    if code.startswith("688"):
        return LIMIT_RATE_STAR
    return LIMIT_RATE_MAINBOARD


# ============================================================================
# A 股数据 Feed（支持涨跌停字段）
# ============================================================================


class AShareData(bt.feeds.PandasData):
    """Backtrader PandasData 包装，支持 A 股特有字段（pct_chg、amount）。"""

    params = (
        ("datetime", "datetime"),
        ("open", "open"),
        ("high", "high"),
        ("low", "low"),
        ("close", "close"),
        ("volume", "volume"),
        ("pct_chg", "pct_chg"),
        ("amount", "amount"),
    )


# ============================================================================
# A 股手续费体系
# ============================================================================


class CommInfoAShare(bt.CommInfoBase):
    """A 股完整手续费体系。"""

    params = (
        ("commission", 0.0003),
        ("stamp_duty", 0.001),
        ("transfer_fee", 0.00001),
        ("min_commission", 5.0),
        ("is_sh", True),
    )

    def _getcommission(self, size, price, pseudoexec):
        turnover = abs(size) * price
        commission = max(turnover * self.p.commission, self.p.min_commission)
        if self.p.is_sh:
            commission += turnover * self.p.transfer_fee
        return commission

    def getcommission(self, size, price):
        return self._getcommission(size, price, pseudoexec=True)


class StampDutyScheme(bt.CommInfoBase):
    """专门处理印花税的佣金子类。"""

    params = (
        ("stamp_duty", 0.001),
        ("min_commission", 5.0),
        ("is_sh", True),
        ("transfer_fee", 0.00001),
    )

    def _getcommission(self, size, price, pseudoexec):
        if size > 0:
            return 0.0
        return self._calc_cost(size, price)

    def _calc_cost(self, size, price):
        turnover = abs(size) * price
        commission = max(turnover * 0.0003, self.p.min_commission)
        if self.p.is_sh:
            commission += turnover * self.p.transfer_fee
        commission += turnover * self.p.stamp_duty
        return commission


def create_ashare_commission(
    cerebro: bt.Cerebro,
    commission_rate: float = 0.0003,
    stamp_duty: float = 0.001,
    transfer_fee: float = 0.00001,
    min_commission: float = 5.0,
    is_sh: bool = True,
):
    cerebro.broker.setcommission(
        commission=commission_rate,
        margin=None,
        mult=1.0,
        commtype=bt.CommInfoBase.COMM_PERC,
        stocklike=True,
        percabs=True,
    )
    cerebro._ashare_params = {
        "stamp_duty": stamp_duty,
        "transfer_fee": transfer_fee,
        "commission_rate": commission_rate,
        "min_commission": min_commission,
        "is_sh": is_sh,
    }


def calc_sell_cost(size: int, price: float, is_sh: bool = True) -> float:
    if size <= 0:
        return 0.0
    turnover = abs(size) * price
    stamp_duty = turnover * 0.001
    transfer_fee = turnover * 0.00001 if is_sh else 0.0
    return stamp_duty + transfer_fee


# ============================================================================
# A 股策略基类（T+1 追踪 + 涨跌停过滤）
# ============================================================================


class AShareStrategy(bt.Strategy):
    """
    A 股专用策略基类。

    1. T+1 追踪:
       - 记录每只股票的买入日期，禁止同日卖出（T 日买的，T+1 才能卖）
       - 追踪每只股票的持仓数量

    2. 涨跌停过滤:
       - 涨停日：跳过买入
       - 跌停日：跳过卖出
       - 支持主板 ±10%、科创板 ±20%

    3. 策略信号接口:
       - 子类实现 should_buy() → 返回 True 时自动买入
       - 子类实现 should_sell() → 返回 True 时自动卖出
       - 子类实现 get_position_size() → 返回买入股数（默认全仓10%）

    使用方式:
        class MyStrategy(AShareStrategy):
            def should_buy(self, data):
                return self.data.sma > self.data.sma_long

            def should_sell(self, data):
                return self.data.sma < self.data.sma_long

        cerebro.addstrategy(MyStrategy)
    """

    limit_mode = "skip"

    params = (
        ("max_position_ratio", 0.1),
        ("min_buy_amount", 100.0),
        ("verbose", True),
    )

    def __init__(self):
        self._ASHARE_pending_orders = {}
        self._ASHARE_pending_buys = {}
        self._ASHARE_buy_dates = {}
        self._ASHARE_position_size = {}
        self._ASHARE_limit_status = {}
        self._closed_trades = []

    # ----------------------------------------------------------------
    # 指标注册 API（子类在 __init__ 中调用）
    # ----------------------------------------------------------------

    def _reg(self, data, name: str, indicator):
        setattr(self, f"{name}_{data._name}", indicator)

    def _get(self, data, name: str):
        return getattr(self, f"{name}_{data._name}", None)

    # ----------------------------------------------------------------
    # 涨跌停检测
    # ----------------------------------------------------------------

    def _update_limit_status(self, data):
        code = data._name

        if len(data) < 2:
            self._ASHARE_limit_status[code] = (False, False)
            return

        prev_close = data.close[-1]
        if prev_close <= 0 or prev_close != prev_close:
            self._ASHARE_limit_status[code] = (False, False)
            return

        limit_rate = get_limit_rate(code)
        limit_up = prev_close * (1 + limit_rate)
        limit_down = prev_close * (1 - limit_rate)

        close_today = data.close[0]
        high_today = data.high[0]
        is_limit_up = (
            close_today >= limit_up * 0.9999 or high_today >= limit_up * 0.9999
        )

        low_today = data.low[0]
        is_limit_down = (
            close_today <= limit_down * 1.0001 or low_today <= limit_down * 1.0001
        )

        self._ASHARE_limit_status[code] = (is_limit_up, is_limit_down)

    def is_limit_up(self, data) -> bool:
        self._update_limit_status(data)
        return self._ASHARE_limit_status.get(data._name, (False, False))[0]

    def is_limit_down(self, data) -> bool:
        self._update_limit_status(data)
        return self._ASHARE_limit_status.get(data._name, (False, False))[1]

    def get_limit_prices(self, data) -> tuple:
        if len(data) < 2:
            return (float("nan"), float("nan"))

        prev_close = data.close[-1]
        if prev_close <= 0:
            return (float("nan"), float("nan"))

        limit_rate = get_limit_rate(data._name)
        return (
            prev_close * (1 + limit_rate),
            prev_close * (1 - limit_rate),
        )

    # ==================================================================
    # T+1 规则
    # ==================================================================

    def can_sell_today(self, data) -> bool:
        code = data._name
        current_date = data.datetime.date(0)
        buy_date = self._ASHARE_buy_dates.get(code)
        if buy_date is None:
            return True
        return buy_date != current_date

    def _record_buy(self, data, size):
        code = data._name
        buy_date = data.datetime.date(0)
        self._ASHARE_buy_dates[code] = buy_date
        self._ASHARE_position_size[code] = (
            self._ASHARE_position_size.get(code, 0) + size
        )

        if self.params.verbose:
            limit_up, limit_down = self.get_limit_prices(data)
            self.log(
                f"[买入] {code} {size}股 @{data.close[0]:.2f} "
                f"(涨停:{limit_up:.2f} 跌停:{limit_down:.2f})",
                level=logging.INFO,
            )

    def _record_sell(self, data, size):
        code = data._name
        self._ASHARE_position_size[code] = max(
            0, self._ASHARE_position_size.get(code, 0) - size
        )

        if self.params.verbose:
            self.log(f"[卖出] {code} {size}股 @{data.close[0]:.2f}", level=logging.INFO)

    def _record_sell_close(self, data):
        code = data._name
        self._ASHARE_position_size[code] = 0

        if self.params.verbose:
            self.log(f"[平仓] {code}", level=logging.INFO)

    # ==================================================================
    # 买入/卖出下单（带涨跌停 + T+1 过滤）
    # ==================================================================

    def try_buy(self, data, size: Optional[int] = None):
        if self._ASHARE_pending_orders.get(data._name):
            return

        if self.is_limit_up(data):
            if self.params.verbose:
                limit_up, _ = self.get_limit_prices(data)
                self.log(
                    f"[跳过买入] {data._name} 今日涨停 {data.close[0]:.2f} >= 涨停价 {limit_up:.2f}",
                    level=logging.WARNING,
                )
            return

        if size is None:
            size = self.get_position_size(data)

        if size <= 0:
            return

        self._ASHARE_pending_orders[data._name] = True
        order = self.buy(data=data, size=size)
        return order

    def try_sell(self, data, size: Optional[int] = None):
        if self._ASHARE_pending_orders.get(data._name + "_sell"):
            return

        if not self.can_sell_today(data):
            if self.params.verbose:
                buy_date = self._ASHARE_buy_dates.get(data._name)
                self.log(
                    f"[T+1跳过] {data._name} 买入于 {buy_date}，今日不可卖",
                    level=logging.WARNING,
                )
            return

        if self.is_limit_down(data):
            if self.params.verbose:
                _, limit_down = self.get_limit_prices(data)
                self.log(
                    f"[跳过卖出] {data._name} 今日跌停 {data.close[0]:.2f} <= 跌停价 {limit_down:.2f}",
                    level=logging.WARNING,
                )
            return

        pos = self.getposition(data)
        if pos.size <= 0:
            return

        if size is None:
            size = pos.size

        self._ASHARE_pending_orders[data._name + "_sell"] = True
        order = self.sell(data=data, size=size)
        return order

    def close_position(self, data):
        pos = self.getposition(data)
        if pos.size <= 0:
            return

        if not self.can_sell_today(data):
            if self.params.verbose:
                buy_date = self._ASHARE_buy_dates.get(data._name)
                self.log(
                    f"[T+1跳过平仓] {data._name} 买入于 {buy_date}，今日不可卖",
                    level=logging.WARNING,
                )
            return

        self.try_sell(data, size=pos.size)

    # ==================================================================
    # 策略信号接口（子类实现）
    # ==================================================================

    def should_buy(self, data) -> bool:
        raise NotImplementedError("子类必须实现 should_buy()")

    def get_buy_reason(self, data) -> str:
        """获取买入理由"""
        return "策略信号触发买入"

    def should_sell(self, data) -> bool:
        raise NotImplementedError("子类必须实现 should_sell()")

    def get_sell_reason(self, data) -> str:
        """获取卖出理由"""
        return "策略信号触发卖出"

    def get_position_size(self, data) -> int:
        price = data.close[0]
        if price <= 0:
            return 0

        cash = self.broker.getcash()
        total_value = self.broker.getvalue()

        target_value = total_value * self.params.max_position_ratio
        if target_value > cash:
            target_value = cash

        size = int(target_value / price / 100) * 100
        size = max(0, size)

        if size * price < self.params.min_buy_amount:
            return 0

        return size

    # ==================================================================
    # Backtrader 生命周期钩子
    # ==================================================================

    def log(self, txt, dt=None, level=logging.INFO):
        """
        日志记录方法

        Args:
            txt: 日志内容
            dt: 日期时间（默认使用第一个数据的当前日期）
            level: 日志级别，默认 INFO
                  - logging.DEBUG: 筛选过程、候选股状态
                  - logging.INFO: 交易决策、订单执行
                  - logging.WARNING: 风控事件（涨跌停、T+1跳过）
                  - logging.ERROR: 订单失败
        """
        if not self.params.verbose:
            return
        dt = dt or self.datas[0].datetime.date(0)
        _logger.log(level, f"[{dt.isoformat()}] [{self.__class__.__name__}] {txt}")

    def notify_order(self, order):
        code = order.data._name if order.data else "unknown"

        if order.status in [order.Completed]:
            if order.isbuy():
                self._record_buy(order.data, order.executed.size)
                self._ASHARE_pending_orders[code] = False
                setattr(self, "_trade_buy_price_" + code, order.executed.price)
                setattr(self, "_trade_buy_size_" + code, order.executed.size)
                # 记录买入理由
                buy_reason = self.get_buy_reason(order.data)
                setattr(self, "_trade_buy_reason_" + code, buy_reason)
            elif order.issell():
                self._record_sell(order.data, order.executed.size)
                self._ASHARE_pending_orders[code + "_sell"] = False
                sell_price = order.executed.price
                buy_price = getattr(self, "_trade_buy_price_" + code, 0.0)
                trade_size = getattr(self, "_trade_buy_size_" + code, 0)
                if buy_price and trade_size:
                    buy_date = None
                    if code in self._ASHARE_buy_dates:
                        bd = self._ASHARE_buy_dates[code]
                        buy_date = (
                            bd.strftime("%Y-%m-%d")
                            if hasattr(bd, "strftime")
                            else str(bd)
                        )
                    sell_date = self.datas[0].datetime.date(0)
                    sell_date_str = (
                        sell_date.strftime("%Y-%m-%d")
                        if hasattr(sell_date, "strftime")
                        else str(sell_date)
                    )
                    cost = abs(buy_price * trade_size)
                    pnl_comm = order.executed.pnl
                    pnl_pct = (pnl_comm / cost * 100) if cost > 0 else 0.0
                    if self.params.verbose:
                        self.log(
                            f"[平仓] 盈亏: {pnl_comm:+.2f} 元 ({pnl_pct:+.2f}%) "
                            f"代码: {code} 买入价: {buy_price:.2f} 持仓: {trade_size}股",
                            level=logging.INFO,
                        )
                    # 获取买卖理由
                    buy_reason = getattr(
                        self, "_trade_buy_reason_" + code, "未知买入理由"
                    )
                    sell_reason = self.get_sell_reason(order.data)
                    self._closed_trades.append(
                        {
                            "code": code,
                            "buy_date": buy_date,
                            "sell_date": sell_date_str,
                            "buy_price": float(buy_price),
                            "sell_price": float(sell_price),
                            "pnl": float(pnl_comm),
                            "pnl_pct": float(pnl_pct),
                            "size": int(trade_size),
                            "buy_reason": buy_reason,
                            "sell_reason": sell_reason,
                        }
                    )
                    setattr(self, "_trade_buy_price_" + code, 0.0)
                    setattr(self, "_trade_buy_size_" + code, 0)
                    setattr(self, "_trade_buy_reason_" + code, "")

        elif order.status in [order.Canceled, order.Rejected]:
            self._ASHARE_pending_orders[code] = False
            self._ASHARE_pending_orders[code + "_sell"] = False
            if self.params.verbose:
                self.log(f"[订单失败] {code} 取消/拒绝", level=logging.ERROR)

    def notify_trade(self, trade):
        pass

    def next(self):
        for data in self.datas:
            if len(data) < 2:
                continue

            pos = self.getposition(data)
            has_position = pos.size > 0

            if has_position:
                if self.should_sell(data):
                    self.try_sell(data)
            else:
                if self.should_buy(data):
                    self.try_buy(data)

    def stop(self):
        if self.params.verbose:
            print(f"\n[{self.datetime.date(0)}] === 回测结束，平仓 ===")
        for data in self.datas:
            pos = self.getposition(data)
            if pos.size > 0:
                self.close_position(data)
