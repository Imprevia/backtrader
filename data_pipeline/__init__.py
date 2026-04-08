# -*- coding: utf-8 -*-
"""
data_pipeline - A股数据获取与划分模块

从 baostock 获取沪深A股K线数据，并按股票随机划分训练/验证/测试集。

用法:
    python -m data_pipeline.stock_list      # Step 1: 获取股票列表
    python -m data_pipeline.download_kline  # Step 2: 下载K线数据
    python -m data_pipeline.split           # Step 3: 随机划分数据集
"""
