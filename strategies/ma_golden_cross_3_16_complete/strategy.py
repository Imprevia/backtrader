from backtest_engine import AShareStrategy, logging
import backtrader as bt


class MAGoldenCross3_16_Complete(AShareStrategy):
    """
    完整版右侧均线金叉3穿16策略 - 实现所有卖出条件

    卖出条件：
    1. 止损：跌破16日均线且3日内无法收回，或亏损达5%
    2. 止盈（ATR动态）：
       - 短线：盈利达到2×ATR可减半仓
       - 中线：3日线下穿16日线（死叉）清仓
       - 或股价远离均线、乖离率过大时分批止盈
    """

    params = (
        ("ma_fast_period", 3),
        ("ma_slow_period", 25),  # 16 → 25（更长周期更稳定）
        ("trend_ma_period", 20),
        ("stop_loss_percent", 5.0),  # 4.5 → 5.0（恢复止损给波动空间）
        ("recovery_days", 3),  # 保持3日
        # ==========================================================
        # ATR止盈参数（推荐组合优化）
        # ==========================================================
        ("atr_period", 14),  # 保持14
        ("atr_partial_mult", 2.5),  # 3.0 → 2.5（2.5×ATR减半仓锁定利润）
        ("atr_full_mult", 4.0),  # 5.0 → 4.0（4×ATR完全止盈，减少回吐）
        # ==========================================================
        # 选股参数（收紧以提高胜率）
        # ==========================================================
        ("lookback_low_period", 30),  # 保持30
        ("min_rebound_pct", 4.0),  # 5.0 → 4.0（略放宽入场）
        ("max_rebound_pct", 25.0),  # 30.0 → 25.0（限制过大反弹）
        ("use_rebound_filter", True),  # 保持True
        ("volume_surge_ratio", 1.3),  # 1.2 → 1.3（提高放量要求）
        ("volume_lookback", 20),  # 保持20
        ("volume_confirm_days", 3),  # 保持3
        ("max_candidates", 25),  # 20 → 25（略增加候选）
        ("max_pending_days", 20),  # 15 → 20（给候选股更多时间）
        # ==========================================================
        # 过滤参数（提高过滤要求）
        # ==========================================================
        ("rsi_period", 14),  # 保持14
        ("rsi_threshold", 30),  # 恢复 30（RSI 28 版本导致交易质量下降）
        ("use_rsi_filter", True),  # 保持True
        ("use_trend_filter", True),  # 保持True（开启趋势过滤）
        ("trend_confirmation_bars", 5),  # 3 → 5（严格趋势确认）
        # ==========================================================
        # 乖离率参数
        # ==========================================================
        ("max_bias_for_holding", 15.0),  # 20.0 → 15.0（收紧乖离率）
        # ==========================================================
        # 仓位参数（推荐组合优化）
        # ==========================================================
        ("max_position_ratio", 0.25),  # 0.20 → 0.25（仓位提到25%）
    )

    def __init__(self):
        super().__init__()

        # 记录每只股票的买入价格和买入日期
        self.entry_prices = {}
        self.entry_bars = {}
        self.half_sold = {}  # 标记是否已减半仓

        # 记录分批止盈事件（用于HTML展示）
        self._partial_sells = []

        # ==========================================================
        # 选股相关状态
        # ==========================================================
        self._golden_cross_bars = {}  # 每只股票的金叉确认日期(bar序号)
        self._candidate_scores = {}  # 候选股质量评分 {code: score}
        self._pending_buy_codes = {}  # 待买入候选股 {code: (score, bar, price, pending_days)}

        # 预计算需要的最小历史数据量
        self._min_data_bars = (
            max(
                self.params.ma_slow_period,
                self.params.volume_lookback,
                self.params.lookback_low_period,
            )
            + 5
        )

        # 为每只股票注册指标
        for data in self.datas:
            self._reg(
                data,
                "ma_fast",
                bt.indicators.SMA(data.close, period=self.params.ma_fast_period),
            )
            self._reg(
                data,
                "ma_slow",
                bt.indicators.SMA(data.close, period=self.params.ma_slow_period),
            )
            self._reg(
                data,
                "trend_ma",
                bt.indicators.SMA(data.close, period=self.params.trend_ma_period),
            )
            # 成交量均线
            self._reg(
                data,
                "volume_sma",
                bt.indicators.SMA(data.volume, period=self.params.volume_lookback),
            )
            # ATR指标（用于动态止盈，14日标准ATR）
            self._reg(
                data,
                "atr",
                bt.indicators.ATR(data, period=self.params.atr_period),
            )

            # RSI指标（动态计算，不预注册避免除零）
            # RSI在价格横盘时avg_gain=avg_loss=0导致除零

    def should_buy(self, data) -> bool:
        """
        选股 + 买入完整流程（优化版）

        选股条件：
        1. 趋势确认：20日线向上 + 股价从近期低点反弹5%-15%
        2. RSI过滤：RSI>50（只做强势股）
        3. 即将金叉：3日均线在16日均线上方不远处（差距<2%）
        4. 成交量配合：金叉前3日内有放量（超过20日均量1.5倍）

        买入条件：
        - 候选股在金叉确认当天收盘后加入待买
        - 次日开盘价附近买入，或回踩16日均线不破时买入
        - 候选股最多等待5个交易日，超时清理
        """
        code = data._name

        # 数据量不足时跳过
        if len(data.close) < self._min_data_bars:
            return False

        ma_fast = self._get(data, "ma_fast")
        ma_slow = self._get(data, "ma_slow")
        trend_ma = self._get(data, "trend_ma")
        volume_sma = self._get(data, "volume_sma")

        if ma_fast is None or ma_slow is None or trend_ma is None or volume_sma is None:
            return False

        current_price = data.close[0]
        current_bar = len(self)

        # ==========================================================
        # 第一步：清理超时的候选股
        # ==========================================================
        self._cleanup_stale_candidates(code, current_bar)

        # ==========================================================
        # 第二步：选股阶段 - 识别即将金叉的候选股
        # ==========================================================
        self._screen_candidate(
            data,
            ma_fast,
            ma_slow,
            trend_ma,
            volume_sma,
            current_price,
            current_bar,
        )

        # ==========================================================
        # 第三步：买入阶段 - 候选股金叉确认后次日买入
        # ==========================================================
        return self._check_buy_signal(data, ma_fast, ma_slow, current_price, code)

    def _cleanup_stale_candidates(self, code: str, current_bar: int):
        """清理超时的候选股（等待超过max_pending_days仍未金叉则移除）"""
        if code in self._pending_buy_codes:
            _, entry_bar, _, pending_days = self._pending_buy_codes[code]
            days_waited = current_bar - entry_bar
            if days_waited > self.params.max_pending_days:
                self.log(f"[选股清理] {code} 超时未金叉({days_waited}日)，移除候选")
                del self._pending_buy_codes[code]

    def _screen_candidate(
        self,
        data,
        ma_fast,
        ma_slow,
        trend_ma,
        volume_sma,
        current_price,
        current_bar,
    ):
        """
        选股筛选：识别刚发生金叉的候选股，满足条件则加入待买列表

        选股条件：
        1. 趋势确认：20日线向上 + 反弹4%-25%
        2. RSI过滤：RSI>30（动态计算避免除零）
        3. 金叉刚发生：3日均线从下方向上穿越25日均线
        4. 放量配合：金叉当日或前3日有放量1.3倍+
        """
        code = data._name

        # ==========================================================
        # 死叉检测：如果3日均线下穿25日均线，移除候选名单
        # ==========================================================
        if code in self._pending_buy_codes:
            ma_fast_val = ma_fast[0]
            ma_slow_val = ma_slow[0]
            ma_fast_prev = self._get_prev_ma_fast(data)
            ma_slow_prev = self._get_prev_ma_slow(data)

            if ma_fast_prev is not None and ma_slow_prev is not None:
                # 死叉：前一天快线在慢线上方，当天快线在慢线下方
                death_cross = (ma_fast_prev > ma_slow_prev) and (
                    ma_fast_val <= ma_slow_val
                )
                if death_cross:
                    self.log(f"[DEBUG] {code} 死叉形成，移除候选", level=logging.DEBUG)
                    del self._pending_buy_codes[code]
                    return

        # 跳过已有持仓的股票
        if self.getposition(data).size > 0:
            return

        # 如果已在待买列表（金叉即将发生已确认)，更新等待天数
        if code in self._pending_buy_codes:
            score, entry_bar, entry_price, _ = self._pending_buy_codes[code]
            self._pending_buy_codes[code] = (
                score,
                entry_bar,
                entry_price,
                current_bar - entry_bar + 1,
            )
            return

        ma_fast_val = ma_fast[0]
        ma_slow_val = ma_slow[0]

        # ==========================================================
        # 条件1: RSI过滤（RSI>40，动态计算避免除零）
        # ==========================================================
        rsi_val = self._compute_rsi(data)
        if self.params.use_rsi_filter and rsi_val <= self.params.rsi_threshold:
            self.log(
                f"[DEBUG] {code} RSI={rsi_val:.1f} <= {self.params.rsi_threshold} 过滤",
                level=logging.DEBUG,
            )
            return

        # ==========================================================
        # 条件2: 趋势方向过滤（20日线向上）
        # ==========================================================
        if self.params.use_trend_filter and not self._check_trend_upward(
            data, trend_ma
        ):
            self.log(f"[DEBUG] {code} 趋势向下过滤", level=logging.DEBUG)
            return

        # ==========================================================
        # 条件3: 趋势确认（反弹5%-15%）
        # ==========================================================
        if not self._check_trend_confirmation(data, current_price, trend_ma):
            self.log(f"[DEBUG] {code} 趋势确认未通过", level=logging.DEBUG)
            return

        # ==========================================================
        # 条件4: 金叉刚发生（3日均线从下方向上穿越25日均线）
        # ==========================================================
        if ma_slow_val <= 0:
            self.log(f"[DEBUG] {code} 均线值异常过滤", level=logging.DEBUG)
            return

        ma_fast_prev = self._get_prev_ma_fast(data)
        ma_slow_prev = self._get_prev_ma_slow(data)

        if ma_fast_prev is None or ma_slow_prev is None:
            self.log(f"[DEBUG] {code} 历史均线数据不足", level=logging.DEBUG)
            return

        # 金叉刚发生：前一天快线在慢线下方，当天快线在慢线上方
        golden_cross = ma_fast_prev <= ma_slow_prev and ma_fast_val > ma_slow_val
        if not golden_cross:
            self.log(f"[DEBUG] {code} 金叉未形成", level=logging.DEBUG)
            return

        ma_fast_prev = self._get_prev_ma_fast(data)
        ma_slow_prev = self._get_prev_ma_slow(data)

        if ma_fast_prev is None or ma_slow_prev is None:
            self.log(f"[DEBUG] {code} 历史均线数据不足", level=logging.DEBUG)
            return

        # 金叉即将发生：快线在慢线下方，且快线接近慢线（差距 < 3%）
        if ma_slow_val <= 0:
            self.log(f"[DEBUG] {code} 均线值异常过滤", level=logging.DEBUG)
            return

        ma_fast_prev = self._get_prev_ma_fast(data)
        ma_slow_prev = self._get_prev_ma_slow(data)

        if ma_fast_prev is None or ma_slow_prev is None:
            self.log(f"[DEBUG] {code} 历史均线数据不足", level=logging.DEBUG)
            return

        # 快线在慢线下方（尚未金叉）
        if ma_fast_prev >= ma_slow_prev:
            self.log(f"[DEBUG] {code} 快线未在慢线下方", level=logging.DEBUG)
            return

        # 快线接近慢线，差距 < 3%
        gap_ratio = (ma_slow_val - ma_fast_val) / ma_slow_val
        if gap_ratio >= 0.03:
            self.log(
                f"[DEBUG] {code} 快线远离慢线(差距{gap_ratio * 100:.1f}%)",
                level=logging.DEBUG,
            )
            return

        # ==========================================================
        # 条件5: 成交量配合（金叉前3日内有放量1.3倍+）
        # ==========================================================
        if not self._check_volume_surge(data, volume_sma):
            self.log(f"[DEBUG] {code} 成交量未放量", level=logging.DEBUG)
            return

        # ==========================================================
        # 计算候选股质量评分，并加入待买列表
        # ==========================================================
        score = self._calculate_candidate_score(data, current_price, trend_ma)

        # 记录待买入候选股（金叉刚发生）
        self._pending_buy_codes[code] = (score, current_bar, current_price, 1)
        self._candidate_scores[code] = score
        self.log(
            f"[选股] {code} 加入候选, 评分:{score:.1f}, RSI:{rsi_val:.1f}, 现价:{current_price:.2f}"
        )

    def _check_trend_upward(self, data, trend_ma) -> bool:
        """
        趋势方向过滤：检查20日均线是否连续N日向上
        """
        lookback = self.params.trend_confirmation_bars
        if len(data.close) < lookback + 1:
            return False

        # 检查最近N日趋势线是否持续向上
        trend_vals = [trend_ma[-i] for i in range(1, lookback + 1)]
        for i in range(len(trend_vals) - 1):
            if trend_vals[i] <= trend_vals[i + 1]:
                return False
        return True

    def _check_trend_confirmation(self, data, current_price, trend_ma) -> bool:
        """
        趋势确认：
        1. 股价从近期低点反弹5%-15%（参数化）
        2. 或站上20日均线
        """
        if self.params.use_rebound_filter:
            # 计算近期低点
            lookback = self.params.lookback_low_period
            if len(data.close) < lookback + 5:
                return False
            recent_prices = [data.close[-i] for i in range(1, lookback + 1)]
            min_price = min(recent_prices)
            rebound_pct = (current_price - min_price) / min_price * 100

            # 反弹幅度在5%-15%之间，或已站上20日线
            trend_ok = (
                self.params.min_rebound_pct
                <= rebound_pct
                <= self.params.max_rebound_pct
            ) or (current_price > trend_ma[0])
            return trend_ok
        else:
            # 只要求站上20日线
            return current_price > trend_ma[0]

    def _check_volume_surge(self, data, volume_sma) -> bool:
        """
        成交量配合：金叉前N日内有放量
        """
        volume_lookback = self.params.volume_confirm_days
        if len(data.volume) < volume_lookback + 1:
            return False

        # 检查前N日是否有放量（使用参数化阈值1.5）
        for i in range(1, volume_lookback + 1):
            vol_today = data.volume[-i]
            vol_avg = volume_sma[-i] if i > 0 else volume_sma[0]
            if vol_avg <= 0:
                continue
            if vol_today >= vol_avg * self.params.volume_surge_ratio:
                return True
        return False

    def _compute_rsi(self, data) -> float:
        """
        动态计算RSI指标（避免Backtrader预注册指标在价格横盘时除零崩溃）

        使用标准RSI公式：RSI = 100 - 100/(1+RS)
        其中RS = avg_gain / avg_loss
        当avg_loss=0时返回100（极端强势）
        当avg_gain=avg_loss=0时返回50（无波动）
        """
        period = self.params.rsi_period
        if len(data.close) < period + 1:
            return 50.0  # 数据不足时返回中性值

        deltas = []
        for i in range(1, period + 1):
            deltas.append(data.close[-i + 1] - data.close[-i])

        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]

        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period

        if avg_loss == 0:
            return 100.0  # 无损失，极度强势
        rs = avg_gain / avg_loss
        rsi = 100.0 - 100.0 / (1.0 + rs)
        return rsi

    def _calculate_candidate_score(self, data, current_price, trend_ma) -> float:
        """
        计算候选股质量评分（分数越高越好）

        评分维度：
        1. 放量强度：放量越多分数越高（权重40%）
        2. 趋势强度：站上20日线越多分数越高（权重30%）
        3. 相对强弱：从低点反弹幅度适中加分（权重30%）
        """
        score = 0.0

        volume_sma = self._get(data, "volume_sma")
        total_surge = 0.0
        volume_lookback = self.params.volume_confirm_days
        for i in range(1, volume_lookback + 1):
            vol_today = data.volume[-i]
            vol_avg = volume_sma[-i] if i > 0 else volume_sma[0]
            if vol_avg > 0:
                total_surge += vol_today / vol_avg
        avg_surge = total_surge / volume_lookback
        if avg_surge >= self.params.volume_surge_ratio:
            volume_score = min(avg_surge / 2.5, 1.0) * 40
        else:
            volume_score = 0
        score += volume_score

        trend_ma_val = trend_ma[0]
        if trend_ma_val > 0:
            trend_strength = (current_price - trend_ma_val) / trend_ma_val * 100
            trend_score = max(0, min(trend_strength / 5.0, 1.0)) * 30
            score += trend_score

        lookback = self.params.lookback_low_period
        if len(data.close) >= lookback:
            recent_prices = [data.close[-i] for i in range(1, lookback + 1)]
            min_price = min(recent_prices)
            rebound_pct = (current_price - min_price) / min_price * 100
            if (
                self.params.min_rebound_pct
                <= rebound_pct
                <= self.params.max_rebound_pct
            ):
                rebound_score = (1.0 - abs(rebound_pct - 10.0) / 5.0) * 30
            else:
                rebound_score = 0
            score += rebound_score

        return score

    def _check_buy_signal(self, data, ma_fast, ma_slow, current_price, code) -> bool:
        """
        买入信号检查

        买入条件：
        1. 股票在待买列表中（T日金叉已确认）
        2. 收盘价站稳在25日均线上方
        """
        if code not in self._pending_buy_codes:
            return False

        ma_slow_val = ma_slow[0]
        ma_fast_val = ma_fast[0]
        if ma_slow_val <= 0 or ma_fast_val <= 0:
            return False

        if current_price <= ma_slow_val:
            return False

        score, entry_bar, entry_price, pending_days = self._pending_buy_codes[code]
        if code not in self._golden_cross_bars:
            self._golden_cross_bars[code] = len(self)
            self.log(
                f"[金叉确认] {code} @ {current_price:.2f}, 25日线:{ma_slow_val:.2f}, 等{pending_days}日"
            )

        self._pending_buy_codes.pop(code, None)
        return True

    def should_sell(self, data) -> bool:
        """完整卖出条件判断"""
        position = self.getposition(data)
        if position.size <= 0:
            return False

        current_price = data.close[0]
        buy_price = position.price
        ma_fast = self._get(data, "ma_fast")
        ma_slow = self._get(data, "ma_slow")
        # 动态计算乖离率（避免预计算指标的除零问题）
        ma_slow_val = ma_slow[0] if ma_slow is not None else 0
        bias = (
            (current_price - ma_slow_val) / (ma_slow_val + 1e-10) * 100
            if ma_slow_val != 0
            else 0.0
        )

        if ma_fast is None or ma_slow is None:
            return False

        # 获取股票代码
        code = data._name
        entry_bar = self.entry_bars.get(code, 0)
        current_bar = len(self)
        half_sold = self.half_sold.get(code, False)

        # 1. 死叉信号 - 清仓
        if len(data.close) > 1:
            ma_fast_prev = self._get_prev_ma_fast(data)
            ma_slow_prev = self._get_prev_ma_slow(data)
            if ma_fast_prev is not None and ma_slow_prev is not None:
                death_cross = (ma_fast < ma_slow) and (ma_fast_prev >= ma_slow_prev)
                if death_cross:
                    # 清除状态
                    self.entry_prices.pop(code, None)
                    self.entry_bars.pop(code, None)
                    self.half_sold.pop(code, None)
                    return True

        # 2. 止损条件
        below_ma_slow = current_price < ma_slow
        stop_loss_triggered = (buy_price - current_price) / buy_price > (
            self.params.stop_loss_percent / 100
        )

        # 实现安全的"3日内无法收回"止损逻辑
        recovery_failed = False
        if below_ma_slow and current_bar - entry_bar >= self.params.recovery_days:
            # 检查过去3天是否都低于16日均线（安全的方式）
            recovery_failed = True
            for i in range(min(self.params.recovery_days, current_bar - entry_bar)):
                if current_bar - i <= entry_bar:
                    break
                # 安全地获取历史价格和均线
                if len(data.close) > i and len(ma_slow.array) > i:
                    historical_price = data.close[-i] if i > 0 else current_price
                    historical_ma_slow = ma_slow[-i] if i > 0 else ma_slow[0]
                    if historical_price >= historical_ma_slow:
                        recovery_failed = False
                        break

        # 止损条件：跌破16日均线且3日内无法收回，或亏损达5%
        if (below_ma_slow and recovery_failed) or stop_loss_triggered:
            # 清除状态
            self.entry_prices.pop(code, None)
            self.entry_bars.pop(code, None)
            self.half_sold.pop(code, None)
            return True

        # 3. ATR动态止盈（替代固定百分比止盈）
        # 获取ATR值
        atr_ind = self._get(data, "atr")
        atr_val = atr_ind[0] if atr_ind is not None else 0
        if atr_val <= 0:
            atr_val = current_price * 0.02  # ATR失效时默认用2%价格波动

        # 3a. 乖离率过大止盈（保留，作为辅助风控）
        if abs(bias) > self.params.max_bias_for_holding:
            if not half_sold:
                # 第一次乖离率过大，减半仓
                self._execute_partial_sell(data, position.size // 2)
                self.half_sold[code] = True
                return False  # 不完全卖出
            else:
                # 已经减半仓，现在清仓
                self.entry_prices.pop(code, None)
                self.entry_bars.pop(code, None)
                self.half_sold.pop(code, None)
                return True

        # 3b. ATR动态止盈：盈利达到2×ATR减半仓，3×ATR完全止盈
        profit_pips = current_price - buy_price  # 盈利空间（价格差）
        atr_partial_target = self.params.atr_partial_mult * atr_val  # 2×ATR
        atr_full_target = self.params.atr_full_mult * atr_val  # 3×ATR

        # 盈利达到3×ATR，完全止盈
        if profit_pips >= atr_full_target:
            self.entry_prices.pop(code, None)
            self.entry_bars.pop(code, None)
            self.half_sold.pop(code, None)
            return True

        # 盈利达到2×ATR，未减仓则减半仓
        if profit_pips >= atr_partial_target:
            if not half_sold:
                self._execute_partial_sell(data, position.size // 2)
                self.half_sold[code] = True
                return False  # 不完全卖出

        return False

    def _execute_partial_sell(self, data, size_to_sell):
        """执行部分卖出"""
        if size_to_sell > 0:
            self.try_sell(data, size=size_to_sell)

    def notify_order(self, order):
        super().notify_order(order)

        if order.status not in [order.Completed, order.Partial]:
            return

        code = order.data._name

        if order.isbuy():
            self.entry_prices[code] = order.executed.price
            self.entry_bars[code] = len(self)
            self.half_sold[code] = False
            setattr(self, "_trade_buy_price_" + code, order.executed.price)
            setattr(self, "_trade_buy_size_" + code, order.executed.size)
            buy_reason = self.get_buy_reason(order.data)
            setattr(self, "_trade_buy_reason_" + code, buy_reason)
            buy_dt = order.data.datetime.date(0)
            setattr(self, "_trade_buy_date_" + code, buy_dt)
            return

        if not order.issell():
            return

        buy_price = self.entry_prices.get(code, 0.0)
        if not buy_price:
            return

        remaining_after_sell = self._ASHARE_position_size.get(code, 0)
        is_partial = remaining_after_sell > 1

        # 动态计算乖离率和ATR止盈
        ma_slow_ord = self._get(order.data, "ma_slow")
        ma_slow_ord_val = ma_slow_ord[0] if ma_slow_ord is not None else 0
        bias_value = (
            (order.data.close[0] - ma_slow_ord_val) / (ma_slow_ord_val + 1e-10) * 100
            if ma_slow_ord_val != 0
            else 0.0
        )
        atr_ind = self._get(order.data, "atr")
        atr_val = atr_ind[0] if atr_ind is not None else 0
        if atr_val <= 0:
            atr_val = order.data.close[0] * 0.02

        if abs(bias_value) > self.params.max_bias_for_holding:
            sell_reason = (
                f"乖离率过大({bias_value:.2f}%)减半仓"
                if is_partial
                else f"乖离率过大({bias_value:.2f}%)分批止盈"
            )
        elif is_partial:
            sell_reason = (
                f"盈利达{self.params.atr_partial_mult:.0f}×ATR({atr_val:.2f})减半仓"
            )
        else:
            sell_reason = (
                f"盈利达{self.params.atr_full_mult:.0f}×ATR({atr_val:.2f})完全止盈"
            )

        buy_date_attr = getattr(self, "_trade_buy_date_" + code, "")
        from datetime import datetime

        buy_date_str = (
            buy_date_attr.strftime("%Y-%m-%d")
            if isinstance(buy_date_attr, datetime)
            else str(buy_date_attr)
        )
        sell_date = self.datas[0].datetime.date(0)
        sell_date_str = (
            sell_date.strftime("%Y-%m-%d")
            if hasattr(sell_date, "strftime")
            else str(sell_date)
        )
        buy_reason = getattr(self, "_trade_buy_reason_" + code, "3日均线上穿16日均线")
        buy_size = getattr(self, "_trade_buy_size_" + code, order.size)

        pnl = order.executed.pnl
        cost = abs(buy_price * buy_size)
        pnl_pct = (pnl / cost * 100) if cost > 0 else 0.0

        if is_partial:
            if self._closed_trades and self._closed_trades[-1]["code"] == code:
                self._closed_trades.pop()

            self.log(
                f"[部分卖出] 盈亏: {pnl:+.2f} 元 ({pnl_pct:+.2f}%) "
                f"代码: {code} 卖出: {order.size}股 @{order.executed.price:.2f} "
                f"剩余: {remaining_after_sell}股 原因: {sell_reason}"
            )

            self._partial_sells.append(
                {
                    "code": code,
                    "buy_date": buy_date_str,
                    "sell_date": sell_date_str,
                    "buy_price": float(buy_price),
                    "sell_price": float(order.executed.price),
                    "sell_size": int(order.size),
                    "remaining_size": int(remaining_after_sell),
                    "pnl": float(pnl),
                    "pnl_pct": float(pnl_pct),
                    "buy_reason": buy_reason,
                    "sell_reason": sell_reason,
                }
            )
        else:
            # 完全平仓时的日志
            self.log(
                f"[平仓] 盈亏: {pnl:+.2f} 元 ({pnl_pct:+.2f}%) "
                f"代码: {code} 买入: {buy_date_str} @{buy_price:.2f} "
                f"卖出: {sell_date_str} @{order.executed.price:.2f} "
                f"原因: {sell_reason}"
            )

    def _get_prev_ma_fast(self, data):
        if len(data.close) < self.params.ma_fast_period + 2:
            return None
        total = 0
        for i in range(2, self.params.ma_fast_period + 2):
            total += data.close[-i]
        return total / self.params.ma_fast_period

    def _get_prev_ma_slow(self, data):
        if len(data.close) < self.params.ma_slow_period + 2:
            return None
        total = 0
        for i in range(2, self.params.ma_slow_period + 2):
            total += data.close[-i]
        return total / self.params.ma_slow_period

    def get_buy_reason(self, data) -> str:
        code = data._name
        score = self._candidate_scores.get(code, 0)
        return f"均线金叉选股(评分{score:.1f})"

    def get_sell_reason(self, data) -> str:
        # 由于这个方法在平仓完成后调用，此时持仓可能已清零
        # 我们需要基于当前价格和指标来判断卖出原因

        current_price = data.close[0]
        ma_fast = self._get(data, "ma_fast")
        ma_slow = self._get(data, "ma_slow")

        # 动态计算乖离率
        ma_slow_reason_val = ma_slow[0] if ma_slow is not None else 0
        bias_value = (
            (current_price - ma_slow_reason_val) / (ma_slow_reason_val + 1e-10) * 100
            if ma_slow_reason_val != 0
            else 0.0
        )

        # 检查死叉（主要卖出信号）
        if len(data.close) > 1:
            ma_fast_prev = self._get_prev_ma_fast(data)
            ma_slow_prev = self._get_prev_ma_slow(data)
            if ma_fast_prev is not None and ma_slow_prev is not None:
                death_cross = (ma_fast < ma_slow) and (ma_fast_prev >= ma_slow_prev)
                if death_cross:
                    return "3日均线下穿16日均线（死叉）清仓"

        # 检查止损
        if current_price < ma_slow:
            return f"跌破16日均线且{self.params.recovery_days}日内无法收回，或亏损达{self.params.stop_loss_percent}%止损"

        # 检查ATR动态止盈
        atr_ind = self._get(data, "atr")
        atr_val = atr_ind[0] if atr_ind is not None else 0
        if atr_val <= 0:
            atr_val = current_price * 0.02
        if abs(bias_value) > self.params.max_bias_for_holding:
            return f"乖离率过大({bias_value:.2f}%)分批止盈"
        elif self.half_sold.get(data._name, False):
            return f"盈利达{self.params.atr_full_mult:.0f}×ATR({atr_val:.2f})完全止盈"
        else:
            return f"盈利达{self.params.atr_partial_mult:.0f}×ATR({atr_val:.2f})减半仓"
