"""
网页可视化模块 - 生成交易K线图
"""

import os
import json
from pathlib import Path
from typing import Dict, List, Optional, Any
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def generate_trade_charts(
    strategy,
    output_file: str,
    strategy_name: str = "Strategy",
    data_cache_dir: str = "data_cache",
) -> bool:
    """生成交易K线图网页"""
    closed_trades = getattr(strategy, "_closed_trades", [])
    partial_sells = getattr(strategy, "_partial_sells", [])
    if not closed_trades and not partial_sells:
        print("没有找到已完成的交易记录，跳过图表生成")
        return False

    print(
        f"发现 {len(closed_trades)} 笔完全平仓交易, {len(partial_sells)} 笔分批止盈记录..."
    )

    try:
        train_data = pd.read_pickle(os.path.join(data_cache_dir, "train_data.pkl"))
        val_data = pd.read_pickle(os.path.join(data_cache_dir, "val_data.pkl"))
        test_data = pd.read_pickle(os.path.join(data_cache_dir, "test_data.pkl"))

        all_data = {}
        all_data.update(train_data)
        all_data.update(val_data)
        all_data.update(test_data)
    except Exception as e:
        print(f"加载数据缓存失败: {e}")
        return False

    chart_htmls = []
    for i, trade in enumerate(closed_trades):
        code = trade["code"]
        buy_date = trade["buy_date"]
        sell_date = trade["sell_date"]

        if code not in all_data:
            print(f"警告: 股票 {code} 的数据未找到，跳过")
            continue

        stock_df = all_data[code].copy()
        if stock_df.empty:
            print(f"警告: 股票 {code} 的数据为空，跳过")
            continue

        if "datetime" not in stock_df.columns:
            print(f"警告: 股票 {code} 缺少datetime列，跳过")
            continue

        stock_df["datetime"] = pd.to_datetime(stock_df["datetime"])
        stock_df = stock_df.set_index("datetime").sort_index()

        try:
            buy_dt = pd.to_datetime(buy_date)
            sell_dt = pd.to_datetime(sell_date)

            # 找到买入和卖出日期对应的索引（兼容pandas 3.0）
            buy_idx = None
            sell_idx = None

            # 查找最接近的日期
            for i, date in enumerate(stock_df.index):
                if date >= buy_dt:
                    buy_idx = i
                    break
            if buy_idx is None:
                buy_idx = len(stock_df) - 1

            for i, date in enumerate(stock_df.index):
                if date >= sell_dt:
                    sell_idx = i
                    break
            if sell_idx is None:
                sell_idx = len(stock_df) - 1

            start_idx = max(0, buy_idx - 20)
            end_idx = min(len(stock_df) - 1, sell_idx + 20)

            chart_data = stock_df.iloc[start_idx : end_idx + 1].copy()

            if len(chart_data) < 5:
                print(f"警告: 股票 {code} 的图表数据太少，跳过")
                continue

            fig = create_kline_chart(chart_data, trade, buy_dt, sell_dt)
            chart_html = fig.to_html(include_plotlyjs=False, full_html=False)
            chart_htmls.append(
                {
                    "code": code,
                    "buy_date": buy_date,
                    "sell_date": sell_date,
                    "pnl_pct": trade["pnl_pct"],
                    "buy_reason": trade.get("buy_reason", "无"),
                    "sell_reason": trade.get("sell_reason", "无"),
                    "html": chart_html,
                }
            )

        except KeyError as e:
            print(f"警告: 股票 {code} 的日期 {buy_date} 或 {sell_date} 未找到: {e}")
            continue
        except Exception as e:
            print(f"警告: 处理股票 {code} 时出错: {e}")
            continue

    if not chart_htmls:
        print("没有有效的交易图表可生成")
        return False

    html_content = generate_html_page(chart_htmls, strategy_name, partial_sells)
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"交易K线图已生成: {output_file}")
    return True


def create_kline_chart(
    data: pd.DataFrame,
    trade: Dict[str, Any],
    buy_dt: pd.Timestamp,
    sell_dt: pd.Timestamp,
) -> go.Figure:
    """创建单个股票的K线图"""
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        row_heights=[0.7, 0.3],
        subplot_titles=("K线图", "成交量"),
    )

    fig.add_trace(
        go.Candlestick(
            x=data.index.tolist(),
            open=data["open"].tolist(),
            high=data["high"].tolist(),
            low=data["low"].tolist(),
            close=data["close"].tolist(),
            name="K线",
        ),
        row=1,
        col=1,
    )

    # 计算并添加3日均线
    ma3 = data["close"].rolling(window=3, min_periods=1).mean()
    fig.add_trace(
        go.Scatter(
            x=data.index.tolist(),
            y=ma3.tolist(),
            mode="lines",
            line=dict(color="blue", width=1),
            name="3日均线",
        ),
        row=1,
        col=1,
    )

    # 计算并添加16日均线
    ma16 = data["close"].rolling(window=16, min_periods=1).mean()
    fig.add_trace(
        go.Scatter(
            x=data.index.tolist(),
            y=ma16.tolist(),
            mode="lines",
            line=dict(color="orange", width=1),
            name="16日均线",
        ),
        row=1,
        col=1,
    )

    buy_price = trade["buy_price"]
    fig.add_trace(
        go.Scatter(
            x=[buy_dt],
            y=[buy_price],
            mode="markers",
            marker=dict(color="green", size=12, symbol="triangle-up"),
            name=f"买入: {buy_price:.2f}",
            showlegend=True,
        ),
        row=1,
        col=1,
    )

    sell_price = trade["sell_price"]
    fig.add_trace(
        go.Scatter(
            x=[sell_dt],
            y=[sell_price],
            mode="markers",
            marker=dict(color="red", size=12, symbol="triangle-down"),
            name=f"卖出: {sell_price:.2f}",
            showlegend=True,
        ),
        row=1,
        col=1,
    )

    fig.add_trace(
        go.Bar(
            x=data.index.tolist(),
            y=data["volume"].tolist(),
            name="成交量",
            marker_color="lightblue",
        ),
        row=2,
        col=1,
    )

    fig.update_layout(
        title=f"股票 {trade['code']} - 盈亏: {trade['pnl_pct']:+.2f}%",
        height=600,
        showlegend=True,
        xaxis_rangeslider_visible=False,
    )

    # 设置X轴日期格式为 %m-%d
    fig.update_xaxes(title_text="日期", row=1, col=1, tickformat="%m-%d")
    fig.update_xaxes(title_text="日期", row=2, col=1, tickformat="%m-%d")
    fig.update_yaxes(title_text="价格", row=1, col=1)
    fig.update_yaxes(title_text="成交量", row=2, col=1)

    return fig


def generate_html_page(
    chart_data: List[Dict], strategy_name: str, partial_sells: List[Dict]
) -> str:
    """生成完整的HTML页面"""
    plotly_js = """
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    """

    css_style = """
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 20px;
            background-color: #f5f5f5;
        }
        .header {
            text-align: center;
            margin-bottom: 30px;
            color: #333;
        }
        .chart-container {
            background-color: white;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 30px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .chart-info {
            margin-bottom: 15px;
            font-size: 14px;
            color: #666;
        }
        .chart-title {
            font-size: 18px;
            font-weight: bold;
            margin-bottom: 10px;
            color: #333;
        }
        hr {
            margin: 40px 0;
            border: none;
            border-top: 1px solid #eee;
        }
    </style>
    """

    html_parts = [
        "<!DOCTYPE html>",
        "<html lang='zh-CN'>",
        "<head>",
        "    <meta charset='UTF-8'>",
        "    <meta name='viewport' content='width=device-width, initial-scale=1.0'>",
        f"    <title>{strategy_name} - 交易K线图</title>",
        plotly_js,
        css_style,
        "</head>",
        "<body>",
        f"    <div class='header'>",
        f"        <h1>{strategy_name}</h1>",
        f"        <p>共 {len(chart_data)} 笔完全平仓交易的K线图分析</p>",
        f"    </div>",
    ]

    if partial_sells:
        html_parts.extend(
            [
                "    <div class='partial-sells-section' style='background: #fff3cd; border: 1px solid #ffc107; border-radius: 8px; padding: 15px; margin-bottom: 30px;'>",
                "        <h2 style='color: #856404; margin-top: 0;'>📤 分批止盈记录</h2>",
                "        <p style='color: #856404; font-size: 13px;'>以下为分批减仓事件（减半仓但未完全平仓）：</p>",
                "        <table style='width: 100%; border-collapse: collapse; font-size: 13px;'>",
                "            <thead>",
                "                <tr style='background: #ffeeba;'>",
                "                    <th style='padding: 8px; border: 1px solid #ffc107; text-align: left;'>股票代码</th>",
                "                    <th style='padding: 8px; border: 1px solid #ffc107; text-align: left;'>买入日期</th>",
                "                    <th style='padding: 8px; border: 1px solid #ffc107; text-align: left;'>分批卖出日期</th>",
                "                    <th style='padding: 8px; border: 1px solid #ffc107; text-align: right;'>买入价格</th>",
                "                    <th style='padding: 8px; border: 1px solid #ffc107; text-align: right;'>分批卖出价格</th>",
                "                    <th style='padding: 8px; border: 1px solid #ffc107; text-align: right;'>卖出股数</th>",
                "                    <th style='padding: 8px; border: 1px solid #ffc107; text-align: right;'>剩余股数</th>",
                "                    <th style='padding: 8px; border: 1px solid #ffc107; text-align: right;'>本次盈亏</th>",
                "                    <th style='padding: 8px; border: 1px solid #ffc107; text-align: right;'>盈亏%</th>",
                "                    <th style='padding: 8px; border: 1px solid #ffc107; text-align: left;'>卖出理由</th>",
                "                </tr>",
                "            </thead>",
                "            <tbody>",
            ]
        )
        for ps in partial_sells:
            sign = "+" if ps["pnl"] >= 0 else ""
            sign_pct = "+" if ps["pnl_pct"] >= 0 else ""
            pnl_color = "#28a745" if ps["pnl"] >= 0 else "#dc3545"
            html_parts.append(
                f"                <tr style='background: #fff;'>"
                f"<td style='padding: 6px 8px; border: 1px solid #ffc107;'>{ps['code']}</td>"
                f"<td style='padding: 6px 8px; border: 1px solid #ffc107;'>{ps['buy_date']}</td>"
                f"<td style='padding: 6px 8px; border: 1px solid #ffc107;'>{ps['sell_date']}</td>"
                f"<td style='padding: 6px 8px; border: 1px solid #ffc107; text-align: right;'>{ps['buy_price']:.2f}</td>"
                f"<td style='padding: 6px 8px; border: 1px solid #ffc107; text-align: right;'>{ps['sell_price']:.2f}</td>"
                f"<td style='padding: 6px 8px; border: 1px solid #ffc107; text-align: right;'>{ps['sell_size']}</td>"
                f"<td style='padding: 6px 8px; border: 1px solid #ffc107; text-align: right;'>{ps['remaining_size']}</td>"
                f"<td style='padding: 6px 8px; border: 1px solid #ffc107; text-align: right; color: {pnl_color};'>{sign}{ps['pnl']:.2f}</td>"
                f"<td style='padding: 6px 8px; border: 1px solid #ffc107; text-align: right; color: {pnl_color};'>{sign_pct}{ps['pnl_pct']:.2f}%</td>"
                f"<td style='padding: 6px 8px; border: 1px solid #ffc107;'>{ps['sell_reason']}</td>"
                f"</tr>"
            )
        html_parts.extend(
            [
                "            </tbody>",
                "        </table>",
                "    </div>",
            ]
        )

    for i, chart_info in enumerate(chart_data):
        html_parts.extend(
            [
                f"    <div class='chart-container'>",
                f"        <div class='chart-title'>交易 #{i + 1}: 股票 {chart_info['code']}</div>",
                f"        <div class='chart-info'>",
                f"            买入日期: {chart_info['buy_date']} | 卖出日期: {chart_info['sell_date']} | 盈亏: {chart_info['pnl_pct']:+.2f}%",
                f"        </div>",
                f"        <div class='chart-reasons' style='margin-bottom: 10px; font-size: 12px; color: #444;'>",
                f"            <strong>买入理由:</strong> {chart_info.get('buy_reason', '无')}",
                f"            <br>",
                f"            <strong>卖出理由:</strong> {chart_info.get('sell_reason', '无')}",
                f"        </div>",
                f"        {chart_info['html']}",
                f"    </div>",
                f"    <hr>",
            ]
        )

    html_parts.extend(["</body>", "</html>"])

    return "\n".join(html_parts)
