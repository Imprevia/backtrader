# data_pipeline — A股数据获取与划分模块

从 baostock 获取沪深A股K线数据（2020-2025），按股票随机划分训练/验证/测试集。

## 文件结构

```
data_pipeline/
├── __init__.py           # 模块初始化
├── stock_list.py         # Step 1: 获取股票列表
├── download_kline.py     # Step 2: 下载K线数据
├── split.py              # Step 3: 随机划分数据集
└── README.md             # 本文件
```

## 快速开始

### 方式一：模块运行

```bash
# Step 1: 获取股票列表（~5秒）
python -m data_pipeline.stock_list

# Step 2: 下载K线数据（~1小时，可中断续传）
python -m data_pipeline.download_kline

# Step 3: 随机划分 7:2:1
python -m data_pipeline.split
```

### 方式二：直接运行

```bash
python data_pipeline/stock_list.py
python data_pipeline/download_kline.py
python data_pipeline/split.py
```

## 各步骤说明

### Step 1: stock_list

| 项 | 说明 |
|---|---|
| 来源 | baostock.query_stock_industry() |
| 输出 | `data_cache/stock_list.pkl` |
| 字段 | updateDate, code, code_name, industry, industryClassification |
| 过滤 | 仅保留 `sh.` / `sz.` 开头（排除ETF、新三板等） |

### Step 2: download_kline

| 项 | 说明 |
|---|---|
| 数据源 | baostock.query_history_k_data_plus() |
| 时间范围 | 2020-01-02 ~ 2025-12-31 |
| 频率 | 日线 (d) |
| 复权 | 前复权 (adjustflag=2) |
| 并行 | 3 进程（baostock 对单IP限流） |
| 字段 | datetime, open, high, low, close, volume, amount, turn, pct_chg |
| 输出 | `data_cache/all_data_raw.pkl` |

**断点续传**：每下载 100 只自动保存，`Ctrl+C` 中断后再次运行自动跳过已下载的股票。

### Step 3: split

| 项 | 说明 |
|---|---|
| 划分方式 | 按股票完全随机划分（非时间切分） |
| 比例 | 训练集 70% / 验证集 20% / 测试集 10% |
| 随机种子 | 42（可复现） |
| 数据完整性 | 每只股票的完整K线（2020-2025）只属于一个集合 |

输出文件：
- `data_cache/train_data.pkl` — 训练集
- `data_cache/val_data.pkl` — 验证集
- `data_cache/test_data.pkl` — 测试集
- `data_cache/split_map.pkl` — 划分映射（含股票代码列表，可查每只股票属于哪个集）

## 输出数据格式

每个 `.pkl` 文件保存为 `{code: DataFrame}` 字典：

```python
# 读取
import pandas as pd
data = pd.read_pickle("data_cache/train_data.pkl")
df = data["600519"]  # 贵州茅台的K线

# DataFrame 列
# datetime, code, open, high, low, close, volume, amount, turn, pct_chg
```

## 依赖

```
baostock>=0.8.8
pandas>=2.0
numpy>=1.24
tqdm>=4.60
```
