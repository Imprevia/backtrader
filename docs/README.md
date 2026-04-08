# A 股量化回测系统

## 概述
这是一个基于 Python + Backtrader 的 A 股量化回测系统，专为 A 股市场特性设计，支持 T+1 交易、涨跌停限制、A 股手续费体系等。

## 核心特性
- **A 股专用策略基类**: `AShareStrategy` 继承自 Backtrader 的 Strategy，内置 T+1 和涨跌停处理
- **完整的手续费体系**: 支持佣金（万三）、印花税（千分之一，卖出收取）、过户费（沪市）
- **数据管道**: 自动获取 A 股数据（5509 只股票，2020-2025 年完整 K 线）
- **随机数据划分**: 按股票随机划分为训练集、验证集、测试集（7:2:1）
- **并行回测**: 支持多进程加速回测
- **策略模块化**: 每个策略独立目录，包含策略代码和配置文件

## 目录结构
```
backtrader/
├── backtest_engine.py       # 核心：AShareStrategy基类 + A股手续费体系
├── run_backtest.py          # 入口：回测运行器 + 报告生成器 + 内置策略
├── backtest_runner.py       # 封装的回测运行器（支持全量/简单测试模式）
├── parallel_backtest_runner.py  # 并行回测运行器
├── log_manager.py           # 日志管理器（自动清理和保存回测数据）
├── data_pipeline/           # 数据获取与划分模块
│   ├── stock_list.py        #   Step 1: 获取A股股票列表
│   ├── download_kline.py    #   Step 2: 下载K线数据（并行+断点续传）
│   └── split.py             #   Step 3: 按股票随机划分 7:2:1
├── strategies/             # 策略实现（每个策略一个独立目录）
├── data_cache/             # K线数据缓存 (.pkl)
├── logs/                   # 回测日志输出目录
└── docs/                   # 文档目录
```

## 快速开始

### 1. 数据准备
```bash
# Step 1: 获取股票列表
python -m data_pipeline.stock_list

# Step 2: 下载K线数据
python -m data_pipeline.download_kline

# Step 3: 随机划分数据
python -m data_pipeline.split
```

### 2. 运行回测
```bash
# 完整回测（训练+验证）
python run_backtest.py

# 测试模式（少量股票）
python run_backtest.py --max-stocks 50

# 仅训练集
python run_backtest.py --train

# 仅验证集  
python run_backtest.py --val

# 运行特定策略
python run_backtest.py --strategy conservative

# 启用并行回测
python run_backtest.py --parallel
```

## 策略开发

所有策略必须继承 `AShareStrategy` 类，并实现以下方法：
```python
def should_buy(self, data) -> bool: ...
def should_sell(self, data) -> bool: ...
```

可选重写：
```python
def get_position_size(self, data) -> int: ...  # 默认全仓10%
def _reg(self, data, name, indicator): ...     # 指标注册
def _get(self, data, name): ...               # 指标访问
```

### A 股特性约束
- **T+1 交易**: 当日买入的股票，当日不可卖出
- **涨跌停**: 主板 ±10%、科创板 ±20%，涨跌停日不挂单
- **最小交易单位**: 100 股（A股规则）

## 依赖
- Python 3.8+
- Backtrader
- baostock
- pandas
- numpy

详细依赖请查看 `requirements.txt` 文件。