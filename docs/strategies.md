# 策略开发指南

## 创建新策略

### 1. 目录结构
每个策略必须在 `strategies/` 目录下创建独立的子目录：

```
strategies/
└── your_strategy_name/
    ├── strategy.py          # 策略实现代码
    └── config.yaml          # 策略配置文件
```

### 2. 策略类实现

所有策略必须继承 `AShareStrategy` 类：

```python
from backtest_engine import AShareStrategy

class YourStrategyName(AShareStrategy):
    """你的策略描述"""
    
    def __init__(self):
        super().__init__()
        # 在这里为每个股票注册指标
        for data in self.datas:
            self._reg(data, "sma_fast", bt.indicators.SMA(data.close, period=10))
            self._reg(data, "sma_slow", bt.indicators.SMA(data.close, period=30))
    
    def should_buy(self, data) -> bool:
        """买入信号判断"""
        # 获取当前股票的指标值
        sma_fast = self._get(data, "sma_fast")
        sma_slow = self._get(data, "sma_slow")
        
        # 实现你的买入逻辑
        if sma_fast > sma_slow:
            return True
        return False
    
    def should_sell(self, data) -> bool:
        """卖出信号判断"""
        sma_fast = self._get(data, "sma_fast")
        sma_slow = self._get(data, "sma_slow")
        
        # 实现你的卖出逻辑
        if sma_fast < sma_slow:
            return True
        return False
    
    def get_position_size(self, data) -> int:
        """可选：自定义仓位大小"""
        # 默认返回全仓的10%
        return super().get_position_size(data)
```

### 3. 配置文件 (config.yaml)

```yaml
# 策略参数配置
strategy_params:
  sma_fast_period: 10
  sma_slow_period: 30

# 回测参数
backtest_params:
  start_date: "2020-01-01"
  end_date: "2025-12-31"
  initial_cash: 1000000
  commission_rate: 0.0003  # 万三佣金
```

## A 股特性处理

### T+1 交易限制
系统自动处理 T+1 限制，你只需要使用提供的方法：

- **检查是否可以今日卖出**: `self.can_sell_today(data)`
- **安全买入**: `self.try_buy(data, size)`
- **安全卖出**: `self.try_sell(data, size)`

**不要**直接调用 `self.buy()` 或 `self.sell()`，这会绕过 A 股限制检查。

### 涨跌停处理
系统自动检测涨跌停，并在涨跌停日不产生交易信号。你可以通过以下方式获取涨跌停价格：

```python
limit_up = data.close[0] * (1 + self.get_limit_rate(data._name))
limit_down = data.close[0] * (1 - self.get_limit_rate(data._name))
```

### 手续费体系
系统内置完整的 A 股手续费计算：
- **买入**: 佣金（万三）+ 过户费（沪市万一）
- **卖出**: 佣金（万三）+ 印花税（千分之一）+ 过户费（沪市万一）

## 多股票支持

策略会自动在所有加载的股票上并行执行。在 `__init__` 中，你需要为每个 `data` 对象注册独立的指标：

```python
def __init__(self):
    super().__init__()
    for data in self.datas:
        # 为每只股票注册独立的指标
        self._reg(data, "rsi", bt.indicators.RSI(data.close, period=14))
        self._reg(data, "macd", bt.indicators.MACD(data.close))
```

在交易信号判断中，通过 `_get()` 方法获取指定股票的指标值：

```python
def should_buy(self, data):
    rsi = self._get(data, "rsi")
    return rsi < 30
```

## 调试和日志

使用 `self.log()` 方法记录日志：

```python
self.log(f"[买入信号] {data._name} RSI={rsi:.2f}")
```

日志会自动包含日期时间戳，并输出到 `logs/` 目录。

## 最佳实践

1. **指标注册**: 在 `__init__` 中完成所有指标注册，不要在 `next()` 中动态创建
2. **数据访问**: 使用 `_get(data, "indicator_name")` 访问指标，避免直接访问 `self.data`
3. **交易执行**: 始终使用 `try_buy()` 和 `try_sell()`，不要直接调用 Backtrader 的原生方法
4. **参数配置**: 将策略参数放在 `config.yaml` 中，便于调整和回测
5. **错误处理**: 考虑数据缺失、停牌等边界情况

## 示例策略

参考现有的策略实现：
- `strategies/conservative/` - 保守型ATR止损策略
- `strategies/diversified_portfolio/` - 多样化投资组合策略  
- `strategies/optimized_portfolio/` - 优化版多策略组合策略

这些示例展示了如何正确实现 A 股策略，包括风险管理、仓位控制等高级功能。