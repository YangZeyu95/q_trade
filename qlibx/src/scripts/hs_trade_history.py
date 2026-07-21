"""Read executed stock trades through the official :mod:`hs` SDK.

The strategy only needs the latest completed buy and latest completed sell for
each symbol.  Buy and sell records are deliberately stored in separate slots
so a sell can never reset a buy interval (or vice versa).
"""

import logging
import math
import os
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

try:
    import hs  # noqa: F401 - explicit official SDK dependency check
    from hs.api.hs_open_api import OpenAPI

    SDK_AVAILABLE = True
    SDK_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - depends on local installation
    OpenAPI = None
    SDK_AVAILABLE = False
    SDK_IMPORT_ERROR = exc


logger = logging.getLogger(__name__)


# Huasheng's current-entrust status codes. Anything not explicitly terminal is
# treated as active so a newly introduced gateway status cannot permit a
# duplicate strategy order.
TERMINAL_ENTRUST_STATUS_CODES = {"5", "6", "8", "9", "F", "G", "J"}
TERMINAL_ENTRUST_STATUS_TEXT = {
    "filled", "canceled", "cancelled", "rejected", "review failed",
    "partially canceled", "partially cancelled", "failed", "error",
    "已成交", "已撤销", "已撤单", "已取消", "已拒绝", "废单", "审核失败",
    "部成部撤", "部分撤销", "部分撤单",
}


class HSTradeHistoryError(RuntimeError):
    """Raised when HS cannot provide an authoritative execution history."""


class HSTradeHistory:
    """Fetch and normalize latest buy/sell fills from HS Gateway."""

    def __init__(self, ip=None, http_port=None, tcp_port=None, open_api=None):
        if open_api is None and not SDK_AVAILABLE:
            raise HSTradeHistoryError(f"华盛 HS SDK 不可用: {SDK_IMPORT_ERROR}")
        self.ip = ip or os.getenv("HUASHENG_GATEWAY_IP", "127.0.0.1")
        try:
            self.http_port = int(http_port or os.getenv("HUASHENG_HTTP_PORT", "11111"))
            self.tcp_port = int(tcp_port or os.getenv("HUASHENG_TCP_PORT", "11112"))
            self.lookback_days = max(1, int(os.getenv("HS_TRADE_HISTORY_DAYS", "365")))
            self.window_days = max(1, int(os.getenv("HS_TRADE_HISTORY_WINDOW_DAYS", "30")))
            self.windows_per_refresh = max(
                1, int(os.getenv("HS_TRADE_HISTORY_WINDOWS_PER_REFRESH", "3"))
            )
        except (TypeError, ValueError) as exc:
            raise HSTradeHistoryError("华盛 Gateway 端口和成交历史配置必须是整数") from exc

        self._open_api = open_api or OpenAPI(
            ip=self.ip,
            http_port=self.http_port,
            tcp_port=self.tcp_port,
            logging_filename=os.getenv("HUASHENG_TRADE_HISTORY_SDK_LOG", os.devnull),
        )
        self._lock = threading.RLock()
        self._et_tz = ZoneInfo("America/New_York")
        self._history_cache_date = None
        self._historical_latest = {}
        self._history_start_limit = None
        self._next_history_window_end = None
        self.history_complete = False

    @staticmethod
    def _records(response, operation):
        if not isinstance(response, dict) or not response.get("ok"):
            detail = response.get("err") if isinstance(response, dict) else response
            raise HSTradeHistoryError(f"{operation}失败: {detail or 'HS Gateway 无响应'}")
        data = response.get("data")
        if isinstance(data, list):
            return [record for record in data if isinstance(record, dict)]
        if isinstance(data, dict):
            records = data.get("data") or data.get("deliverList") or []
            return [record for record in records if isinstance(record, dict)]
        return []

    def _query_pages(self, method, operation, **kwargs):
        records = []
        cursor = "0"
        # 99 is below the SDK's documented per-page limit of 100.  The hard
        # page cap prevents a malformed cursor from blocking a snapshot round.
        for _ in range(20):
            response = method(query_count=99, query_param_str=cursor, **kwargs)
            page = self._records(response, operation)
            records.extend(page)
            if len(page) < 99:
                return records
            next_cursor = str(page[-1].get("queryParamStr") or "").strip()
            if not next_cursor or next_cursor == cursor:
                raise HSTradeHistoryError(
                    f"{operation}分页游标缺失或未前进，无法确认结果完整"
                )
            cursor = next_cursor
        raise HSTradeHistoryError(f"{operation}超过最大分页数，无法确认结果完整")

    @staticmethod
    def _positive_float(value):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and number > 0 else None

    @staticmethod
    def _nonnegative_float(value):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and number >= 0 else None

    @staticmethod
    def _clean_symbol(value):
        return (
            str(value or "").strip().upper()
            .replace("US.", "").replace(".US", "")
            .replace("HK.", "").replace(".HK", "")
        )

    @staticmethod
    def _action(value):
        normalized = str(value or "").strip().lower()
        if normalized in {"1", "buy", "b", "买入"}:
            return "buy"
        if normalized in {"2", "sell", "s", "卖出"}:
            return "sell"
        return None

    @staticmethod
    def _is_active_entrust(record):
        raw_status = str(
            record.get("status") or record.get("entrustStatus") or ""
        ).strip().upper()
        status_text = " ".join(
            str(record.get("statusDesc") or record.get("orderStatusName") or "")
            .strip().lower().split()
        )
        if raw_status in TERMINAL_ENTRUST_STATUS_CODES:
            return False
        if status_text in TERMINAL_ENTRUST_STATUS_TEXT:
            return False
        return True

    @classmethod
    def _normalize_active_order(cls, record):
        if not cls._is_active_entrust(record):
            return None

        symbol = cls._clean_symbol(record.get("stockCode") or record.get("securityCode"))
        order_id = str(
            record.get("entrustId") or record.get("entrustNo")
            or record.get("orderId") or record.get("recordNo") or ""
        ).strip()
        action = cls._action(record.get("entrustBs") or record.get("businessBs"))
        if not symbol or not order_id or action is None:
            raise HSTradeHistoryError(
                f"HS 当日委托存在无法识别的在途记录: {record}"
            )

        total_quantity = cls._nonnegative_float(record.get("entrustAmount"))
        filled_quantity = None
        for key in ("sumBusinessAmount", "businessAmount"):
            value = record.get(key)
            if value is not None and str(value).strip() != "":
                filled_quantity = cls._nonnegative_float(value)
                if filled_quantity is None:
                    raise HSTradeHistoryError(
                        f"HS 当日委托存在无法计算敞口的成交数量: {record}"
                    )
                break
        if filled_quantity is None:
            filled_quantity = 0.0
        remaining_quantity = None
        for key in ("unBusinessAmount", "leftAmount", "remainingAmount"):
            value = record.get(key)
            if value is not None and str(value).strip() != "":
                remaining_quantity = cls._nonnegative_float(value)
                if remaining_quantity is None:
                    raise HSTradeHistoryError(
                        f"HS 当日委托存在无法计算敞口的剩余数量: {record}"
                    )
                break
        if remaining_quantity is None and total_quantity is not None:
            remaining_quantity = max(0.0, total_quantity - filled_quantity)
        if remaining_quantity is None:
            raise HSTradeHistoryError(
                f"HS 当日委托存在无法计算敞口的委托数量: {record}"
            )

        price = cls._nonnegative_float(record.get("entrustPrice"))
        if action == "buy" and remaining_quantity > 0 and not price:
            raise HSTradeHistoryError(
                f"HS 当日买入委托存在无法计算敞口的委托价格: {record}"
            )
        raw_status = str(
            record.get("status") or record.get("entrustStatus") or ""
        ).strip()
        status_desc = str(
            record.get("statusDesc") or record.get("orderStatusName") or raw_status
        ).strip()
        return {
            "order_id": order_id,
            "symbol": symbol,
            "action": action,
            "status": status_desc,
            "status_code": raw_status,
            "quantity": remaining_quantity,
            "entrust_quantity": total_quantity or 0.0,
            "filled_quantity": filled_quantity,
            "price": price or 0.0,
            "notional": remaining_quantity * (price or 0.0),
            "date": str(record.get("date") or "").strip(),
            "time": str(record.get("entrustTime") or "").strip(),
            "source": "HS SDK current entrust",
        }

    @classmethod
    def _normalize(cls, record):
        symbol = cls._clean_symbol(record.get("stockCode") or record.get("securityCode"))
        action = cls._action(record.get("entrustBs") or record.get("businessBs"))
        price = cls._positive_float(record.get("businessPrice"))
        quantity = cls._positive_float(record.get("businessAmount"))
        trade_date = str(record.get("date") or record.get("businessDate") or "").strip()
        if not symbol or action is None or price is None or quantity is None:
            return None
        try:
            parsed_date = datetime.strptime(trade_date.replace("/", "-")[:10], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return None

        raw_time = str(record.get("businessTime") or "").strip()
        try:
            parsed_time = datetime.strptime(raw_time, "%H:%M:%S").time()
            sort_key = (parsed_date.toordinal(), parsed_time.hour * 3600 + parsed_time.minute * 60 + parsed_time.second)
        except ValueError:
            raw_time = ""
            sort_key = (parsed_date.toordinal(), 0)

        return {
            "symbol": symbol,
            "action": action,
            "date": parsed_date.isoformat(),
            "time": raw_time,
            "price": price,
            "quantity": quantity,
            "amount": cls._positive_float(record.get("businessBalance")) or price * quantity,
            "order_id": str(record.get("entrustId") or record.get("recordNo") or "").strip(),
            "source": "HS SDK",
            "_sort_key": sort_key,
        }

    @classmethod
    def _merge_latest(cls, latest, raw_records):
        # HS returns newest records first.  Keep the first record when the
        # historical API has no intraday time and two fills share a date.
        for raw_record in raw_records:
            record = cls._normalize(raw_record)
            if record is None:
                continue
            side_records = latest.setdefault(record["symbol"], {})
            existing = side_records.get(record["action"])
            if existing is None or record["_sort_key"] > existing["_sort_key"]:
                side_records[record["action"]] = record
        return latest

    @staticmethod
    def _public_records(latest):
        result = {}
        for symbol, side_records in latest.items():
            result[symbol] = {}
            for action, record in side_records.items():
                result[symbol][action] = {
                    key: value for key, value in record.items() if key != "_sort_key"
                }
        return result

    def _reset_historical_cache(self, trading_date):
        self._history_cache_date = trading_date
        self._historical_latest = {}
        self._history_start_limit = trading_date - timedelta(days=self.lookback_days)
        self._next_history_window_end = trading_date - timedelta(days=1)
        self.history_complete = False

    def _refresh_next_history_window(self, exchange_type):
        if self.history_complete:
            return
        if self._next_history_window_end < self._history_start_limit:
            self.history_complete = True
            return
        window_start = max(
            self._history_start_limit,
            self._next_history_window_end - timedelta(days=self.window_days - 1),
        )
        page_records = self._query_pages(
            self._open_api.query_history_deliver_list,
            "查询历史成交",
            exchange_type=exchange_type,
            start_date=window_start.strftime("%Y%m%d"),
            end_date=self._next_history_window_end.strftime("%Y%m%d"),
        )
        self._merge_latest(self._historical_latest, page_records)
        self._next_history_window_end = window_start - timedelta(days=1)
        self.history_complete = self._next_history_window_end < self._history_start_limit

    def get_latest_by_symbol(self, exchange_type="P", now=None):
        """Return ``{symbol: {buy: ..., sell: ...}}`` from actual fills.

        Today's executions are refreshed every call. One older date window is
        added per call, newest first, until the configured lookback is covered.
        This keeps every 60-second snapshot bounded while still converging on
        a full historical cache after startup.
        """
        current = now or datetime.now(self._et_tz)
        if current.tzinfo is None:
            current = current.replace(tzinfo=self._et_tz)
        trading_date = current.astimezone(self._et_tz).date()

        with self._lock:
            today_records = self._query_pages(
                self._open_api.query_real_deliver_list,
                "查询当日成交",
                exchange_type=exchange_type,
            )
            if self._history_cache_date != trading_date:
                self._reset_historical_cache(trading_date)
            for _ in range(self.windows_per_refresh):
                self._refresh_next_history_window(exchange_type)
                if self.history_complete:
                    break
            latest = {
                symbol: {action: dict(record) for action, record in sides.items()}
                for symbol, sides in self._historical_latest.items()
            }
            self._merge_latest(latest, today_records)

        return self._public_records(latest)

    def get_active_orders(self, exchange_type="P"):
        """Return every non-terminal order from HS's current entrust list.

        This is intentionally a fresh query on every shared snapshot. Unknown
        statuses remain active, while malformed active records fail the whole
        query so the strategy cannot mistake incomplete data for no orders.
        """
        with self._lock:
            raw_records = self._query_pages(
                self._open_api.query_real_entrust_list,
                "查询当日委托",
                exchange_type=exchange_type,
            )
            active_orders = []
            for raw_record in raw_records:
                order = self._normalize_active_order(raw_record)
                if order is not None:
                    active_orders.append(order)
        return active_orders
