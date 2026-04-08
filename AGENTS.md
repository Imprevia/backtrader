# A 股量化回测系统 - 知识库

**Generated:** 2026-04-06
**Stack:** Python 3 + Backtrader + baostock + pandas
**范围:** A 股回测引擎、数据获取、策略运行

## STRUCTURE

```
backtrader/
├── backtest_engine.py       # 核心：AShareStrategy基类 + A股手续费体系 + 日志模块
├── backtest_runner.py       # 封装的回测运行器（支持全量/简单测试模式）
├── parallel_backtest_runner.py  # 并行回测运行器（多进程加速）
├── run_backtest.py          # 入口：回测运行器 + 报告生成器
├── log_manager.py           # 日志管理器（自动清理和保存回测数据）
├── web_visualizer/          # 网页可视化模块（交易K线图生成）
│   └── visualizer.py        #   交易图表生成器
├── data_pipeline/           # 数据获取与划分模块
│   ├── stock_list.py       #   Step 1: 获取A股股票列表
│   ├── download_kline.py   #   Step 2: 下载K线数据（并行+断点续传）
│   ├── split.py            #   Step 3: 按股票随机划分 7:2:1（预划分）
│   └── dynamic_split.py    #   动态划分模块（每次回测重新划分，防过拟合）
├── strategies/              # 策略实现（每个策略一个独立目录）
│   └── ma_golden_cross_3_16_complete/  # 均线金叉3穿25策略（优化版）
│       ├── strategy.py      #   策略实现
│       └── config.yaml      #   策略参数配置
├── logs/                   # 回测日志输出目录
│   ├── *.log               #   日志文件
│   └── *.json              #   回测结果JSON
└── data_cache/             # K线数据缓存 (.pkl)
```

## WHERE TO LOOK

| 任务 | 文件 | 备注 |
|------|------|------|
| 修改交易规则（T+1/涨跌停） | `backtest_engine.py` | `AShareStrategy` 类 |
| 添加新策略 | `strategies/` 目录下创建新目录 | 每个策略对应一个独立目录，包含 strategy.py 和 config.yaml |
| 修改手续费体系 | `backtest_engine.py` | `CommInfoAShare` / `create_ashare_commission()` |
| 数据获取 | `data_pipeline/` | 三步流水线：股票列表 → K线下载 → 随机划分 |
| 训练/验证对比 | `run_backtest.py` 或 `backtest_runner.py` | 使用封装的回测运行器 |
| YAML策略配置 | 策略目录下的 `config.yaml` | 每个策略目录包含独立的配置文件 |
| 日志管理 | `log_manager.py` | 自动清理旧日志并保存回测结果 |
| 交易可视化 | `web_visualizer/visualizer.py` | 生成交易K线图HTML报告 |

## 模块依赖

```
run_backtest.py
  ├── backtest_engine.py  (AShareStrategy, AShareData, create_ashare_commission)
  ├── backtest_runner.py  (封装的回测运行器)
  ├── log_manager.py      (日志管理器)
  └── data_cache/*.pkl   (train_data.pkl, val_data.pkl)

strategies/ (目录式策略)
  └── backtest_engine.py  (AShareStrategy)

web_visualizer/
  └── visualizer.py       (交易K线图生成器)

data_pipeline/
  ├── baostock (外部API) → stock_list.pkl → all_data_raw.pkl
  └── split.py → train_data.pkl, val_data.pkl, test_data.pkl
```

## 核心概念

### A 股特性（必须遵守）
- **T+1 交易**：当日买入的股票，当日不可卖出
- **涨跌停**：主板 ±10%、科创板 ±20%，涨跌停日不挂单
- **手续费**：佣金（万三）+ 印花税（千分之一，卖出）+ 过户费（沪市万一）
- **最小交易单位**：100 股（A股规则）

### 策略基类约定
所有策略必须继承 `AShareStrategy`，实现：
```python
def should_buy(self, data) -> bool: ...
def should_sell(self, data) -> bool: ...
```

可选重写：
```python
def get_position_size(self, data) -> int: ...  # 默认全仓10%
def _reg(self, data, name, indicator): ...       # 指标注册
def _get(self, data, name): ...                 # 指标访问
```

### 数据流
```
data_pipeline/ (baostock)
  → stock_list.pkl (5509只A股)
  → all_data_raw.pkl (每只完整K线 2020-2025)
  → 随机划分 7:2:1
  → train_data.pkl / val_data.pkl / test_data.pkl
                                     ↓
        run_backtest.py → (预划分模式，默认)
                                     ↓
        run_backtest.py --dynamic-split → (动态划分，每次重新划分)
```

## ANTI-PATTERNS (THIS PROJECT)

- **不要**在 `AShareStrategy` 外部直接调用 `buy()/sell()`，用 `try_buy()/try_sell()`
- **不要**在策略层手动处理 T+1，用 `can_sell_today()` 检查
- **不要**删除 `__init__` 中的 `super().__init__()`（Backtrader 元类注册依赖）
- **不要**在 `AShareStrategy` 子类中混用 `self.data`（多股场景应用 `self.datas` 遍历）
- **不要**对单只股票创建指标（应用 `_reg()` 注册，支持多股并行）

## UNIQUE STYLES

- 多股票并行：每个 data 在 `__init__` 中通过 `_reg()` 注册独立指标
- 数据管道：`data_pipeline/` 三步模块化（获取列表 → 并行下载 → 随机划分）
- 随机划分：按股票随机 7:2:1（非时间切分），每只股票完整数据只属一个集合
- 涨跌停检测：`prev_close * (1 ± limit_rate)`，考虑浮点误差（×0.9999/×1.0001）
- 指标访问：`_get(data, "sma_fast")` 获取指定股票的指标，避免多股混淆

## CONVENTIONS

- 股票代码：纯数字字符串（`"600519"`，不含 `sh.` 前缀）
- 日期：`pd.Timestamp` + `YYYY-MM-DD` 字符串混用
- 涨跌停率：`get_limit_rate(code)` 根据代码前缀判断（`688`→±20%，其他→±10%）
- 参数命名：`snake_case`，类参数用 `params = ((k,v), ...)` tuple 格式（Backtrader 规范）
- 日志：`self.log("[操作] 详情")` 打印带日期的日志

## COMMANDS

```bash
# === 数据获取 ===
python -m data_pipeline.stock_list      # Step 1: 获取股票列表
python -m data_pipeline.download_kline  # Step 2: 下载K线数据（可Ctrl+C断点续传）
python -m data_pipeline.split           # Step 3: 随机划分 7:2:1

# === 回测运行 ===
python run_backtest.py                   # 完整回测（训练+验证）
python run_backtest.py --max-stocks 50   # 测试模式（少量股票）
python run_backtest.py --train           # 仅训练集
python run_backtest.py --val             # 仅验证集
python run_backtest.py --strategy conservative  # 运行保守型策略
python run_backtest.py --strategy diversified_portfolio  # 运行多样化投资组合策略
python run_backtest.py --strategy optimized_portfolio  # 运行优化版多策略组合策略

# === 并行回测（多进程加速）===
python run_backtest.py --parallel        # 启用并行回测（自动CPU核心数-1）
python run_backtest.py --parallel --workers 4  # 指定4个工作进程
python run_backtest.py --parallel --max-stocks 1000  # 并行测试模式

# === 动态划分（防过拟合）===
python run_backtest.py --dynamic-split   # 每次回测动态划分数据
python run_backtest.py --dynamic-split --seed 42  # 使用固定种子（可复现）
python run_backtest.py --dynamic-split --max-stocks 100  # 动态划分+少量股票测试
```

## NOTES

- 策略中的指标必须通过 `_reg(data, "name", indicator)` 注册，`_get(data, "name")` 访问
- `BacktestReport` 预计算所有 analyzer 结果，子类可直接访问 `self.avg_win` 等属性
- 数据划分：按股票随机 7:2:1，每只股票包含 2020-2025 完整K线
- **交易可视化**：回测完成后自动在 `logs/` 目录生成HTML文件，显示每笔交易的K线图（买入前20天+卖出后20天），包含3日和16日均线，并标记买卖点及买卖理由

## DATA STATUS

**重要：数据已经下载完成！**

- 所有A股数据（5509只股票，2020-2025年完整K线）已成功下载并缓存到 `data_cache/` 目录
- 数据文件包括：`stock_list.pkl`, `train_data.pkl`, `val_data.pkl`, `test_data.pkl`
- **禁止后续操作修改或重新下载数据**，以免破坏现有数据一致性
- 如需重新生成数据，请先备份现有数据缓存文件

**动态划分功能已实现（防过拟合）：**
- 使用 `--dynamic-split` 参数时，每次回测会重新划分训练集/验证集
- 默认使用时间戳作为种子（每次划分不同），或用 `--seed` 指定固定种子（可复现）
