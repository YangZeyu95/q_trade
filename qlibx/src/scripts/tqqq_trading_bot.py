"""
华盛量化 OpenAPI - 自动交易程序
"""

import requests
import time
from datetime import datetime
import pytz
import logging
import json
import csv
import os
import base64
import argparse
import math
import threading
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from huasheng_api import HuashengGatewayAPI
from market_hours import get_market_status

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(SCRIPT_DIR, 'trading.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

DEFAULT_MAX_TOTAL_LEVERAGE = 2.0
DEFAULT_MIN_MAINTENANCE_MARGIN_RATIO = 0.30

# 华盛 TradeStockDeliverNotify.entrustStatus 状态码。未知状态保留原始值，
# 避免 SDK/网关增加新状态时被错误地当成已成交。
TRADE_PUSH_STATUS_NAMES = {
    "0": "Not Registered",
    "1": "Pending",
    "2": "Submitted",
    "3": "Cancel Pending",
    "4": "Partial Cancel Pending",
    "5": "Partially Cancelled",
    "6": "Canceled",
    "7": "Partial",
    "8": "Filled",
    "9": "Rejected",
    "A": "Modify Pending",
    "E": "Modify Pending",
    "F": "Rejected",
    "G": "Canceled",
    "H": "Review Pending",
    "J": "Review Failed",
    "W": "Pending Confirmation",
    "X": "Pre-Filled",
}

TRADE_LOG_FIELDS = [
    "timestamp", "symbol", "action", "quantity", "price", "volume",
    "order_id", "status", "stock_name", "filled_quantity",
    "remaining_quantity", "business_price", "entrust_price",
    "entrust_quantity", "record_no", "entrust_no", "status_updated_at",
]
TRADE_PUSH_FIELDS = TRADE_LOG_FIELDS[8:]

# 这些状态都表示委托仍可能发生变化。未知状态也按未终态处理，避免
# 网关新增状态时自动策略重复下单。
TERMINAL_ORDER_STATUSES = {
    "filled",
    "canceled",
    "cancelled",
    "partially canceled",
    "partially cancelled",
    "rejected",
    "review failed",
    "failed",
    "error",
}

EXECUTED_ORDER_STATUSES = {
    "filled",
    "partial",
    "partially canceled",
    "partially cancelled",
}


class TradingStrategy:

    def __init__(self, api, strategy_file=None, risk_config_file=None):
        self.api = api
        self.data_type = 20002  # 美股
        self.exchange_type = "P"  # 美股交易所
        script_dir = SCRIPT_DIR

        # 成交量阈值（单位：股）
        self.volume_threshold_buy = 60_000_000   # 60M
        self.volume_threshold_sell = 40_000_000  # 40M

        # 美东时区
        self.et_tz = pytz.timezone('America/New_York')

        # The backend writes this file from the dashboard. The engine reloads
        # it every loop, so risk changes do not require a process restart.
        self.strategy_file = strategy_file or os.path.join(script_dir, "stock_strategy.json")
        self.risk_config_file = risk_config_file or os.path.join(script_dir, "trading_config.json")
        self.max_total_leverage = DEFAULT_MAX_TOTAL_LEVERAGE
        self.min_maintenance_margin_ratio = DEFAULT_MIN_MAINTENANCE_MARGIN_RATIO
        self.load_risk_config()

        # 定义交易记录存放目录
        self.trades_dir = os.path.join(script_dir, "trades")
        os.makedirs(self.trades_dir, exist_ok=True)
        self._trade_log_lock = threading.RLock()
        # 推送可能早于 place_order 返回后的 record_trade 落盘，先暂存状态。
        self._early_trade_pushes = {}
        self._hs_trade_history = {}
        self._hs_trade_history_available = False
        self._hs_trade_history_complete = False
        self._hs_active_orders = []
        self._hs_active_orders_available = False

        # Initialize strategy configuration
        self.stock_strategies = self.load_stock_strategies()

    def load_risk_config(self):
        """Reload global risk controls written by the backend dashboard."""
        config = {
            "max_total_leverage": DEFAULT_MAX_TOTAL_LEVERAGE,
            "min_maintenance_margin_ratio": DEFAULT_MIN_MAINTENANCE_MARGIN_RATIO,
        }
        try:
            if os.path.exists(self.risk_config_file):
                with open(self.risk_config_file, 'r', encoding='utf-8') as f:
                    raw_config = json.load(f)
                if isinstance(raw_config, dict):
                    config.update(raw_config)

            max_leverage = float(config["max_total_leverage"])
            min_margin_ratio = float(config["min_maintenance_margin_ratio"])
            if (
                not math.isfinite(max_leverage)
                or not math.isfinite(min_margin_ratio)
                or max_leverage <= 0
                or not 0 < min_margin_ratio <= 1
            ):
                raise ValueError("risk values are outside valid ranges")
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as e:
            logger.error(f"无法读取风控配置 {self.risk_config_file}: {e}，使用默认值")
            max_leverage = DEFAULT_MAX_TOTAL_LEVERAGE
            min_margin_ratio = DEFAULT_MIN_MAINTENANCE_MARGIN_RATIO

        changed = (
            max_leverage != self.max_total_leverage
            or min_margin_ratio != self.min_maintenance_margin_ratio
        )
        self.max_total_leverage = max_leverage
        self.min_maintenance_margin_ratio = min_margin_ratio
        if changed:
            logger.info(
                "风控配置已加载: 总杠杆上限 %.2fx, 最低保证金比例 %.2f%%",
                self.max_total_leverage,
                self.min_maintenance_margin_ratio * 100,
            )
        return config

    def load_stock_strategies(self):
        """Load stock-specific strategy parameters from a JSON file"""
        required_fields = [
            "buy_point", "sell_point", "buy_total", "sell_total",
            "buy_day_interval", "sell_day_interval", "buy_price_interval",
            "max_position", "fear_greed_buy", "fear_greed_sell"
        ]

        if os.path.exists(self.strategy_file):
            try:
                with open(self.strategy_file, 'r', encoding='utf-8') as f:
                    strategies = json.load(f)
                    if not isinstance(strategies, dict):
                        raise ValueError("strategy file root must be an object")
                    valid_strategies = {}
                    # 验证每一只股票的配置是否完整
                    for symbol, config in strategies.items():
                        if not isinstance(symbol, str) or not symbol.strip():
                            logger.error("策略包含空或非法股票代码，已跳过")
                            continue
                        if not isinstance(config, dict):
                            logger.error("股票 %s 策略配置必须是对象，已跳过", symbol)
                            continue
                        missing = [f for f in required_fields if f not in config]
                        if missing:
                            logger.error(f"股票 {symbol} 策略配置不完整，缺失字段: {missing}. 该股票将被跳过交易。")
                            continue
                        try:
                            normalized = dict(config)
                            integer_fields = (
                                "buy_total", "sell_total", "buy_day_interval",
                                "sell_day_interval",
                            )
                            float_fields = (
                                "buy_point", "sell_point", "buy_price_interval",
                                "max_position", "fear_greed_buy", "fear_greed_sell",
                                "buy_limit_price", "sell_limit_price",
                            )
                            for field in integer_fields:
                                number = float(config[field])
                                if not math.isfinite(number) or not number.is_integer():
                                    raise ValueError(f"{field} must be a finite integer")
                                normalized[field] = int(number)
                            for field in float_fields:
                                number = float(config.get(field, 0.0))
                                if not math.isfinite(number):
                                    raise ValueError(f"{field} must be finite")
                                normalized[field] = number

                            if normalized["buy_total"] <= 0 or normalized["sell_total"] <= 0:
                                raise ValueError("buy_total/sell_total must be positive")
                            if normalized["buy_day_interval"] < 0 or normalized["sell_day_interval"] < 0:
                                raise ValueError("day intervals cannot be negative")
                            for field in (
                                "buy_point", "sell_point", "buy_price_interval",
                                "buy_limit_price", "sell_limit_price",
                            ):
                                if normalized[field] < 0:
                                    raise ValueError(f"{field} cannot be negative")
                            if not 0 < normalized["max_position"] <= 100:
                                raise ValueError("max_position must be in (0, 100]")
                            if normalized["fear_greed_buy"] > normalized["fear_greed_sell"]:
                                raise ValueError("buy signal threshold cannot exceed sell threshold")
                        except (KeyError, TypeError, ValueError, OverflowError) as exc:
                            logger.error(
                                "股票 %s 策略配置数值非法: %s. 该股票将被跳过交易。",
                                symbol,
                                exc,
                            )
                            continue
                        valid_strategies[symbol.strip().upper()] = normalized
                    return valid_strategies
            except Exception as e:
                logger.error(f"无法解析策略文件: {str(e)}")
                return {}
        else:
            logger.warning(f"找不到策略文件 {self.strategy_file}。")
            return {}

    def get_stock_strategy(self, symbol):
        """Get the strategy for a specific stock"""
        if symbol in self.stock_strategies:
            return self.stock_strategies[symbol]
        else:
            # If no strategy is found for the stock, report an error instead of using defaults
            logger.error(f"No strategy found for stock: {symbol}. Please add strategy parameters to {self.strategy_file}")
            return None

    def record_trade(self, symbol, action, quantity, price, volume, order_result=None):
        """
        Record a trade to the trade log file in CSV format
        """
        order_id = HuashengGatewayAPI.extract_order_id(order_result)
        # Use the trades subdirectory
        trade_log_file = os.path.join(self.trades_dir, f"{symbol.lower()}_trading.csv")

        # Write the trade record to the CSV file
        try:
            with self._trade_log_lock:
                # Keep the early-push lookup and initial write atomic with
                # handle_trade_push. Otherwise a push can arrive after pop()
                # but before the row exists and leave the row as Submitted.
                early_push = (
                    self._early_trade_pushes.pop(order_id, {}) if order_id else {}
                )
                trade_record = {
                    "timestamp": datetime.now(self.et_tz).isoformat(),
                    "symbol": symbol,
                    "action": action,
                    "quantity": quantity,
                    "price": price,
                    "volume": volume,
                    "order_id": order_id,
                    "status": early_push.get("status", "Submitted"),
                    **early_push,
                }
                # Check if file exists to determine if we need to write headers.
                file_exists = os.path.isfile(trade_log_file)
                existing_fields = None
                if file_exists:
                    with open(trade_log_file, "r", newline="", encoding="utf-8") as existing_file:
                        existing_fields = next(csv.reader(existing_file), None)
                with open(trade_log_file, 'a', newline='', encoding='utf-8') as csvfile:
                    fieldnames = existing_fields or TRADE_LOG_FIELDS
                    writer = csv.DictWriter(
                        csvfile,
                        fieldnames=fieldnames,
                        extrasaction="ignore",
                    )

                    if not file_exists or not existing_fields:
                        writer.writeheader()

                    writer.writerow(trade_record)

            logger.info(f"Trade recorded: {action} {quantity} shares of {symbol} at ${price} (ID: {order_id})")
        except Exception as e:
            logger.error(f"Failed to save trade log: {str(e)}")

    def submit_order(
        self,
        symbol,
        action,
        quantity,
        price,
        volume=0,
        entrust_type="3",
        record_price=None,
    ):
        """Submit and record one order for both strategy and manual flows."""
        if action not in {"buy", "sell"}:
            raise ValueError(f"Unsupported order action: {action!r}")
        side = "1" if action == "buy" else "2"
        result = self.api.place_order(
            exchangeType=self.exchange_type,
            stock_code=symbol,
            entrustAmount=quantity,
            entrustPrice=str(price),
            entrustBs=side,
            entrustType=entrust_type,
        )
        if result:
            self.record_trade(
                symbol,
                action,
                quantity,
                price if record_price is None else record_price,
                volume,
                result,
            )
        return result

    @staticmethod
    def _payload_value(payload, field, default=""):
        """Safely read a protobuf field or a test double field."""
        try:
            value = getattr(payload, field, default)
        except Exception:
            return default
        return default if value is None else value

    @staticmethod
    def _normalize_trade_push_status(raw_status):
        status = str(raw_status or "").strip()
        return TRADE_PUSH_STATUS_NAMES.get(status.upper(), status or "Unknown")

    def handle_trade_push(self, payload):
        """处理 SDK 推送的委托状态，并更新对应交易 CSV。"""
        record_no = str(self._payload_value(payload, "recordNo")).strip()
        entrust_no = str(self._payload_value(payload, "entrustNo")).strip()
        order_ids = []
        for order_id in (record_no, entrust_no):
            if order_id and order_id not in order_ids:
                order_ids.append(order_id)
        if not order_ids:
            logger.warning("收到没有 recordNo/entrustNo 的交易推送，忽略: %s", payload)
            return False

        new_status = self._normalize_trade_push_status(
            self._payload_value(payload, "entrustStatus")
        )
        stock_code = str(self._payload_value(payload, "stockCode", "")).strip()
        push_data = {
            "status": new_status,
            "stock_name": self._payload_value(payload, "stockName"),
            "filled_quantity": self._payload_value(payload, "sumBusinessAmount")
            or self._payload_value(payload, "businessAmount"),
            "remaining_quantity": self._payload_value(payload, "leftAmount"),
            "business_price": self._payload_value(payload, "businessPrice"),
            "entrust_price": self._payload_value(payload, "entrustPrice")
            or self._payload_value(payload, "originalPrice"),
            "entrust_quantity": self._payload_value(payload, "entrustAmount")
            or self._payload_value(payload, "originalAmount"),
            "record_no": record_no,
            "entrust_no": entrust_no,
            "status_updated_at": datetime.now(self.et_tz).isoformat(),
        }
        matched = False
        updated = False

        with self._trade_log_lock:
            if os.path.exists(self.trades_dir):
                for filename in os.listdir(self.trades_dir):
                    if not filename.endswith("_trading.csv"):
                        continue

                    filepath = os.path.join(self.trades_dir, filename)
                    rows = []
                    file_updated = False
                    try:
                        with open(filepath, "r", encoding="utf-8") as f:
                            reader = csv.DictReader(f)
                            fieldnames = reader.fieldnames
                            if not fieldnames:
                                continue
                            for field in TRADE_PUSH_FIELDS:
                                if field not in fieldnames:
                                    fieldnames.append(field)
                            for row in reader:
                                for field in TRADE_PUSH_FIELDS:
                                    row.setdefault(field, "")
                                row_order_id = str(row.get("order_id") or "").strip()
                                if row_order_id in order_ids:
                                    matched = True
                                    if str(row.get("status")) != new_status:
                                        logger.info(
                                            "交易推送更新订单 %s 状态: %s -> %s",
                                            row_order_id,
                                            row.get("status"),
                                            new_status,
                                        )
                                    for field, value in push_data.items():
                                        if str(row.get(field) or "") != str(value or ""):
                                            row[field] = value
                                            file_updated = True
                                            updated = True
                                rows.append(row)

                        if file_updated:
                            with open(filepath, "w", encoding="utf-8", newline="") as f:
                                writer = csv.DictWriter(f, fieldnames=fieldnames)
                                writer.writeheader()
                                writer.writerows(rows)
                    except Exception as exc:
                        logger.error("交易推送更新 %s 失败: %s", filename, exc)

            if not matched:
                # place_order 返回订单号后才会调用 record_trade；推送可能先到。
                # 记录两个编号，兼容改单/撤单时 entrustNo 变化的情况。
                for order_id in order_ids:
                    self._early_trade_pushes[order_id] = dict(push_data)

        logger.info(
            "收到交易推送: %s order=%s status=%s businessAmount=%s leftAmount=%s%s",
            stock_code or "未知股票",
            "/".join(order_ids),
            new_status,
            self._payload_value(payload, "businessAmount"),
            self._payload_value(payload, "leftAmount"),
            "（尚未找到本地订单，已暂存）" if not matched else "",
        )
        return matched or updated

    def update_pending_orders(self):
        """
        Check all trade logs for non-terminal orders and update their status.
        """
        logger.debug("Checking for pending orders to update status...")
        if not os.path.exists(self.trades_dir):
            return

        for filename in os.listdir(self.trades_dir):
            if not filename.endswith("_trading.csv"):
                continue

            filepath = os.path.join(self.trades_dir, filename)
            try:
                # 先只读取订单号。网络查询期间不持有文件锁，避免 SDK 回调
                # 被慢网关请求阻塞。
                with self._trade_log_lock:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        reader = csv.DictReader(f)
                        pending_order_ids = [
                            str(row.get('order_id') or '').strip()
                            for row in reader
                            if str(row.get('order_id') or '').strip()
                            and self._is_active_order_status(row.get('status'))
                        ]

                status_updates = {}
                for order_id in pending_order_ids:
                    try:
                        details = self.api.get_order_details(order_id)
                    except Exception as exc:
                        logger.error(f"Error querying status for order {order_id}: {exc}")
                        continue
                    if details:
                        # 华盛返回的状态字段通常是 orderStatus 或 entrustStatus。
                        raw_status = details.get('orderStatusName') or details.get('orderStatus')
                        if raw_status:
                            status_updates[order_id] = self._normalize_trade_push_status(raw_status)

                if not status_updates:
                    continue

                # 查询完成后重新读取。若 SDK 回调已经把订单改成终态，
                # 这里会跳过该行，避免旧的轮询结果覆盖新推送。
                with self._trade_log_lock:
                    rows = []
                    updated = False
                    with open(filepath, 'r', encoding='utf-8') as f:
                        reader = csv.DictReader(f)
                        fieldnames = reader.fieldnames
                        for row in reader:
                            order_id = str(row.get('order_id') or '').strip()
                            if (
                                order_id in status_updates
                                and self._is_active_order_status(row.get('status'))
                            ):
                                new_status = status_updates[order_id]
                                if str(new_status) != str(row['status']):
                                    logger.info(
                                        f"Order {order_id} status updated: {row['status']} -> {new_status}"
                                    )
                                    row['status'] = new_status
                                    updated = True
                            rows.append(row)

                    if updated and fieldnames:
                        with open(filepath, 'w', encoding='utf-8', newline='') as f:
                            writer = csv.DictWriter(f, fieldnames=fieldnames)
                            writer.writeheader()
                            writer.writerows(rows)
            except Exception as e:
                logger.error(f"Error updating status in {filename}: {e}")

    def is_trading_time(self):
        """检查是否在交易时间"""
        return get_market_status(datetime.now(self.et_tz))["is_open"]

    def is_near_close(self, minutes_before=10):
        """检查是否接近收盘"""
        now_et = datetime.now(self.et_tz)
        market_status = get_market_status(now_et)
        if not market_status["is_open"]:
            return False
        market_close = datetime.fromisoformat(market_status["market_close"])

        time_to_close = (market_close - now_et).total_seconds() / 60

        return 0 < time_to_close <= minutes_before

    @staticmethod
    def _normalize_market_symbol(symbol):
        return (
            str(symbol or "").upper()
            .replace("US.", "")
            .replace(".US", "")
            .replace("HK.", "")
            .replace(".HK", "")
        )

    def get_shared_market_snapshot(self):
        """Read the backend-owned one-minute snapshot for one strategy round."""
        try:
            response = requests.get(
                "http://localhost:8000/api/market/snapshot?force=true",
                # The first SDK history refresh walks bounded historical
                # windows and can legitimately take longer than quote-only
                # snapshots. Subsequent 60-second rounds use its daily cache.
                timeout=30,
            )
            if response.status_code != 200:
                logger.warning("共享行情快照请求失败: HTTP %s", response.status_code)
                return None
            data = response.json()
            if not isinstance(data, dict) or data.get("stale") or not data.get("complete"):
                logger.warning(
                    "共享行情快照不可用于交易: stale=%s complete=%s age=%s",
                    data.get("stale") if isinstance(data, dict) else None,
                    data.get("complete") if isinstance(data, dict) else None,
                    data.get("age_seconds") if isinstance(data, dict) else None,
                )
                return None

            realtime = data.get("realtime") or {}
            quotes = {}
            signals = {}
            for raw_symbol, item in realtime.items():
                if not isinstance(item, dict):
                    continue
                normalized = self._normalize_market_symbol(raw_symbol)
                quotes[normalized] = {
                    "lastPrice": item.get("regularLastPrice", item.get("lastPrice", 0)),
                    "volume": item.get("volume", 0),
                }
                if item.get("signal_available"):
                    signals[normalized] = item.get("signal")

            positions = {}
            for position in data.get("holdings") or []:
                if isinstance(position, dict):
                    positions[self._normalize_market_symbol(position.get("stockCode"))] = (
                        self._position_quantity_from_record(position)
                    )

            account = data.get("account") or {}
            try:
                risk_metrics = {
                    "equity": float(account["total_asset"]),
                    "buying_power": float(account["buying_power"]),
                    "gross_market_value": float(account["market_value"]),
                }
                if (
                    not all(math.isfinite(value) for value in risk_metrics.values())
                    or risk_metrics["equity"] <= 0
                    or risk_metrics["gross_market_value"] < 0
                ):
                    risk_metrics = None
            except (KeyError, TypeError, ValueError):
                risk_metrics = None

            active_orders = data.get("active_orders")
            if (
                data.get("active_orders_available") is not True
                or not isinstance(active_orders, list)
            ):
                logger.warning("共享行情快照缺少 HS 当日委托，跳过本轮策略")
                return None
            for order in active_orders:
                if not isinstance(order, dict):
                    logger.warning("共享行情快照包含非法 HS 当日委托，跳过本轮策略")
                    return None
                order_id = str(order.get("order_id") or "").strip()
                order_symbol = self._normalize_market_symbol(order.get("symbol"))
                action = str(order.get("action") or "").strip().lower()
                try:
                    quantity = float(order.get("quantity"))
                    price = float(order.get("price"))
                except (TypeError, ValueError):
                    logger.warning("共享行情快照包含无法计算敞口的 HS 当日委托")
                    return None
                if (
                    not order_id
                    or not order_symbol
                    or action not in {"buy", "sell"}
                    or not math.isfinite(quantity)
                    or quantity < 0
                    or not math.isfinite(price)
                    or price < 0
                    or (action == "buy" and quantity > 0 and price <= 0)
                ):
                    logger.warning("共享行情快照包含非法 HS 当日委托，跳过本轮策略")
                    return None

            return {
                "quotes": quotes,
                "signals": signals,
                "positions": positions,
                "risk_metrics": risk_metrics,
                "trade_history": (
                    data.get("trade_history")
                    if isinstance(data.get("trade_history"), dict) else {}
                ),
                "trade_history_available": (
                    data.get("trade_history_available") is True
                    and isinstance(data.get("trade_history"), dict)
                ),
                "trade_history_complete": data.get("trade_history_complete") is True,
                "active_orders": active_orders,
                "active_orders_available": True,
            }
        except Exception as exc:
            logger.warning("读取共享行情快照异常: %s", exc)
            return None

    @staticmethod
    def _position_quantity_from_record(position):
        value = (
            position.get("enableAmount")
            or position.get("currentAmount")
            or position.get("canSellAmount")
            or position.get("canSellQty")
            or 0
        )
        try:
            quantity = float(value)
            if not math.isfinite(quantity) or quantity < 0 or not quantity.is_integer():
                return 0
            return int(quantity)
        except (TypeError, ValueError, OverflowError):
            return 0

    def _refresh_candidate_quote(self, symbol, quote, strategy, action):
        """Confirm a candidate's price immediately before a live order."""
        try:
            fresh_quote = self.api.get_realtime_quote(symbol, self.data_type)
        except Exception as exc:
            logger.warning("下单前确认 %s 行情失败: %s", symbol, exc)
            return None
        if not fresh_quote:
            logger.warning("下单前未取得 %s 最新行情，取消本次下单", symbol)
            return None
        try:
            fresh_price = float(fresh_quote.get("lastPrice", 0))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(fresh_price) or fresh_price <= 0:
            return None
        if action == "buy" and strategy["buy_point"] > 0 and fresh_price > strategy["buy_point"]:
            logger.info("%s 下单前价格 %.4f 已超过买入价位，取消下单", symbol, fresh_price)
            return None
        if action == "sell" and strategy["sell_point"] > 0 and fresh_price < strategy["sell_point"]:
            logger.info("%s 下单前价格 %.4f 未达到卖出价位，取消下单", symbol, fresh_price)
            return None
        return fresh_quote

    def get_signal_indicator(self, symbol):
        """
        Fetch the current signal indicator for a specific stock.
        """
        try:
            # Fetch from our backend's realtime endpoint which now includes signals
            response = requests.get("http://localhost:8000/api/realtime", timeout=5)
            if response.status_code == 200:
                data = response.json()
                symbol_data = data.get(symbol, {})
                if symbol_data.get("signal_available", "signal" in symbol_data) is False:
                    return None
                return symbol_data.get("signal")
            return None
        except Exception as e:
            logger.error(f"Failed to fetch signal indicator for {symbol}: {e}")
            return None

    def _get_account_risk_metrics(self):
        """Return account equity and available buying power for risk checks.

        ``assetBalance`` is used as account equity.  Unlike the old
        holdings-only denominator, this remains meaningful when the account
        has cash or margin debt.  ``buyPower`` is kept as a separate check:
        it describes what can be purchased, not how much concentration risk
        the account should take.
        """
        try:
            funds = self.api.get_account_funds(exchange_type=self.exchange_type)
        except Exception as e:
            logger.error(f"获取账户资金信息异常: {e}")
            return None

        if not isinstance(funds, dict):
            logger.error("无法获取账户资金信息，跳过买入以保护账户")
            return None

        try:
            account_equity = float(funds.get("assetBalance"))
            buying_power = float(funds.get("buyPower"))
            gross_market_value = float(
                self.api.get_total_portfolio_value(self.exchange_type)
            )
        except (TypeError, ValueError):
            logger.error(
                "账户资金信息缺少有效的 assetBalance、buyPower 或持仓市值，跳过买入"
            )
            return None

        if (
            not all(
                math.isfinite(value)
                for value in (account_equity, buying_power, gross_market_value)
            )
            or account_equity <= 0
            or gross_market_value < 0
        ):
            logger.warning(
                f"账户风险数据异常 (净资产={account_equity:.2f}, "
                f"持仓市值={gross_market_value:.2f})，跳过买入"
            )
            return None

        return {
            "equity": account_equity,
            "buying_power": buying_power,
            "gross_market_value": gross_market_value,
        }

    @staticmethod
    def _is_active_order_status(status):
        """Return whether a local order record is not in a terminal state."""
        normalized = " ".join(str(status or "").strip().lower().split())
        if normalized in TERMINAL_ORDER_STATUSES:
            return False
        # Treat an empty/unknown status conservatively when an order id exists;
        # a new gateway status must not accidentally permit duplicate orders.
        return True

    @staticmethod
    def _positive_float(value):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and number > 0 else None

    def _get_pending_orders(self):
        """Combine local non-terminal rows with HS's current active orders."""
        pending_orders = []
        if os.path.exists(self.trades_dir):
            # The CSV is shared by the SDK callback, HTTP fallback, and strategy
            # loop, so this read uses the same lock as their writes.
            with self._trade_log_lock:
                for filename in os.listdir(self.trades_dir):
                    if not filename.endswith("_trading.csv"):
                        continue
                    filepath = os.path.join(self.trades_dir, filename)
                    try:
                        with open(filepath, "r", encoding="utf-8") as f:
                            for row in csv.DictReader(f):
                                order_id = str(row.get("order_id") or "").strip()
                                if not order_id or not self._is_active_order_status(row.get("status")):
                                    continue

                                symbol = str(row.get("symbol") or "").strip().upper()
                                action = str(row.get("action") or "").strip().lower()
                                quantity = self._positive_float(
                                    row.get("remaining_quantity")
                                    or row.get("entrust_quantity")
                                    or row.get("quantity")
                                )
                                price = self._positive_float(
                                    row.get("entrust_price") or row.get("price")
                                )
                                notional = (
                                    quantity * price
                                    if quantity is not None and price is not None
                                    else 0.0
                                )
                                pending_orders.append({
                                    "order_id": order_id,
                                    "symbol": symbol,
                                    "action": action,
                                    "status": str(row.get("status") or "").strip(),
                                    "quantity": quantity or 0.0,
                                    "price": price or 0.0,
                                    "notional": notional,
                                    "source": "local",
                                })
                    except (OSError, csv.Error) as exc:
                        logger.error("读取未完成订单 %s 失败: %s", filepath, exc)

        # The SDK list is authoritative for orders that were accepted by HS
        # but never reached the local CSV (for example, a response timeout).
        # Prefer its remaining quantity when the same order exists locally.
        by_order_id = {
            order["order_id"]: order for order in pending_orders if order["order_id"]
        }
        if self._hs_active_orders_available:
            for raw_order in self._hs_active_orders:
                if not isinstance(raw_order, dict):
                    continue
                order_id = str(raw_order.get("order_id") or "").strip()
                symbol = self._normalize_market_symbol(raw_order.get("symbol"))
                if not order_id or not symbol:
                    continue
                quantity = self._positive_float(raw_order.get("quantity"))
                price = self._positive_float(raw_order.get("price"))
                sdk_order = {
                    "order_id": order_id,
                    "symbol": symbol,
                    "action": str(raw_order.get("action") or "").strip().lower(),
                    "status": str(raw_order.get("status") or "").strip(),
                    "quantity": quantity or 0.0,
                    "price": price or 0.0,
                    "notional": (
                        quantity * price
                        if quantity is not None and price is not None
                        else 0.0
                    ),
                    "source": "HS SDK",
                }
                by_order_id[order_id] = sdk_order
        return list(by_order_id.values())

    def _has_pending_order(self, symbol):
        """Prevent a symbol from accumulating multiple live orders."""
        normalized_symbol = str(symbol or "").strip().upper()
        pending = [
            order for order in self._get_pending_orders()
            if order["symbol"] == normalized_symbol
        ]
        if pending:
            order_ids = ", ".join(order["order_id"] for order in pending)
            logger.info(
                "%s 存在未终态订单 (%s)，跳过本轮自动下单",
                normalized_symbol,
                order_ids,
            )
            return True
        return False

    def _get_pending_buy_exposure(self):
        """Return pending buy notional overall and grouped by symbol."""
        total = 0.0
        by_symbol = {}
        for order in self._get_pending_orders():
            if order["action"] != "buy":
                continue
            notional = order["notional"]
            total += notional
            by_symbol[order["symbol"]] = by_symbol.get(order["symbol"], 0.0) + notional
        return total, by_symbol

    def execute_strategy(self, symbol, snapshot=None):
        """执行交易策略 for a specific stock"""
        try:
            if snapshot is not None and "trade_history_available" in snapshot:
                self._hs_trade_history_available = snapshot["trade_history_available"] is True
                self._hs_trade_history = (
                    snapshot.get("trade_history")
                    if self._hs_trade_history_available
                    and isinstance(snapshot.get("trade_history"), dict)
                    else {}
                )
                self._hs_trade_history_complete = snapshot.get("trade_history_complete") is True
            if snapshot is not None and "active_orders_available" in snapshot:
                self._hs_active_orders_available = snapshot["active_orders_available"] is True
                self._hs_active_orders = (
                    snapshot.get("active_orders")
                    if self._hs_active_orders_available
                    and isinstance(snapshot.get("active_orders"), list)
                    else []
                )

            # 检查是否在交易时间
            if not self.is_trading_time():
                logger.info(f"{symbol} 非美股常规交易时段，跳过")
                return

            if self._has_pending_order(symbol):
                return

            # 检查是否接近收盘
            # if not self.is_near_close():
            #     logger.info(f"未到收盘前10分钟，跳过 {symbol}")
            #     return

            # A strategy round normally supplies one shared quote snapshot.
            # Direct callers without a snapshot retain the old single-symbol
            # behavior for compatibility and isolated tests.
            if snapshot is None:
                try:
                    quote = self.api.get_realtime_quote(symbol, self.data_type)
                except Exception as e:
                    logger.error(f"获取 {symbol} 行情异常: {e}")
                    return
            else:
                quote = (snapshot.get("quotes") or {}).get(
                    self._normalize_market_symbol(symbol)
                )

            if not quote:
                logger.error(f"无法获取 {symbol} 实时报价")
                return

            # 获取当日累计成交量
            volume = quote.get("volume", 0)
            try:
                last_price = float(quote.get("lastPrice", 0))
            except (TypeError, ValueError):
                logger.warning(f"{symbol} 价格不是有效数字，跳过交易")
                return

            # Defense: Avoid zero price crashes
            if not math.isfinite(last_price) or last_price <= 0:
                logger.warning(f"{symbol} 价格异常 ({last_price}), 跳过交易")
                return

            # Signals are also read from the same round snapshot.  The legacy
            # method remains for direct one-symbol callers.
            if snapshot is None:
                raw_signal = self.get_signal_indicator(symbol)
            else:
                raw_signal = (snapshot.get("signals") or {}).get(
                    self._normalize_market_symbol(symbol)
                )
            if raw_signal is None:
                logger.warning(f"{symbol} 贪恐指数不可用，跳过交易")
                return
            try:
                signal_value = float(raw_signal)
            except (ValueError, TypeError):
                logger.error(f"{symbol} 收到非法信号值: {raw_signal}, 使用中性值 0")
                signal_value = 0.0
            if not math.isfinite(signal_value):
                logger.error(f"{symbol} 收到非有限信号值: {raw_signal}, 使用中性值 0")
                signal_value = 0.0

            logger.info(f"{symbol} 价格: ${last_price:.2f}, 成交量: {volume:,}, 信号值: {signal_value:.1f}")

            # Get the strategy for this stock
            strategy = self.get_stock_strategy(symbol)
            if not strategy:
                logger.error(f"No strategy available for {symbol}, skipping trade")
                return

            # Dry Run Protection
            is_dry_run = getattr(self, 'dry_run', False)

            # buy_point/sell_point <= 0 means no price restriction
            buy_price_ok = (strategy['buy_point'] <= 0) or (last_price <= strategy['buy_point'])
            sell_price_ok = (strategy['sell_point'] <= 0) or (last_price >= strategy['sell_point'])

            # Check buy conditions: price AND signal indicator
            if buy_price_ok and signal_value <= strategy['fear_greed_buy']:
                if snapshot is not None:
                    quote = self._refresh_candidate_quote(
                        symbol, quote, strategy, "buy"
                    )
                    if quote is None:
                        return
                    try:
                        last_price = float(quote.get("lastPrice", 0))
                    except (TypeError, ValueError):
                        return
                    if not math.isfinite(last_price) or last_price <= 0:
                        return
                    volume = quote.get("volume", 0)

                # Calculate quantity based on strategy
                quantity = int(strategy['buy_total'] / last_price)
                if quantity <= 0:
                    logger.info(f"{symbol} 计算买入数量为 0 (buy_total={strategy['buy_total']}, price={last_price}), 跳过")
                    return

                # Check position control
                if snapshot is None:
                    current_position_qty = self.api.get_stock_position_qty(
                        symbol, self.exchange_type
                    )
                else:
                    current_position_qty = (snapshot.get("positions") or {}).get(
                        self._normalize_market_symbol(symbol), 0
                    )
                current_position_value = current_position_qty * last_price
                risk_metrics = (
                    self._get_account_risk_metrics()
                    if snapshot is None
                    else snapshot.get("risk_metrics")
                )
                if not risk_metrics:
                    logger.warning("共享账户风控快照不可用，跳过 %s 买入", symbol)
                    return

                pending_buy_value, pending_buy_by_symbol = self._get_pending_buy_exposure()

                account_equity = risk_metrics["equity"]
                buying_power = risk_metrics["buying_power"]
                max_position_pct = strategy['max_position']
                buy_limit = strategy.get('buy_limit_price', 0.0)
                order_price = buy_limit if buy_limit > 0 else round(last_price + 0.01, 2)
                order_value = quantity * order_price
                projected_position_value = (
                    current_position_value
                    + pending_buy_by_symbol.get(symbol.upper(), 0.0)
                    + order_value
                )
                projected_position_pct = (projected_position_value / account_equity) * 100
                gross_market_value = max(
                    risk_metrics["gross_market_value"], current_position_value
                )
                projected_gross_market_value = (
                    gross_market_value + pending_buy_value + order_value
                )
                projected_total_leverage = projected_gross_market_value / account_equity
                projected_margin_ratio = (
                    account_equity / projected_gross_market_value
                    if projected_gross_market_value > 0 else float("inf")
                )

                if projected_total_leverage > self.max_total_leverage:
                    logger.info(
                        f"{symbol} 买入后预计总杠杆 {projected_total_leverage:.2f}x "
                        f"超过上限 {self.max_total_leverage:.2f}x"
                    )
                    return

                if projected_margin_ratio < self.min_maintenance_margin_ratio:
                    logger.info(
                        f"{symbol} 买入后预计保证金比例 "
                        f"{projected_margin_ratio:.2%} 低于最低要求 "
                        f"{self.min_maintenance_margin_ratio:.2%}"
                    )
                    return

                if projected_position_pct >= max_position_pct:
                    logger.info(
                        f"{symbol} 买入后预计仓位 {projected_position_pct:.2f}% "
                        f"(${projected_position_value:.2f} / 净资产 ${account_equity:.2f}) "
                        f"将超过上限 {max_position_pct:.2f}%"
                    )
                    return

                projected_buying_power = pending_buy_value + order_value
                if projected_buying_power > buying_power:
                    logger.info(
                        f"{symbol} 当前订单及未成交买单合计 ${projected_buying_power:.2f} "
                        f"超过可用购买力 "
                        f"${buying_power:.2f}，跳过买入"
                    )
                    return

                if self.check_buy_conditions(symbol, strategy, last_price):
                    logger.info(f"满足买入条件: 价格 ${last_price:.2f}, 信号 {signal_value:.1f} <= {strategy['fear_greed_buy']:.1f}")

                    if is_dry_run:
                        logger.info(f"[DRY RUN] Would place BUY order for {quantity} shares of {symbol}")
                        return

                    # 有限价则用限价挂单（博更好价格），否则用市价+1分钱（快速成交）
                    entrust_price = str(order_price)

                    self.submit_order(
                        symbol,
                        "buy",
                        quantity,
                        entrust_price,
                        volume,
                        record_price=last_price,
                    )

            elif sell_price_ok and signal_value >= strategy['fear_greed_sell']:
                # Check sell conditions
                if snapshot is None:
                    position_qty = self.api.get_stock_position_qty(
                        symbol, self.exchange_type
                    )
                else:
                    position_qty = (snapshot.get("positions") or {}).get(
                        self._normalize_market_symbol(symbol), 0
                    )
                if position_qty > 0:
                    if snapshot is not None:
                        quote = self._refresh_candidate_quote(
                            symbol, quote, strategy, "sell"
                        )
                        if quote is None:
                            return
                        try:
                            last_price = float(quote.get("lastPrice", 0))
                        except (TypeError, ValueError):
                            return
                        if not math.isfinite(last_price) or last_price <= 0:
                            return
                        volume = quote.get("volume", 0)
                        sell_price_ok = (
                            strategy['sell_point'] <= 0
                            or last_price >= strategy['sell_point']
                        )
                        if not sell_price_ok:
                            return
                    if not self.check_sell_conditions(symbol, strategy, last_price):
                        return

                    reason = "价格" if last_price >= strategy['sell_point'] else "信号值"
                    logger.info(f"满足卖出条件({reason}): 价格 ${last_price:.2f}, 信号 {signal_value:.1f}")

                    if is_dry_run:
                        logger.info(f"[DRY RUN] Would place SELL order for {symbol}")
                        return

                    # Calculate quantity to sell
                    sell_total = strategy.get('sell_total', 0)
                    if sell_total <= 0:
                        logger.warning("卖出金额未配置或为0，禁止卖出，避免误触发全仓卖出")
                        return

                    sell_quantity = int(sell_total / last_price)
                    # Cannot sell more than we have
                    sell_quantity = min(sell_quantity, position_qty)
                    if sell_quantity <= 0:
                        logger.warning(f"卖出金额设定过小 ({sell_total}), 计算出数量为0, 跳过")
                        return

                    # 有限价则用限价挂单（博更好价格），否则用市价-1分钱（快速成交）
                    sell_limit = strategy.get('sell_limit_price', 0.0)
                    entrust_price = str(sell_limit) if sell_limit > 0 else str(round(last_price - 0.01, 2))

                    self.submit_order(
                        symbol,
                        "sell",
                        sell_quantity,
                        entrust_price,
                        volume,
                        record_price=last_price,
                    )
        except Exception as e:
            logger.error(f"执行 {symbol} 策略时发生未捕获异常: {e}")

    def check_buy_conditions(self, symbol, strategy, current_price):
        """
        Check if buy conditions are met based on date and price intervals
        Args:
            symbol: Stock symbol
            strategy: Strategy parameters for the stock
            current_price: Current market price
        Returns:
            Boolean indicating if buy conditions are met
        """
        # Check date interval (days since last buy)
        days_interval = strategy['buy_day_interval']
        if days_interval > 0:
            last_buy_date = self.get_last_buy_date(symbol)
            if last_buy_date:
                days_since_last_buy = (datetime.now(self.et_tz).date() - last_buy_date).days
                if days_since_last_buy < days_interval:
                    logger.info(f"{symbol} 未到买入日期间隔: 距离上次买入 {days_since_last_buy} 天, 需要等待 {days_interval} 天")
                    return False
            else:
                # If no previous buy records, we can proceed
                logger.info(f"{symbol} 无历史买入记录，可以执行买入")
        else:
            # If days interval is 0 or negative, there's no date restriction
            logger.info(f"{symbol} 买入日期间隔设置为0，无日期限制")

        # Check price interval (percentage difference from last executed price)
        # For price interval checking, we need to read the last buy price from the trade history
        last_buy_price = self.get_last_buy_price(symbol)
        if last_buy_price and strategy['buy_price_interval'] > 0:
            price_diff_pct = ((current_price - last_buy_price) / last_buy_price) * 100
            if abs(price_diff_pct) < strategy['buy_price_interval']:
                logger.info(f"{symbol} 未到买入价格间隔: 当前价格 {current_price:.2f}, 上次买入价 {last_buy_price:.2f}, 价差 {price_diff_pct:.2f}%, 需要至少 {strategy['buy_price_interval']:.2f}%")
                return False

        return True

    def check_sell_conditions(self, symbol, strategy, current_price):
        """
        Check if sell conditions are met based on date interval.
        """
        # Check date interval (days since last sell)
        days_interval = strategy.get('sell_day_interval', 0)
        if days_interval > 0:
            last_sell_date = self.get_last_sell_date(symbol)
            if last_sell_date:
                days_since_last_sell = (datetime.now(self.et_tz).date() - last_sell_date).days
                if days_since_last_sell < days_interval:
                    logger.info(f"{symbol} 未到卖出日期间隔: 距离上次卖出 {days_since_last_sell} 天, 需要等待 {days_interval} 天")
                    return False
        return True

    def get_last_sell_date(self, symbol):
        """Get the last sell date from history."""
        return self._get_last_trade_info(symbol, "sell", "date")

    def get_last_sell_price(self, symbol):
        """Get the last sell price from history."""
        return self._get_last_trade_info(symbol, "sell", "price")

    def get_last_buy_date(self, symbol):
        """Get the last buy date from history."""
        return self._get_last_trade_info(symbol, "buy", "date")

    def get_last_buy_price(self, symbol):
        """Get the last buy price from history."""
        return self._get_last_trade_info(symbol, "buy", "price")

    def _get_last_trade_info(self, symbol, action, info_type):
        """
        Get the latest executed buy/sell from the shared HS SDK snapshot.

        Local CSV is retained only for direct/legacy callers where no HS
        history snapshot is available.  When HS is authoritative and reports
        no record for a side, do not resurrect an unrelated local order.
        info_type: 'date' or 'price'
        """
        normalized_symbol = self._normalize_market_symbol(symbol)
        normalized_action = str(action or "").strip().lower()
        if self._hs_trade_history_available:
            side_records = self._hs_trade_history.get(normalized_symbol) or {}
            record = side_records.get(normalized_action)
            if not isinstance(record, dict):
                if not self._hs_trade_history_complete:
                    raise RuntimeError(
                        f"HS SDK {normalized_symbol} {normalized_action} 历史仍在回填"
                    )
                return None
            try:
                if info_type == "price":
                    price = self._positive_float(record.get("price"))
                    if price is None:
                        raise ValueError("HS trade price is invalid")
                    return price
                if info_type == "date":
                    return datetime.strptime(str(record["date"]), "%Y-%m-%d").date()
            except (KeyError, TypeError, ValueError) as exc:
                logger.error(
                    "HS SDK %s %s 成交记录无效: %s",
                    normalized_symbol,
                    normalized_action,
                    exc,
                )
                raise RuntimeError(
                    f"HS SDK {normalized_symbol} {normalized_action} 成交记录无效"
                ) from exc

        trade_log_file = os.path.join(self.trades_dir, f"{symbol.lower()}_trading.csv")
        if not os.path.exists(trade_log_file):
            return None

        try:
            with open(trade_log_file, 'r', encoding='utf-8') as csvfile:
                rows = list(csv.DictReader(csvfile))
                for row in reversed(rows):
                    try:
                        status = " ".join(
                            str(row.get("status") or "").strip().lower().split()
                        )
                        if (
                            str(row.get("action") or "").strip().lower() == action
                            and status in EXECUTED_ORDER_STATUSES
                        ):
                            if info_type == "price":
                                execution_price = self._positive_float(
                                    row.get("business_price")
                                ) or self._positive_float(row.get("price"))
                                if execution_price is None:
                                    raise ValueError("trade price is not positive and finite")
                                return execution_price
                            elif info_type == "date":
                                timestamp_str = str(row["timestamp"]).strip()
                                date_str = timestamp_str.split('T')[0]
                                return datetime.strptime(date_str, '%Y-%m-%d').date()
                    except (KeyError, TypeError, ValueError) as e:
                        logger.debug(
                            "Skipping malformed row in %s: %s (%s)",
                            trade_log_file,
                            row,
                            e,
                        )
                        continue
        except Exception as e:
            logger.error(f"Error reading last {action} {info_type} for {symbol}: {str(e)}")
        return None


def main():
    """主程序"""
    parser = argparse.ArgumentParser(description="华盛量化自动交易程序")
    parser.add_argument(
        "--live",
        action="store_true",
        default=False,
        help="启用真实交易模式（默认为Dry Run模拟模式，不下单）",
    )
    args = parser.parse_args()

    dry_run = not args.live

    logger.info("=" * 60)
    logger.info("量化交易程序启动")
    if dry_run:
        logger.info("🔒 DRY RUN 模式 — 不会真实下单，仅模拟输出")
        logger.info("   如需真实交易，请使用: python tqqq_trading_bot.py --live")
    else:
        logger.info("🚀 真实交易模式 — 订单将被真实提交！")
    logger.info("=" * 60)

    # 初始化API
    api = HuashengGatewayAPI()

    # 创建策略实例
    strategy = TradingStrategy(api)
    strategy.dry_run = dry_run

    # 获取所有配置的股票
    stocks = list(strategy.stock_strategies.keys())
    logger.info(f"配置的交易股票: {stocks}")

    # Strategy evaluation stays at 60 seconds to avoid unnecessary repeated
    # signal/order checks. Pending order status is polled independently so the
    # UI can reflect fills without waiting for the next strategy round.
    check_interval = 60
    try:
        order_status_check_interval = max(
            5.0, float(os.getenv("ORDER_STATUS_CHECK_SECONDS", "10"))
        )
    except (TypeError, ValueError):
        order_status_check_interval = 10.0
    try:
        gateway_check_interval = max(
            1.0, float(os.getenv("HUASHENG_HEARTBEAT_SECONDS", "10"))
        )
    except (TypeError, ValueError):
        gateway_check_interval = 10.0
    logger.info(
        "策略检查间隔: %s秒，订单状态检查间隔: %s秒，网关探活间隔: %s秒\n",
        check_interval,
        order_status_check_interval,
        gateway_check_interval,
    )

    # 用官方 SDK 的 TCP 推送实时更新委托状态；如果 SDK、TCP 端口或订阅
    # 不可用，继续使用下面的 HTTP 轮询，不影响已有的安全兜底。
    trade_push = None
    try:
        from hs_trade_push import TradePushListener

        trade_push = TradePushListener(strategy.handle_trade_push)
        if not trade_push.start():
            logger.warning("交易推送 SDK 启动失败，将继续使用订单状态轮询")
            trade_push.stop()
            trade_push = None
    except Exception as exc:
        logger.warning("交易推送 SDK 不可用，将继续使用订单状态轮询: %s", exc)

    # 主循环
    try:
        next_strategy_check = 0.0
        next_order_status_check = 0.0
        next_gateway_check = 0.0
        gateway_available = False
        while True:
            now = time.monotonic()

            # Keep the engine alive across a short gateway/network outage,
            # but never evaluate or submit a strategy while the read-only
            # gateway probe is failing.
            if now >= next_gateway_check:
                try:
                    gateway_available = strategy.api.check_connection(
                        exchange_type=strategy.exchange_type
                    )
                except Exception as exc:
                    gateway_available = False
                    logger.warning("网关探活异常，暂停交易: %s", exc)
                if not gateway_available:
                    logger.warning("网关不可用，暂停本轮策略和订单状态更新")
                next_gateway_check = now + gateway_check_interval

            # Poll submitted/partial orders separately from strategy checks.
            if gateway_available and now >= next_order_status_check:
                strategy.update_pending_orders()
                next_order_status_check = now + order_status_check_interval

            if now >= next_strategy_check:
                if gateway_available:
                    # Reload strategy and risk configuration so frontend
                    # changes take effect without restarting the engine.
                    strategy.stock_strategies = strategy.load_stock_strategies()
                    strategy.load_risk_config()
                    stocks = list(strategy.stock_strategies.keys())

                    # Fetch one backend-owned snapshot for the whole round.
                    # Every stock then reads its own quote/signal/position
                    # from that snapshot instead of hitting Gateway again.
                    market_snapshot = strategy.get_shared_market_snapshot()
                    if market_snapshot is None:
                        logger.warning("共享行情快照不可用，跳过本轮策略评估")
                    else:
                        for stock in stocks:
                            strategy.execute_strategy(stock, snapshot=market_snapshot)
                else:
                    logger.warning("网关仍不可用，跳过本轮策略评估")

                logger.info("=" * 60)
                next_strategy_check = time.monotonic() + check_interval

            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("\n程序已停止")
    except Exception as e:
        logger.error(f"程序异常: {str(e)}")
        raise
    finally:
        if trade_push is not None:
            trade_push.stop()


if __name__ == "__main__":
    main()
