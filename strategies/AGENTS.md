# Strategies - 策略模块

**继承链:** `AShareStrategy` (backtest_engine.py) → 具体策略

## OVERVIEW

所有策略均继承 `AShareStrategy`，实现 `should_buy()` / `should_sell()` 接口。

## WHERE TO LOOK

| 策略 | 文件 | 原理 | 回测结果 |
|------|------|------|---------|
| SMAGoldenCrossStrategy | `sma_golden_cross.py` | 5/16日均线 + 20日趋势过滤 + RSI>40 + 成交量确认 + 止损-5%/止盈+10%/超时20日 | 验证集胜率47.4% |
| ConservativeStrategy | `conservative_strategy.py` | 3/25日均线 + RSI>48 + ATR追踪止损 + 止盈2×ATR + Kelly仓位(0.15) + 成交量过滤 | 验证集 SQN 3.89、胜率38.7%、训练集收益+9.1% |
| AggressiveStrategy | `aggressive_strategy.py` | 3/16日 + ATR波动率过滤 + 移动止损 + 2:1盈亏比 | 验证集胜率37.1%、SQN 1.42 |
| MomentumStrategy | `momentum.py` | 20日动量 > 0 买入，< -5% 止损，20天强制退出 |
| BollingerBandsStrategy | `bollinger_bands.py` | 价格触及布林下轨买入，上轨或超期卖出 |
| SMACrossStrategy | `run_backtest.py` | 5/20日均线金叉死叉 + 20日趋势过滤（内置） |
| ConfiguredStrategy | `run_backtest.py` | YAML配置动态生成的策略类 |
| MAGoldenCross3_16_Complete | `ma_golden_cross_3_16_complete/strategy.py` | 3/16日均线金叉死叉 + 完整止损体系（跌破16日均线3日无法收回或亏损7%）+ 分批止盈（乖离率>8%减半仓/盈利10%-15%减半仓/盈利20%全清） | 待回测 |

## STRUCTURE

```
strategies/
├── sma_golden_cross.py      # 优化版SMA金叉：止损+止盈+超时+成交量确认
├── sma_golden_cross_v1.py   # 原版备份（3穿16 + RSI>50 + 无止损）
├── conservative_strategy.py  # 保守型优化版：ATR追踪止损+止盈2×ATR+Kelly仓位
├── aggressive_strategy.py    # 激进型：ATR波动率过滤+移动止损+2:1盈亏比
├── momentum.py             # 动量策略（20日 + 止损）
├── bollinger_bands.py      # 布林带均值回归策略
├── strategy_template.yaml  # YAML策略配置模板
│
├── ma_golden_cross_3_16_complete/  # 完整版右侧均线金叉3穿16策略
│   ├── strategy.py        #   策略实现：分批止盈+完整止损体系
│   └── config.yaml        #   策略参数配置
└── ma_golden_cross_3_16_fixed/     # 修复版均线金叉策略
    └── strategy.py        #   策略实现
```

## ANTI-PATTERNS (THIS MODULE)

- **不要**用 `self.data` 访问指标：多股票场景下 `self.data` 仅指向第一个数据源
- **不要**直接创建全局指标：`self.sma = bt.ind.SMA(...)` 会导致多股共用同一指标
- **正确做法**：在 `__init__` 中对每只 `data` 调用 `self._reg(data, "name", indicator)`
- **不要**省略 `super().__init__()`
- **不要**省略 `super().notify_order()`

## CONVENTIONS

- 指标注册：`self._reg(data, "name", bt.ind.SMA(data, period=N))`
- 指标访问：`self._get(data, "name")[0]`
- 入场记录：`self._entry_bar[code] = len(data)` 在 `notify_order` 的买入完成时设置
- 参数格式：`params = (("fast_period", 3), ...)`（Backtrader tuple 格式）
- 策略内持仓检查：`self.getposition(data).size > 0`

## YAML 策略配置

`strategy_template.yaml` 支持动态策略类生成：
```yaml
strategy:
  name: "模板策略"
params:
  max_position_ratio: 0.1
signals:
  buy:
    - type: cross_over
      indicator1: sma_fast
      indicator2: sma_slow
```

支持信号类型：`cross_over`, `cross_below`, `gt`, `lt`, `value_gt`
