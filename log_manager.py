"""
日志管理模块 - 处理回测日志的保存和清理
"""

import os
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional
from logging.handlers import RotatingFileHandler

# 尝试导入网页可视化模块
try:
    from web_visualizer import generate_trade_charts

    WEB_VISUALIZER_AVAILABLE = True
except ImportError:
    WEB_VISUALIZER_AVAILABLE = False


class JSONFormatter(logging.Formatter):
    """JSON格式日志格式化器"""

    def __init__(self, include_extra: bool = True):
        super().__init__()
        self.include_extra = include_extra

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # 添加额外字段
        if self.include_extra:
            if hasattr(record, "strategy"):
                log_data["strategy"] = record.strategy
            if hasattr(record, "code"):
                log_data["code"] = record.code
            if hasattr(record, "action"):
                log_data["action"] = record.action
            if hasattr(record, "pnl"):
                log_data["pnl"] = record.pnl
            if hasattr(record, "pnl_pct"):
                log_data["pnl_pct"] = record.pnl_pct
        return json.dumps(log_data, ensure_ascii=False)


class LogManager:
    """日志管理器，负责日志文件的创建、清理和保存"""

    def __init__(
        self,
        logs_dir: str = "logs",
        max_bytes: int = 10 * 1024 * 1024,
        backup_count: int = 5,
    ):
        """
        初始化日志管理器

        Args:
            logs_dir: 日志目录
            max_bytes: 单个日志文件最大字节数（默认10MB）
            backup_count: 保留的备份文件数量（默认5个）
        """
        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(exist_ok=True)
        self.max_bytes = max_bytes
        self.backup_count = backup_count

    def clear_old_logs(self):
        """清理旧的日志文件"""
        for file_path in self.logs_dir.glob("*"):
            if file_path.is_file():
                try:
                    file_path.unlink()
                except PermissionError:
                    # 文件可能正在被使用，跳过
                    continue
                except Exception as e:
                    logging.warning(f"无法删除日志文件 {file_path}: {e}")
        logging.info(f"已清理 {self.logs_dir} 目录中的所有旧日志文件")

    def generate_timestamp(self) -> str:
        """生成时间戳字符串"""
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    def save_backtest_results(
        self, results: Dict[str, Any], strategy_name: str, test_mode: str = "full"
    ) -> str:
        """
        保存回测结果到JSON文件

        Args:
            results: 回测结果数据
            strategy_name: 策略名称
            test_mode: 测试模式 ('full', 'train', 'val', 'small')

        Returns:
            保存的文件路径
        """
        timestamp = self.generate_timestamp()
        filename = f"{test_mode}_backtest_{strategy_name}_{timestamp}.json"
        filepath = self.logs_dir / filename

        # 确保目录存在
        filepath.parent.mkdir(parents=True, exist_ok=True)

        # 保存结果
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

        logging.info(f"回测结果已保存到: {filepath}")
        return str(filepath)

    def create_log_handler(
        self,
        strategy_name: str,
        test_mode: str = "full",
        use_json: bool = False,
        use_rotating: bool = True,
    ) -> logging.Handler:
        """
        创建日志处理器，将日志输出到文件

        Args:
            strategy_name: 策略名称
            test_mode: 测试模式
            use_json: 是否使用JSON格式输出
            use_rotating: 是否使用日志轮转

        Returns:
            日志处理器
        """
        timestamp = self.generate_timestamp()
        filename = f"{test_mode}_backtest_{strategy_name}_{timestamp}.log"
        filepath = self.logs_dir / filename

        # 确保目录存在
        filepath.parent.mkdir(parents=True, exist_ok=True)

        if use_rotating:
            handler = RotatingFileHandler(
                filepath,
                encoding="utf-8",
                maxBytes=self.max_bytes,
                backupCount=self.backup_count,
            )
        else:
            handler = logging.FileHandler(filepath, encoding="utf-8")

        if use_json:
            formatter = JSONFormatter()
        else:
            formatter = logging.Formatter(
                "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
            )
        handler.setFormatter(formatter)

        logging.info(
            f"日志文件已创建: {filepath}" + (" (JSON格式)" if use_json else "")
        )
        return handler


def get_backtest_results_from_report(train_report, val_report) -> Dict[str, Any]:
    """从BacktestReport对象提取结果数据"""
    results = {
        "test_config": {
            "strategy_name": getattr(train_report, "name", "Unknown")
            if train_report
            else "Unknown",
            "test_date": datetime.now().isoformat(),
            "initial_capital": getattr(train_report, "initial_cash", 0)
            if train_report
            else 0,
        },
        "performance_metrics": {},
    }

    if train_report:
        results["performance_metrics"]["train"] = {
            "total_trades": train_report.total_trades,
            "winning_trades": train_report.won,
            "losing_trades": train_report.lost,
            "win_rate": train_report.win_rate_pct,
            "avg_win": train_report.avg_win,
            "avg_loss": train_report.avg_loss,
            "profit_factor": (train_report.gross_pnl / abs(train_report.net_pnl))
            if train_report.net_pnl != 0
            else 0,
            "total_return": train_report.total_return_pct,
            "max_drawdown": train_report.max_drawdown_pct,
            "sqn": train_report.sqn,
            "sharpe_ratio": train_report.sharpe_ratio,
            "final_value": train_report.final_value,
        }

    if val_report:
        results["performance_metrics"]["validation"] = {
            "total_trades": val_report.total_trades,
            "winning_trades": val_report.won,
            "losing_trades": val_report.lost,
            "win_rate": val_report.win_rate_pct,
            "avg_win": val_report.avg_win,
            "avg_loss": val_report.avg_loss,
            "profit_factor": (val_report.gross_pnl / abs(val_report.net_pnl))
            if val_report.net_pnl != 0
            else 0,
            "total_return": val_report.total_return_pct,
            "max_drawdown": val_report.max_drawdown_pct,
            "sqn": val_report.sqn,
            "sharpe_ratio": val_report.sharpe_ratio,
            "final_value": val_report.final_value,
        }

    return results
