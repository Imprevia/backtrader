# 系统架构

## 核心组件

### 1. AShareStrategy (backtest_engine.py)
策略基类，继承自 Backtrader 的 Strategy 类，专门针对 A 股市场特性进行封装。

**主要功能：**
- T+1 交易限制处理
- 涨跌停价格计算和过滤
- 多股票并行支持
- 指标注册和访问机制

**关键方法：**
- `try_buy(data, size=None)`: 尝试买入，自动处理涨跌停和T+1限制
- `try_sell(data, size=None)`: 尝试卖出，自动处理涨跌停限制  
- `can_sell_today(data)`: 检查今日是否可以卖出（T+1检查）
- `_reg(data, name, indicator)`: 注册指标
- `_get(data, name)`: 获取指定股票的指标值

### 2. Data Pipeline (data_pipeline/)
三步数据获取流水线：

1. **stock_list.py**: 从 baostock 获取完整的 A 股股票列表（5509 只）
2. **download_kline.py**: 并行下载每只股票的完整 K 线数据（2020-2025）
3. **split.py**: 按股票随机划分 7:2:1（训练:验证:测试）

**数据缓存**: 所有数据缓存在 `data_cache/` 目录下的 .pkl 文件中

### 3. 回测运行器
- **run_backtest.py**: 主入口，负责加载数据、创建策略实例、运行回测、生成报告
- **backtest_runner.py**: 封装的回测运行器，支持不同模式（全量/测试）
- **parallel_backtest_runner.py**: 多进程并行回测，显著提升大数据集回测速度

### 4. 日志管理 (log_manager.py)
- 自动管理回测日志
- 支持日志轮转和清理
- 输出详细的交易记录和策略执行日志

## 数据流

```
baostock API
    ↓
stock_list.pkl (5509只A股)
    ↓  
all_data_raw.pkl (每只完整K线 2020-2025)
    ↓
随机划分 7:2:1
    ↓
train_data.pkl / val_data.pkl / test_data.pkl
    ↓
run_backtest.py → cerebro.adddata()
    ↓
策略执行 → 交易记录 → 回测报告
```

## 策略执行流程

1. 策略初始化 (`__init__`)
   - 调用父类 `__init__()`
   - 为每个股票数据注册技术指标（使用 `_reg()`）
   
2. 每日执行 (`next()`)
   - 遍历所有股票数据
   - 调用 `should_buy()` 和 `should_sell()` 判断交易信号
   - 使用 `try_buy()` 和 `try_sell()` 执行交易（自动处理A股限制）

3. 报告生成
   - 计算收益率、胜率、最大回撤等指标
   - 生成详细的交易日志

## 并行处理

系统支持两种并行模式：

1. **多股票并行**: 在单个回测中同时处理多只股票
2. **多进程并行**: 使用 `--parallel` 参数启动多个进程，每个进程处理不同的股票子集

## 错误处理和边界情况

- **涨跌停处理**: 涨跌停日不产生新的买入/卖出信号
- **T+1 限制**: 当日买入的股票在次日才能卖出
- **数据缺失**: 自动跳过数据不完整的股票
- **手续费计算**: 精确计算佣金、印花税、过户费