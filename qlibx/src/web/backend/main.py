from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import json
import os
import csv
import math
import sys
import time
import threading
import subprocess
import re
from typing import Dict, List, Optional
from datetime import datetime
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# Add scripts directory to path to import huasheng_api
SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../scripts"))
sys.path.append(SCRIPTS_DIR)

from huasheng_api import HuashengGatewayAPI
from hs_option_api import HSOptionAPIError, HSOptionMarketData
from hs_trade_history import HSTradeHistory
from market_hours import get_market_status

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STRATEGY_FILE = os.path.join(SCRIPTS_DIR, "stock_strategy.json")
TRADES_DIR = os.path.join(SCRIPTS_DIR, "trades")
TRADING_CONFIG_FILE = os.path.join(SCRIPTS_DIR, "trading_config.json")
TRADING_LOG_FILE = os.path.join(SCRIPTS_DIR, "trading.log")
TRADING_BOT_FILE = os.path.join(SCRIPTS_DIR, "tqqq_trading_bot.py")
MANUAL_ORDER_WORKER_FILE = os.path.join(SCRIPTS_DIR, "manual_order_worker.py")

ACTIVE_ORDER_STATUSES = {
    "Submitted", "Partial", "Pending", "Waiting", "Cancel Pending",
    "Partial Cancel Pending", "Modify Pending", "Review Pending",
    "Pending Confirmation", "Pre-Filled",
}
TERMINAL_ORDER_STATUSES = {
    "filled", "canceled", "cancelled", "partially canceled",
    "partially cancelled", "rejected", "review failed", "failed", "error",
}


def _is_active_local_order(row):
    """Use the engine's conservative rule for dashboard order state."""
    if not str(row.get("order_id") or "").strip():
        return False
    status = " ".join(str(row.get("status") or "").strip().lower().split())
    return status not in TERMINAL_ORDER_STATUSES

DEFAULT_TRADING_CONFIG = {
    "max_total_leverage": 2.0,
    "min_maintenance_margin_ratio": 0.30,
}

# Lock for strategy file read/write to prevent race conditions
_strategy_lock = threading.Lock()
_config_lock = threading.Lock()
_runtime_lock = threading.RLock()

# Initialize API
api = HuashengGatewayAPI()
_option_api = None
_option_api_lock = threading.Lock()
_trade_history_api = None
_trade_history_api_lock = threading.Lock()

try:
    GATEWAY_HEARTBEAT_INTERVAL_SECONDS = max(
        1.0, float(os.getenv("HUASHENG_HEARTBEAT_SECONDS", "10"))
    )
except (TypeError, ValueError):
    GATEWAY_HEARTBEAT_INTERVAL_SECONDS = 10.0

try:
    GATEWAY_DISCONNECT_GRACE_SECONDS = max(
        0.0, float(os.getenv("HUASHENG_DISCONNECT_GRACE_SECONDS", "60"))
    )
except (TypeError, ValueError):
    GATEWAY_DISCONNECT_GRACE_SECONDS = 60.0

# Runtime state is held by the backend process. The bot itself remains a
# separate process so a failed strategy loop cannot take down the API/UI.
_bot_process = None
_bot_mode = None
_bot_started_at = None
_bot_exit_code = None
_manual_order_process = None
_manual_order_request = None
_manual_order_started_at = None
_manual_order_exit_code = None
_gateway_logged_in = False
_gateway_login_at = None
_gateway_last_heartbeat_at = None
_gateway_last_error = None
_gateway_disconnect_started_at = None
_gateway_disconnect_started_monotonic = None
# Once a gateway session has been established, keep probing after a
# transient disconnect.  The state remains disconnected and trading remains
# blocked until a read-only probe succeeds again.
_gateway_probe_enabled = False
_gateway_heartbeat_stop = threading.Event()
_gateway_heartbeat_thread = None

# 贪恐指数缓存 (key: (symbol, lever, area), value: (timestamp, score))
SZDT_CACHE = {}
DEFAULT_SIGNAL_CACHE_TTL_SECONDS = 30 * 60
try:
    SIGNAL_CACHE_TTL_SECONDS = max(
        30.0,
        float(os.getenv(
            "SZDT_SIGNAL_CACHE_SECONDS",
            str(DEFAULT_SIGNAL_CACHE_TTL_SECONDS),
        )),
    )
except (TypeError, ValueError):
    SIGNAL_CACHE_TTL_SECONDS = float(DEFAULT_SIGNAL_CACHE_TTL_SECONDS)
_signal_missing_key_logged = False

# One backend-owned snapshot is shared by the dashboard and the strategy
# subprocess.  The strategy subprocess cannot share Python memory directly,
# so it reads this snapshot through /api/market/snapshot.
try:
    MARKET_SNAPSHOT_TTL_SECONDS = max(
        5.0, float(os.getenv("MARKET_SNAPSHOT_SECONDS", "60"))
    )
except (TypeError, ValueError):
    MARKET_SNAPSHOT_TTL_SECONDS = 60.0
_market_snapshot = None
_market_snapshot_at = 0.0
_market_snapshot_last_attempt_at = 0.0
_market_snapshot_lock = threading.Lock()
try:
    MARKET_SNAPSHOT_RETRY_SECONDS = max(
        30.0, float(os.getenv("MARKET_SNAPSHOT_RETRY_SECONDS", "180"))
    )
except (TypeError, ValueError):
    MARKET_SNAPSHOT_RETRY_SECONDS = 180.0


def _clean_market_symbol(symbol):
    return str(symbol or "").upper().replace("US.", "").replace(".US", "").replace("HK.", "").replace(".HK", "")

def get_cached_signal(symbol: str, lever: str = "3", emo_area: str = "us"):
    """带缓存的贪恐指数获取，失败时返回 None（安全失败）。"""
    global _signal_missing_key_logged
    now = time.time()
    cache_key = (_clean_market_symbol(symbol), str(lever), str(emo_area))
    if cache_key in SZDT_CACHE:
        ts, score = SZDT_CACHE[cache_key]
        if now - ts < SIGNAL_CACHE_TTL_SECONDS:
            return score

    auth_key = get_effective_szdt_auth_key()
    if not auth_key:
        if not _signal_missing_key_logged:
            logger.error("SZDT_AUTH_KEY 未配置，贪恐指数不可用，禁止交易")
            _signal_missing_key_logged = True
        return None

    _signal_missing_key_logged = False
    # 缓存失效，调用真实 API
    score = api.fetch_fear_greed_index(
        symbol,
        lever,
        emo_area,
        auth_key=auth_key,
    )
    if score is not None:
        SZDT_CACHE[cache_key] = (now, score)
        return score

    # Do not use stale data as a trading signal.
    SZDT_CACHE.pop(cache_key, None)
    return None

class StockStrategy(BaseModel):
    name: Optional[str] = ""
    buy_point: float = Field(ge=0)
    sell_point: float = Field(ge=0)
    buy_total: int = Field(gt=0)
    sell_total: int = Field(gt=0)
    buy_limit_price: float = Field(ge=0)
    sell_limit_price: float = Field(ge=0)
    buy_day_interval: int = Field(ge=0)
    sell_day_interval: int = Field(ge=0)
    buy_price_interval: float = Field(ge=0)
    max_position: float = Field(gt=0, le=100)
    fear_greed_buy: float
    fear_greed_sell: float
    lever: str 
    emo_area: str


class RiskConfig(BaseModel):
    max_total_leverage: float = Field(default=2.0, gt=0)
    min_maintenance_margin_ratio: float = Field(default=0.30, gt=0, le=1)
    # Optional file override. The actual secret is never returned by the API.
    szdt_auth_key: Optional[str] = None


class TradingStartRequest(BaseModel):
    mode: str = "dry_run"


class ManualOrderRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    side: str = Field(pattern="^(buy|sell)$")
    quantity: int = Field(gt=0, le=1_000_000)
    limit_price: float = Field(gt=0)
    mode: str = Field(default="dry_run", pattern="^(dry_run|live)$")
    auto_cancel_seconds: int = Field(default=60, ge=10, le=300)
    confirm_live: bool = False


class LoginRequest(BaseModel):
    password: str


OPTION_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.\-^]{0,19}$")


def _normalize_option_symbol(symbol: str):
    normalized = str(symbol or "").strip().upper()
    if not OPTION_SYMBOL_PATTERN.fullmatch(normalized):
        raise HTTPException(status_code=422, detail="invalid option underlying symbol")
    return normalized


def get_hs_option_api():
    """Lazily create the official HS SDK adapter on first option request."""
    global _option_api
    with _option_api_lock:
        if _option_api is None:
            _option_api = HSOptionMarketData()
        return _option_api


def get_hs_trade_history_api():
    """Lazily create the official HS SDK execution-history adapter."""
    global _trade_history_api
    with _trade_history_api_lock:
        if _trade_history_api is None:
            _trade_history_api = HSTradeHistory()
        return _trade_history_api

def load_strategies():
    with _strategy_lock:
        if os.path.exists(STRATEGY_FILE):
            with open(STRATEGY_FILE, 'r', encoding='utf-8') as f:
                try:
                    return json.load(f)
                except Exception:
                    return {}
        return {}

def save_strategies(strategies):
    with _strategy_lock:
        with open(STRATEGY_FILE, 'w', encoding='utf-8') as f:
            json.dump(strategies, f, indent=2, ensure_ascii=False)


def _normalize_auth_key(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _load_raw_trading_config():
    if not os.path.exists(TRADING_CONFIG_FILE):
        return {}
    try:
        with open(TRADING_CONFIG_FILE, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else {}
    except Exception as e:
        logger.error(f"Failed to load trading config file: {e}")
        return {}


def load_trading_config(include_secret=False):
    with _config_lock:
        try:
            raw = _load_raw_trading_config()
            config = RiskConfig(**raw)
            data = config.model_dump(exclude_none=True)
            if not include_secret:
                data.pop("szdt_auth_key", None)
            return data or dict(DEFAULT_TRADING_CONFIG)
        except Exception as e:
            logger.error(f"Failed to load trading risk config: {e}")
            return dict(DEFAULT_TRADING_CONFIG)


def save_trading_config(config: RiskConfig):
    os.makedirs(os.path.dirname(TRADING_CONFIG_FILE), exist_ok=True)
    with _config_lock:
        temp_file = f"{TRADING_CONFIG_FILE}.tmp"
        existing = _load_raw_trading_config()
        payload = {
            "max_total_leverage": config.max_total_leverage,
            "min_maintenance_margin_ratio": config.min_maintenance_margin_ratio,
        }

        # If the frontend omitted the secret field, preserve an existing file
        # override. An explicit null or empty string removes that override and
        # makes the environment variable the fallback again.
        if "szdt_auth_key" in config.model_fields_set:
            auth_key = _normalize_auth_key(config.szdt_auth_key)
        else:
            auth_key = _normalize_auth_key(existing.get("szdt_auth_key"))
        if auth_key:
            payload["szdt_auth_key"] = auth_key

        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_file, TRADING_CONFIG_FILE)
    return payload


def get_effective_szdt_auth_key():
    """File override wins; environment variable is the fallback."""
    with _config_lock:
        file_key = _normalize_auth_key(_load_raw_trading_config().get("szdt_auth_key"))
    if file_key:
        return file_key
    return _normalize_auth_key(os.getenv("SZDT_AUTH_KEY"))


def get_szdt_auth_config_status():
    with _config_lock:
        file_key = _normalize_auth_key(_load_raw_trading_config().get("szdt_auth_key"))
    env_key = _normalize_auth_key(os.getenv("SZDT_AUTH_KEY"))
    if file_key:
        source = "file"
    elif env_key:
        source = "environment"
    else:
        source = "unset"
    return {
        "szdt_auth_key_configured": bool(file_key or env_key),
        "szdt_auth_key_source": source,
    }


def get_trading_runtime_status():
    with _runtime_lock:
        if _bot_process is None:
            state = "stopped"
        else:
            exit_code = _bot_process.poll()
            state = "running" if exit_code is None else "stopped"

        return {
            "state": state,
            "mode": _bot_mode,
            "pid": _bot_process.pid if _bot_process is not None else None,
            "started_at": _bot_started_at,
            "exit_code": (
                _bot_process.poll() if _bot_process is not None else _bot_exit_code
            ),
            "gateway_logged_in": _gateway_logged_in,
            "gateway_login_at": _gateway_login_at,
            "gateway_connection_state": get_gateway_connection_state(),
            "gateway_last_heartbeat_at": _gateway_last_heartbeat_at,
            "gateway_last_error": _gateway_last_error,
            "manual_order": get_manual_order_runtime_status(),
            "config": load_trading_config(),
        }


def get_manual_order_runtime_status():
    with _runtime_lock:
        if _manual_order_process is None:
            state = "idle"
            exit_code = _manual_order_exit_code
        else:
            exit_code = _manual_order_process.poll()
            state = "running" if exit_code is None else "stopped"
        return {
            "state": state,
            "pid": _manual_order_process.pid if _manual_order_process is not None else None,
            "started_at": _manual_order_started_at,
            "exit_code": exit_code,
            "request": _manual_order_request,
        }


def get_gateway_connection_state():
    if _gateway_logged_in:
        return "connected"
    if _gateway_last_error:
        return "disconnected"
    return "not_logged_in"


def get_gateway_status():
    with _runtime_lock:
        return {
            "logged_in": _gateway_logged_in,
            "connection_state": get_gateway_connection_state(),
            "login_at": _gateway_login_at,
            "last_heartbeat_at": _gateway_last_heartbeat_at,
            "last_error": _gateway_last_error,
            "heartbeat_interval_seconds": GATEWAY_HEARTBEAT_INTERVAL_SECONDS,
            "disconnect_grace_seconds": GATEWAY_DISCONNECT_GRACE_SECONDS,
            "disconnect_started_at": _gateway_disconnect_started_at,
            "gateway_url": api.gateway_url,
        }


def _terminate_bot_process_locked():
    """Stop the strategy process while the runtime lock is held."""
    global _bot_exit_code
    if _bot_process is None or _bot_process.poll() is not None:
        return

    _bot_process.terminate()
    try:
        _bot_process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        logger.warning("Trading engine did not stop gracefully; killing it")
        _bot_process.kill()
        _bot_process.wait(timeout=5)
    _bot_exit_code = _bot_process.returncode
    logger.warning("Trading engine stopped because Huasheng gateway became unavailable")


def _terminate_manual_order_process_locked():
    """Stop a one-shot manual order worker while the runtime lock is held."""
    global _manual_order_exit_code
    if _manual_order_process is None or _manual_order_process.poll() is not None:
        return

    _manual_order_process.terminate()
    try:
        _manual_order_process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        logger.warning("Manual order worker did not stop gracefully; killing it")
        _manual_order_process.kill()
        _manual_order_process.wait(timeout=5)
    _manual_order_exit_code = _manual_order_process.returncode
    logger.warning("Manual order worker stopped because Huasheng gateway became unavailable")


def _mark_gateway_connected():
    global _gateway_logged_in, _gateway_last_heartbeat_at, _gateway_last_error
    global _gateway_probe_enabled
    global _gateway_disconnect_started_at, _gateway_disconnect_started_monotonic
    with _runtime_lock:
        _gateway_logged_in = True
        _gateway_probe_enabled = True
        _gateway_last_heartbeat_at = datetime.now().isoformat()
        _gateway_last_error = None
        _gateway_disconnect_started_at = None
        _gateway_disconnect_started_monotonic = None


def _mark_gateway_disconnected(error, keep_probe=True):
    global _gateway_logged_in, _gateway_login_at, _gateway_last_heartbeat_at, _gateway_last_error
    global _gateway_probe_enabled
    global _gateway_disconnect_started_at, _gateway_disconnect_started_monotonic
    with _runtime_lock:
        was_logged_in = _gateway_logged_in
        now = time.monotonic()
        if _gateway_disconnect_started_monotonic is None:
            _gateway_disconnect_started_monotonic = now
            _gateway_disconnect_started_at = datetime.now().isoformat()
        _gateway_logged_in = False
        _gateway_login_at = None
        if not keep_probe:
            _gateway_probe_enabled = False
        _gateway_last_heartbeat_at = datetime.now().isoformat()
        _gateway_last_error = str(error)
        if was_logged_in:
            logger.error(
                "Huasheng gateway heartbeat failed: %s; "
                "waiting %.0f seconds before stopping trading",
                error,
                GATEWAY_DISCONNECT_GRACE_SECONDS,
            )

        disconnected_for = now - _gateway_disconnect_started_monotonic
        if disconnected_for >= GATEWAY_DISCONNECT_GRACE_SECONDS:
            _terminate_bot_process_locked()
            _terminate_manual_order_process_locked()


def _run_gateway_heartbeat_once(allow_logged_out=False):
    with _runtime_lock:
        if not _gateway_logged_in and not allow_logged_out and not _gateway_probe_enabled:
            return False

    try:
        healthy = api.check_connection(exchange_type="P")
    except Exception as e:
        healthy = False
        error = f"只读账户查询异常: {e}"
    else:
        error = getattr(api, "last_error", None) or "只读账户查询失败"

    if healthy:
        _mark_gateway_connected()
        return True

    _mark_gateway_disconnected(error)
    return False


def _gateway_heartbeat_loop():
    while not _gateway_heartbeat_stop.wait(GATEWAY_HEARTBEAT_INTERVAL_SECONDS):
        _run_gateway_heartbeat_once()


@app.on_event("startup")
def start_gateway_heartbeat():
    global _gateway_heartbeat_thread
    if _gateway_heartbeat_thread is not None and _gateway_heartbeat_thread.is_alive():
        return
    _gateway_heartbeat_stop.clear()
    _gateway_heartbeat_thread = threading.Thread(
        target=_gateway_heartbeat_loop,
        name="huasheng-gateway-heartbeat",
        daemon=True,
    )
    _gateway_heartbeat_thread.start()


@app.on_event("shutdown")
def stop_gateway_heartbeat():
    _gateway_heartbeat_stop.set()
    if _gateway_heartbeat_thread is not None:
        _gateway_heartbeat_thread.join(timeout=2)


def read_trading_logs(limit: int = 200):
    limit = max(1, min(limit, 1000))
    if not os.path.exists(TRADING_LOG_FILE):
        return []
    try:
        with open(TRADING_LOG_FILE, 'r', encoding='utf-8', errors='replace') as f:
            return f.readlines()[-limit:]
    except Exception as e:
        logger.error(f"Failed to read trading log: {e}")
        return []

@app.get("/api/strategies")
def get_strategies():
    return load_strategies()


@app.get("/api/auth/status")
def get_auth_status():
    return get_gateway_status()


@app.get("/api/auth/health")
def get_auth_health():
    """Run an immediate read-only gateway probe and return its status."""
    _run_gateway_heartbeat_once(allow_logged_out=True)
    return get_gateway_status()


@app.get("/api/market/status")
def get_market_session_status():
    """Return the US regular-session status used by the trading engine."""
    return get_market_status()


@app.post("/api/auth/login")
def login_gateway(request: LoginRequest):
    if not request.password:
        raise HTTPException(status_code=400, detail="Trading password is required")

    try:
        success = api.log_in(password=request.password)
    except Exception as e:
        _mark_gateway_disconnected(f"登录异常: {e}")
        raise HTTPException(status_code=401, detail="Huasheng login failed")

    if not success or not _run_gateway_heartbeat_once(allow_logged_out=True):
        _mark_gateway_disconnected(
            getattr(api, "last_error", None) or "登录或只读账户查询失败",
        )
        raise HTTPException(status_code=401, detail="Huasheng login or health check failed")

    global _gateway_login_at
    _gateway_login_at = datetime.now().isoformat()
    invalidate_market_snapshot()
    return {"status": "success", **get_gateway_status()}


@app.get("/api/trading/config")
def get_trading_config():
    config = load_trading_config()
    config.update(get_szdt_auth_config_status())
    return config


@app.put("/api/trading/config")
def update_trading_config(config: RiskConfig):
    global _signal_missing_key_logged
    saved = save_trading_config(config)
    SZDT_CACHE.clear()
    invalidate_market_snapshot()
    _signal_missing_key_logged = False
    logger.info(
        "Trading risk config updated: max leverage %.2fx, minimum margin ratio %.2f%%",
        saved["max_total_leverage"],
        saved["min_maintenance_margin_ratio"] * 100,
    )
    response_config = dict(saved)
    response_config.pop("szdt_auth_key", None)
    response_config.update(get_szdt_auth_config_status())
    return {"status": "success", "config": response_config}


def _position_quantity(position):
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


def _build_market_snapshot():
    """Fetch one consistent read snapshot for UI and strategy consumers."""
    strategies = load_strategies()
    symbols = list(strategies.keys())
    positions_ok = False
    funds_ok = False
    quotes_ok = False

    try:
        holdings_res = api.get_position(exchange_type="P")
        positions = holdings_res.get("positionList", []) if holdings_res else []
        positions_ok = isinstance(holdings_res, dict) and "positionList" in holdings_res
        positions = [dict(position) for position in positions if isinstance(position, dict)]
    except Exception as exc:
        logger.error("共享快照获取持仓失败: %s", exc)
        positions = []

    try:
        funds = api.get_account_funds(exchange_type="P")
        funds_ok = isinstance(funds, dict)
        if not funds_ok:
            funds = {}
    except Exception as exc:
        logger.error("共享快照获取账户资金失败: %s", exc)
        funds = {}

    try:
        quotes = api.get_realtime_quotes(symbols, data_type=20002)
        quotes_ok = isinstance(quotes, dict) and all(
            _clean_market_symbol(symbol) in quotes for symbol in symbols
        )
    except Exception as exc:
        logger.error("共享快照批量获取行情失败: %s", exc)
        quotes = {}

    trade_history = {}
    trade_history_available = False
    trade_history_complete = False
    trade_history_ok = not _gateway_logged_in
    active_orders = []
    active_orders_available = False
    active_orders_ok = not _gateway_logged_in
    if _gateway_logged_in:
        try:
            history_api = get_hs_trade_history_api()
            active_orders = history_api.get_active_orders(exchange_type="P")
            active_orders_available = True
            active_orders_ok = True
        except Exception as exc:
            # Missing current entrust data is unsafe: an accepted order whose
            # local write failed must still block the next strategy round.
            logger.error("共享快照查询 HS 当日委托失败: %s", exc)
        try:
            history_api = get_hs_trade_history_api()
            trade_history = history_api.get_latest_by_symbol(exchange_type="P")
            trade_history_available = True
            trade_history_complete = history_api.history_complete
            trade_history_ok = True
        except Exception as exc:
            # A strategy must not interpret an HS history failure as "no prior
            # trade". Mark the shared snapshot incomplete so the engine skips
            # this round instead of placing an interval-violating order.
            logger.error("共享快照查询 HS 成交历史失败: %s", exc)

    position_map = {
        _clean_market_symbol(position.get("stockCode")): position
        for position in positions
        if _clean_market_symbol(position.get("stockCode"))
    }
    total_market_value = 0.0
    total_pnl = 0.0
    for position in positions:
        try:
            total_market_value += abs(float(position.get("marketValue", 0)))
            total_pnl += float(position.get("incomeBalance", 0))
        except (TypeError, ValueError):
            continue
    try:
        total_asset = float(funds.get("assetBalance", 0))
        cash = float(funds.get("enableBalance", 0))
        buying_power = float(funds.get("buyPower", 0))
    except (TypeError, ValueError):
        total_asset = cash = buying_power = 0.0

    # Keep every UI position percentage on the same basis as the strategy's
    # max_position check: position market value / account equity (assetBalance).
    for position in positions:
        try:
            position["weight"] = (
                float(position.get("marketValue", 0)) / total_asset * 100
                if total_asset > 0 else 0
            )
        except (TypeError, ValueError):
            position["weight"] = 0

    risk_config = load_trading_config()
    gross_leverage = total_market_value / total_asset if total_asset > 0 else 0.0
    margin_ratio = total_asset / total_market_value if total_market_value > 0 else None
    if total_asset <= 0 or not funds_ok or not positions_ok:
        risk_status = "unavailable"
    elif gross_leverage > risk_config["max_total_leverage"]:
        risk_status = "leverage_exceeded"
    elif margin_ratio is not None and margin_ratio < risk_config["min_maintenance_margin_ratio"]:
        risk_status = "margin_ratio_low"
    else:
        risk_status = "safe"

    realtime = {}
    for raw_symbol in symbols:
        clean_symbol = _clean_market_symbol(raw_symbol)
        quote = quotes.get(clean_symbol) or {}
        try:
            pre_after = float(quote.get("preAfterPrice") or 0.0)
            regular_price = float(quote.get("lastPrice") or 0.0)
            last_price = pre_after if pre_after > 0 else regular_price
        except (TypeError, ValueError):
            last_price = 0.0
            regular_price = 0.0

        position = position_map.get(clean_symbol, {})
        try:
            stock_value = float(position.get("marketValue", 0))
        except (TypeError, ValueError):
            stock_value = 0.0
        strategy = strategies[raw_symbol]
        signal = get_cached_signal(
            clean_symbol,
            strategy.get("lever", "1"),
            strategy.get("emo_area", "us"),
        )
        realtime[raw_symbol] = {
            "lastPrice": last_price,
            "regularLastPrice": regular_price,
            "volume": quote.get("volume", 0),
            "quantity": _position_quantity(position),
            "value": stock_value,
            "weight": (stock_value / total_asset * 100) if total_asset > 0 else 0,
            "signal": signal,
            "signal_available": signal is not None,
        }

    return {
        "holdings": positions,
        "account": {
            "total_asset": total_asset,
            "market_value": total_market_value,
            "cash": cash,
            "buying_power": buying_power,
            "total_pnl": total_pnl,
            "gross_leverage": gross_leverage,
            "maintenance_margin_ratio": margin_ratio,
            "max_total_leverage": risk_config["max_total_leverage"],
            "min_maintenance_margin_ratio": risk_config["min_maintenance_margin_ratio"],
            "risk_status": risk_status,
            "gateway_logged_in": _gateway_logged_in,
            "currency": "USD",
        },
        "realtime": realtime,
        "trade_history": trade_history,
        "trade_history_available": trade_history_available,
        "trade_history_complete": trade_history_complete,
        "active_orders": active_orders,
        "active_orders_available": active_orders_available,
        "complete": (
            positions_ok and funds_ok and quotes_ok
            and trade_history_ok and active_orders_ok
        ),
    }


def _empty_account_summary():
    """Return the stable account response shape while Gateway data is unavailable."""
    risk_config = load_trading_config()
    return {
        "total_asset": 0.0,
        "market_value": 0.0,
        "cash": 0.0,
        "buying_power": 0.0,
        "total_pnl": 0.0,
        "gross_leverage": 0.0,
        "maintenance_margin_ratio": None,
        "max_total_leverage": risk_config["max_total_leverage"],
        "min_maintenance_margin_ratio": risk_config["min_maintenance_margin_ratio"],
        "risk_status": "unavailable",
        "gateway_logged_in": _gateway_logged_in,
        "currency": "USD",
    }


def refresh_market_snapshot(force=False):
    """Return the shared snapshot, with a cooldown after failed refreshes."""
    global _market_snapshot, _market_snapshot_at, _market_snapshot_last_attempt_at
    now = time.time()
    with _market_snapshot_lock:
        if (
            not force
            and _market_snapshot is not None
            and now - _market_snapshot_at < MARKET_SNAPSHOT_TTL_SECONDS
        ):
            return _market_snapshot
        if (
            _market_snapshot_last_attempt_at
            and now - _market_snapshot_last_attempt_at < MARKET_SNAPSHOT_RETRY_SECONDS
        ):
            # A forced strategy refresh must also respect the failure cooldown.
            # Return the previous data as explicitly incomplete so consumers
            # cannot trade on it while the Gateway is recovering.
            if _market_snapshot is not None:
                return {**_market_snapshot, "complete": False}
            return {
                "holdings": [],
                "account": _empty_account_summary(),
                "realtime": {},
                "complete": False,
            }

        _market_snapshot_last_attempt_at = now
        snapshot = _build_market_snapshot()
        if snapshot.get("complete"):
            _market_snapshot = snapshot
            _market_snapshot_at = now
            # This marker represents a failed refresh cooldown, not the last
            # successful request. Clear it after a successful refresh so
            # force=True remains useful for the next strategy round.
            _market_snapshot_last_attempt_at = 0.0
        elif _market_snapshot is not None and not force:
            logger.warning("共享快照不完整，继续使用上一份成功快照")
            return _market_snapshot
        return snapshot


def invalidate_market_snapshot():
    """Mark the shared read snapshot for refresh on the next consumer call."""
    global _market_snapshot_at, _market_snapshot_last_attempt_at
    _market_snapshot_at = 0.0
    _market_snapshot_last_attempt_at = 0.0


def get_market_snapshot_response(force=False):
    snapshot = refresh_market_snapshot(force=force)
    age = None
    if _market_snapshot is not None:
        age = max(0.0, time.time() - _market_snapshot_at)
    return {
        **snapshot,
        "updated_at": (
            datetime.fromtimestamp(_market_snapshot_at).isoformat()
            if _market_snapshot is not None else None
        ),
        "age_seconds": age,
        "stale": (
            not snapshot.get("complete")
            or _market_snapshot is None
            or age is None
            or age > MARKET_SNAPSHOT_TTL_SECONDS
        ),
        "snapshot_ttl_seconds": MARKET_SNAPSHOT_TTL_SECONDS,
        "signal_ttl_seconds": SIGNAL_CACHE_TTL_SECONDS,
    }


def probe_signal_service():
    """Validate every configured strategy's signal before starting the bot."""
    auth_key = get_effective_szdt_auth_key()
    if not auth_key:
        return False, "SZDT_AUTH_KEY 未配置"

    strategies = load_strategies()
    if not strategies:
        return True, "没有配置股票策略"

    failures = []
    for symbol, strategy in strategies.items():
        try:
            score = api.fetch_fear_greed_index(
                symbol,
                strategy.get("lever", "1"),
                strategy.get("emo_area", "us"),
                auth_key=auth_key,
            )
        except Exception as e:
            logger.error("贪恐指数预检异常 %s: %s", symbol, e)
            score = None
        if score is None:
            failures.append(symbol)

    if failures:
        return False, f"无法获取股票信号: {', '.join(failures)}"
    return True, None


@app.get("/api/trading/status")
def get_trading_status():
    return get_trading_runtime_status()


@app.get("/api/trading/logs")
def get_trading_logs(limit: int = 200):
    return {"lines": read_trading_logs(limit)}


@app.post("/api/trading/start")
def start_trading(request: TradingStartRequest):
    global _bot_process, _bot_mode, _bot_started_at, _bot_exit_code

    mode = request.mode.strip().lower()
    if mode not in {"dry_run", "live"}:
        raise HTTPException(status_code=400, detail="mode must be dry_run or live")
    if not os.path.exists(TRADING_BOT_FILE):
        raise HTTPException(status_code=500, detail="Trading bot file not found")

    with _runtime_lock:
        if not _gateway_logged_in:
            raise HTTPException(status_code=401, detail="Huasheng gateway is not connected")
        if _bot_process is not None and _bot_process.poll() is None:
            raise HTTPException(status_code=409, detail="Trading engine is already running")

    signal_ok, signal_error = probe_signal_service()
    if not signal_ok:
        raise HTTPException(
            status_code=503,
            detail=f"贪恐指数服务不可用，禁止启动交易: {signal_error}",
        )

    # Re-check both guards after the external preflight, since the gateway
    # heartbeat may change state while the signal service is being queried.
    with _runtime_lock:
        if not _gateway_logged_in:
            raise HTTPException(status_code=401, detail="Huasheng gateway is not connected")
        if _bot_process is not None and _bot_process.poll() is None:
            raise HTTPException(status_code=409, detail="Trading engine is already running")

        command = [sys.executable, TRADING_BOT_FILE]
        if mode == "live":
            command.append("--live")

        try:
            _bot_process = subprocess.Popen(
                command,
                cwd=SCRIPTS_DIR,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            logger.error(f"Failed to start trading engine: {e}")
            raise HTTPException(status_code=500, detail=str(e))

        _bot_mode = mode
        _bot_started_at = datetime.now().isoformat()
        _bot_exit_code = None
        logger.info("Trading engine started in %s mode (pid=%s)", mode, _bot_process.pid)
        return get_trading_runtime_status()


@app.post("/api/trading/stop")
def stop_trading():
    with _runtime_lock:
        if _bot_process is None or _bot_process.poll() is not None:
            return get_trading_runtime_status()
        _terminate_bot_process_locked()
        logger.info("Trading engine stopped (exit_code=%s)", _bot_exit_code)
        return get_trading_runtime_status()

@app.get("/api/stock_info/{symbol}")
def get_stock_info(symbol: str):
    """根据代码获取股票名称"""
    try:
        name = api.get_stock_name(symbol.upper())
        return {"symbol": symbol.upper(), "name": name}
    except Exception as e:
        logger.error(f"Error fetching stock info for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch stock info: {e}")


@app.get("/api/options/chain/{symbol}")
def get_option_chain(symbol: str, expiration: Optional[str] = None):
    """Return an HS SDK option chain for read-only payoff analysis."""
    symbol = _normalize_option_symbol(symbol)
    if expiration is not None and not re.fullmatch(r"\d{4}/\d{2}/\d{2}", expiration):
        raise HTTPException(status_code=422, detail="expiration must use yyyy/MM/dd")
    try:
        return get_hs_option_api().get_chain(symbol, expiration)
    except HTTPException:
        raise
    except HSOptionAPIError as exc:
        logger.warning("HS option chain request failed for %s: %s", symbol, exc)
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        logger.exception("Unexpected HS option chain error for %s", symbol)
        raise HTTPException(status_code=500, detail=f"期权链查询失败: {exc}")

@app.post("/api/strategies/{symbol}")
def update_strategy(symbol: str, strategy: StockStrategy):
    try:
        symbol = symbol.strip().upper()
        if not symbol:
            raise HTTPException(status_code=400, detail="Symbol cannot be empty")
        
        # 1. 立即刷新股票名字
        try:
            official_name = api.get_stock_name(symbol)
            if official_name and official_name != symbol:
                strategy.name = official_name
        except Exception as e:
            logger.warning(f"Failed to fetch name for {symbol}: {e}")

        # 2. 保存策略
        strategies = load_strategies()
        strategies[symbol] = strategy.model_dump()
        save_strategies(strategies)
        
        # 3. 立即拉取一次实时行情和信号 (强制从 API 拉取，同步更新缓存)
        current_price = 0.0
        try:
            quote = api.get_realtime_quote(symbol)
            if quote:
                pa_val = quote.get("preAfterPrice")
                lp_val = quote.get("lastPrice")
                pre_after = float(pa_val) if pa_val else 0.0
                last_p = float(lp_val) if lp_val else 0.0
                current_price = pre_after if pre_after > 0 else last_p
        except Exception: pass
        
        # 强制 API 拉取
        score = api.fetch_fear_greed_index(
            symbol,
            strategy.lever,
            strategy.emo_area,
            auth_key=get_effective_szdt_auth_key(),
        )
        current_signal = score

        # 同步更新缓存；失败时不写入伪造的 0 信号。
        cache_key = (
            _clean_market_symbol(symbol),
            str(strategy.lever),
            str(strategy.emo_area),
        )
        if current_signal is not None:
            SZDT_CACHE[cache_key] = (time.time(), current_signal)
        else:
            SZDT_CACHE.pop(cache_key, None)

        invalidate_market_snapshot()

        logger.info(f"Strategy for {symbol} saved and force-refreshed. Price: {current_price}, Signal: {current_signal}")
        
        return {
            "status": "success", 
            "strategy": strategies[symbol],
            "realtime": {
                "lastPrice": current_price,
                "signal": current_signal,
                "signal_available": current_signal is not None,
            }
        }
    except Exception as e:
        logger.error(f"Error saving strategy {symbol}: {str(e)}")
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/strategies/{symbol}")
def delete_strategy(symbol: str):
    symbol = symbol.strip().upper()
    strategies = load_strategies()
    if symbol in strategies:
        del strategies[symbol]
        save_strategies(strategies)
        return {"status": "success", "message": f"Strategy for {symbol} deleted"}
    raise HTTPException(status_code=404, detail="Stock strategy not found")

@app.get("/api/realtime")
def get_realtime_data():
    return get_market_snapshot_response()["realtime"]


@app.get("/api/market/snapshot")
def get_market_snapshot(force: bool = False):
    """Return the shared holdings/account/quote/signal snapshot."""
    return get_market_snapshot_response(force=force)

@app.get("/api/holdings")
def get_full_holdings():
    """返回账户中所有的真实持仓列表，包含占比"""
    return get_market_snapshot_response()["holdings"]

@app.get("/api/indicator")
def get_indicator():
    """获取第一个股票的贪恐得分 (严格按配置同步)"""
    strategies = load_strategies()
    if not strategies:
        return {"value": 0, "name": "No Data"}
    first_symbol = list(strategies.keys())[0]
    strat = strategies[first_symbol]
    score = get_cached_signal(first_symbol, strat.get("lever", "1"), strat.get("emo_area", "us"))
    return {
        "value": score if score is not None else 0,
        "available": score is not None,
        "name": f"Fear & Greed ({first_symbol})",
    }

@app.get("/api/account")
def get_account_status():
    """获取账户综合资产状态"""
    return get_market_snapshot_response()["account"]

@app.get("/api/history/{symbol}")
def get_history(symbol: str):
    symbol = symbol.strip()
    history_file = os.path.join(TRADES_DIR, f"{symbol.lower()}_trading.csv")
    if not os.path.exists(history_file):
        return []
    history = []
    try:
        with open(history_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                history.append(row)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return history

@app.get("/api/all_history")
def get_all_history():
    all_history = []
    if not os.path.exists(TRADES_DIR):
        return []
    for filename in os.listdir(TRADES_DIR):
        if filename.endswith("_trading.csv"):
            symbol = filename.replace("_trading.csv", "").upper()
            filepath = os.path.join(TRADES_DIR, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        row['symbol'] = symbol
                        all_history.append(row)
            except Exception:
                continue
    all_history.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    return all_history


@app.get("/api/orders/manual/status")
def get_manual_order_status():
    return get_manual_order_runtime_status()


@app.post("/api/orders/manual")
def submit_manual_order(request: ManualOrderRequest):
    """Queue one controlled order without starting the strategy loop."""
    global _manual_order_process, _manual_order_request
    global _manual_order_started_at, _manual_order_exit_code

    symbol = request.symbol.strip().upper()
    if not symbol or any(
        not (char.isascii() and (char.isalnum() or char in ".-_"))
        for char in symbol
    ):
        raise HTTPException(status_code=422, detail="symbol contains invalid characters")
    if not math.isfinite(request.limit_price):
        raise HTTPException(status_code=422, detail="limit_price must be finite")
    if request.mode == "live" and not request.confirm_live:
        raise HTTPException(status_code=400, detail="live manual orders require explicit confirmation")
    if not os.path.exists(MANUAL_ORDER_WORKER_FILE):
        raise HTTPException(status_code=500, detail="Manual order worker file not found")
    with _runtime_lock:
        if request.mode == "live" and not _gateway_logged_in:
            raise HTTPException(status_code=401, detail="Huasheng gateway is not connected")
        if _bot_process is not None and _bot_process.poll() is None:
            raise HTTPException(
                status_code=409,
                detail="请先停止自动交易引擎，再提交手动订单",
            )
        if _manual_order_process is not None and _manual_order_process.poll() is None:
            raise HTTPException(status_code=409, detail="已有手动订单正在处理")

        command = [
            sys.executable,
            MANUAL_ORDER_WORKER_FILE,
            "--mode", request.mode,
            "--symbol", symbol,
            "--side", request.side,
            "--quantity", str(request.quantity),
            "--limit-price", str(request.limit_price),
            "--auto-cancel-seconds", str(request.auto_cancel_seconds),
        ]
        try:
            _manual_order_process = subprocess.Popen(
                command,
                cwd=SCRIPTS_DIR,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            logger.error("Failed to start manual order worker: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))

        _manual_order_request = {
            "symbol": symbol,
            "side": request.side,
            "quantity": request.quantity,
            "limit_price": request.limit_price,
            "mode": request.mode,
            "auto_cancel_seconds": request.auto_cancel_seconds,
        }
        _manual_order_started_at = datetime.now().isoformat()
        _manual_order_exit_code = None
        logger.warning(
            "Manual order worker started: %s %s %s shares at %s (mode=%s, pid=%s)",
            request.side,
            symbol,
            request.quantity,
            request.limit_price,
            request.mode,
            _manual_order_process.pid,
        )
        return {
            "status": "accepted",
            "manual_order": get_manual_order_runtime_status(),
        }


@app.get("/api/orders/realtime")
def get_realtime_orders(limit: int = 100):
    """Return recent local orders with their latest SDK/polling status.

    The engine writes the order as soon as it submits it. The SDK then updates
    the same row with fill/remaining quantities and prices, so this endpoint
    stays useful even when the gateway is temporarily unavailable.
    """
    limit = max(1, min(limit, 500))
    orders = []
    if os.path.exists(TRADES_DIR):
        for filename in os.listdir(TRADES_DIR):
            if not filename.endswith("_trading.csv"):
                continue
            symbol = filename.replace("_trading.csv", "").upper()
            filepath = os.path.join(TRADES_DIR, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        row["symbol"] = row.get("symbol") or symbol
                        row["is_active"] = _is_active_local_order(row)
                        row["last_updated"] = (
                            row.get("status_updated_at")
                            or row.get("timestamp")
                        )
                        orders.append(row)
            except Exception as exc:
                logger.warning("Failed to read realtime orders from %s: %s", filename, exc)

    orders.sort(key=lambda row: row.get("last_updated", ""), reverse=True)
    return {
        "orders": orders[:limit],
        "active_count": sum(1 for row in orders if row.get("is_active")),
        "updated_at": datetime.now().isoformat(),
    }

if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("QuantTrade Backend Starting...")
    print("=" * 60)
    print("Huasheng login and trading controls are available from the dashboard.")
    uvicorn.run(app, host="0.0.0.0", port=8000)
