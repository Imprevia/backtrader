# 数据管道说明

## 概述

数据管道负责获取、处理和划分 A 股市场数据，为回测提供高质量的数据源。整个流程分为三个步骤，按顺序执行。

## 数据源

系统使用 **baostock** 作为数据源，提供免费的 A 股历史数据：
- **股票覆盖**: 5509 只 A 股（包含主板、创业板、科创板）
- **时间范围**: 2020-01-01 至 2025-12-31（完整6年数据）
- **数据字段**: 开盘价、最高价、最低价、收盘价、成交量、成交额
- **更新频率**: 日线数据

## 三步流水线

### Step 1: 获取股票列表 (`stock_list.py`)

**功能**: 从 baostock 获取完整的 A 股股票列表

**执行命令**:
```bash
python -m data_pipeline.stock_list
```

**输出文件**: `data_cache/stock_list.pkl`
- 包含所有 5509 只股票的代码列表
- 股票代码格式: 纯数字字符串（如 "600519"，不含 "sh." 前缀）

**注意事项**:
- 股票列表会自动过滤已退市股票
- 包含沪市（6开头）、深市（0开头）、创业板（3开头）、科创板（688开头）

### Step 2: 下载K线数据 (`download_kline.py`)

**功能**: 并行下载每只股票的完整K线数据

**执行命令**:
```bash
python -m data_pipeline.download_kline
```

**特性**:
- **并行下载**: 使用多线程加速数据获取
- **断点续传**: 支持 Ctrl+C 中断后继续下载
- **错误重试**: 自动重试失败的股票下载
- **进度显示**: 实时显示下载进度

**输出文件**: `data_cache/all_data_raw.pkl`
- 包含每只股票的完整K线数据（2020-2025）
- 数据格式: pandas DataFrame，索引为日期，列为 OHLCV 字段

**性能**:
- 完整下载约需 2-4 小时（取决于网络速度）
- 最终数据文件大小约 2-3 GB

### Step 3: 随机划分数据 (`split.py`)

**功能**: 按股票随机划分训练集、验证集、测试集

**执行命令**:
```bash
python -m data_pipeline.split
```

**划分策略**:
- **按股票划分**: 每只股票的完整数据只属于一个集合
- **比例**: 训练集 70% : 验证集 20% : 测试集 10%
- **随机性**: 使用固定随机种子确保结果可重现

**输出文件**:
- `data_cache/train_data.pkl` - 训练集数据
- `data_cache/val_data.pkl` - 验证集数据  
- `data_cache/test_data.pkl` - 测试集数据

**为什么按股票划分？**
- 避免同一股票在不同集合中出现导致数据泄露
- 更符合实际交易场景（新股票需要在未见过的数据上测试）

## 数据缓存机制

所有下载和处理的数据都缓存在 `data_cache/` 目录中：
- **stock_list.pkl**: 股票列表
- **all_data_raw.pkl**: 原始完整数据
- **train_data.pkl**: 训练集
- **val_data.pkl**: 验证集
- **test_data.pkl**: 测试集

**重要提醒**:
- 数据已经下载完成，禁止重新下载以免破坏数据一致性
- 如需重新生成数据，请先备份现有数据缓存文件

## 数据质量保证

### 数据完整性检查
- 自动跳过数据不完整的股票（缺失超过30天数据）
- 检查价格异常值（负价格、零价格等）
- 验证成交量和成交额的一致性

### 涨跌停数据处理
- 自动计算涨跌停价格
- 标记涨跌停日（用于回测中的交易限制）
- 处理 ST/*ST 股票的特殊涨跌停规则

### 复权处理
- 使用前复权价格，确保历史价格可比性
- 自动处理分红、配股等事件对价格的影响

## 使用数据进行回测

回测系统会自动加载相应的数据文件：

```python
# run_backtest.py 中的数据加载逻辑
if args.train:
    data = load_cached_data("train_data.pkl")
elif args.val:
    data = load_cached_data("val_data.pkl")  
elif args.test:
    data = load_cached_data("test_data.pkl")
else:
    # 默认加载训练+验证数据
    train_data = load_cached_data("train_data.pkl")
    val_data = load_cached_data("val_data.pkl")
    data = merge_data(train_data, val_data)
```

## 故障排除

### 常见问题

**1. 下载过程中断**
- 解决方案: 重新运行 `download_kline.py`，系统会自动续传

**2. 某些股票数据缺失**
- 原因: baostock API 限制或股票已退市
- 解决方案: 系统会自动跳过，不影响整体数据质量

**3. 内存不足**
- 原因: 完整数据集较大（2-3GB）
- 解决方案: 
  - 使用 `--max-stocks N` 参数限制股票数量进行测试
  - 增加系统虚拟内存
  - 分批处理数据

### 数据验证

运行以下命令验证数据完整性：
```bash
# 检查数据文件是否存在
ls -la data_cache/

# 验证数据划分比例
python -c "
import pickle
with open('data_cache/train_data.pkl', 'rb') as f:
    train = pickle.load(f)
with open('data_cache/val_data.pkl', 'rb') as f:
    val = pickle.load(f)  
with open('data_cache/test_data.pkl', 'rb') as f:
    test = pickle.load(f)
total_stocks = len(train) + len(val) + len(test)
print(f'总股票数: {total_stocks}')
print(f'训练集: {len(train)} ({len(train)/total_stocks:.1%})')
print(f'验证集: {len(val)} ({len(val)/total_stocks:.1%})')
print(f'测试集: {len(test)} ({len(test)/total_stocks:.1%})')
"
```

## 扩展和自定义

### 添加新的数据源
如果需要使用其他数据源（如 Tushare、AKShare），可以：

1. 创建新的数据下载模块（如 `download_kline_tushare.py`）
2. 修改数据格式转换逻辑，确保输出格式与现有系统兼容
3. 更新 `run_backtest.py` 中的数据加载逻辑

### 自定义数据划分
如果需要不同的划分策略（如时间序列划分），可以：

1. 修改 `split.py` 中的划分逻辑
2. 注意避免数据泄露问题
3. 更新回测系统的数据加载逻辑以支持新的划分方式