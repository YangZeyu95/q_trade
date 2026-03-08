from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import json
import os
import csv
import sys
import time
import threading
from typing import Dict, List, Optional
from datetime import datetime
import getpass
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

# Lock for strategy file read/write to prevent race conditions
_strategy_lock = threading.Lock()

# Initialize API
api = HuashengGatewayAPI()

# 贪恐指数缓存 (key: symbol, value: (timestamp, score))
SZDT_CACHE = {}
CACHE_TTL = 300  # 5 分钟缓存

def get_cached_signal(symbol: str, lever: str = "3", emo_area: str = "us"):
    """带缓存的贪恐指数获取（API 失败时回退到过期缓存）"""
    now = time.time()
    if symbol in SZDT_CACHE:
        ts, score = SZDT_CACHE[symbol]
        if now - ts < CACHE_TTL:
            return score
    
    # 缓存失效，调用真实 API
    score = api.fetch_fear_greed_index(symbol, lever, emo_area)
    if score is not None:
        SZDT_CACHE[symbol] = (now, score)
        return score
    
    # API failed — fall back to stale cache if available
    if symbol in SZDT_CACHE:
        logger.warning(f"API returned None for {symbol}, using stale cached value")
        return SZDT_CACHE[symbol][1]
    return 0.0

class StockStrategy(BaseModel):
    name: Optional[str] = ""
    buy_point: float
    sell_point: float
    buy_total: int
    sell_total: int
    buy_limit_price: float
    sell_limit_price: float
    buy_day_interval: int
    sell_day_interval: int
    buy_price_interval: float
    max_position: float
    fear_greed_buy: float
    fear_greed_sell: float
    lever: str 
    emo_area: str

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

@app.get("/api/strategies")
def get_strategies():
    return load_strategies()

@app.get("/api/stock_info/{symbol}")
def get_stock_info(symbol: str):
    """根据代码获取股票名称"""
    try:
        name = api.get_stock_name(symbol.upper())
        return {"symbol": symbol.upper(), "name": name}
    except Exception as e:
        logger.error(f"Error fetching stock info for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch stock info: {e}")

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
        score = api.fetch_fear_greed_index(symbol, strategy.lever, strategy.emo_area)
        current_signal = score if score is not None else 0.0
        
        # 同步更新缓存
        SZDT_CACHE[symbol] = (time.time(), current_signal)

        logger.info(f"Strategy for {symbol} saved and force-refreshed. Price: {current_price}, Signal: {current_signal}")
        
        return {
            "status": "success", 
            "strategy": strategies[symbol],
            "realtime": {
                "lastPrice": current_price,
                "signal": current_signal
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
    strategies = load_strategies()
    symbols = list(strategies.keys())
    realtime_data = {}
    
    # 1. 核心优化：一次性获取所有持仓并脱敏存储
    pos_map = {}
    total_portfolio_value = 0.0
    try:
        holdings_res = api.get_position(exchange_type="P")
        pos_list = holdings_res.get("positionList", []) if holdings_res else []
        for pos in pos_list:
            raw_code = pos.get("stockCode", "")
            # 统一脱敏：YINN.US -> YINN
            clean_code = raw_code.upper().replace(".US", "").replace("US.", "").replace(".HK", "").replace("HK.", "")
            pos_map[clean_code] = pos
            total_portfolio_value += float(pos.get("marketValue", 0))
    except Exception as e:
        logger.error(f"Error pre-fetching holdings: {e}")

    # 2. 遍历策略中的 Symbol
    for raw_symbol in symbols:
        # 同样对策略里的 Symbol 脱敏: US.YINN -> YINN
        clean_symbol = raw_symbol.upper().replace("US.", "").replace(".US", "").replace("HK.", "").replace(".HK", "")
        
        try:
            quote = api.get_realtime_quote(clean_symbol)
            if quote:
                pa_val = quote.get("preAfterPrice")
                lp_val = quote.get("lastPrice")
                # 转换并确保不为0
                pre_after = float(pa_val) if pa_val else 0.0
                last_p = float(lp_val) if lp_val else 0.0
                last_price = pre_after if pre_after > 0 else last_p
            else:
                last_price = 0.0
        except Exception as e:
            logger.error(f"Error getting price for {raw_symbol}: {e}")
            last_price = 0.0
            
        qty = 0
        stock_value = 0.0
        # 使用脱敏后的 Symbol 匹配持仓
        if clean_symbol in pos_map:
            pos = pos_map[clean_symbol]
            qty_str = pos.get("enableAmount") or pos.get("currentAmount") or "0"
            qty = int(float(qty_str))
            stock_value = float(pos.get("marketValue", 0))
        
        weight = (stock_value / total_portfolio_value * 100) if total_portfolio_value > 0 else 0
        
        # 获取真实贪恐信号
        strat = strategies[raw_symbol]
        lever = strat.get("lever", "1")
        emo_area = strat.get("emo_area", "us")
        stock_signal = get_cached_signal(clean_symbol, lever, emo_area)
        
        # 返回数据时以 raw_symbol 为 key (前端预期)
        realtime_data[raw_symbol] = {
            "lastPrice": last_price,
            "quantity": qty,
            "value": stock_value,
            "weight": weight,
            "signal": stock_signal
        }
    return realtime_data

@app.get("/api/holdings")
def get_full_holdings():
    """返回账户中所有的真实持仓列表，包含占比"""
    try:
        res = api.get_position(exchange_type="P")
        pos_list = res.get("positionList", []) if res else []
        total_mkt_val = sum(float(pos.get("marketValue", 0)) for pos in pos_list)
        for pos in pos_list:
            mkt_val = float(pos.get("marketValue", 0))
            pos["weight"] = (mkt_val / total_mkt_val * 100) if total_mkt_val > 0 else 0
        return pos_list
    except Exception as e:
        logger.error(f"Error fetching full holdings: {e}")
        return []

@app.get("/api/indicator")
def get_indicator():
    """获取第一个股票的贪恐得分 (严格按配置同步)"""
    strategies = load_strategies()
    if not strategies:
        return {"value": 0, "name": "No Data"}
    first_symbol = list(strategies.keys())[0]
    strat = strategies[first_symbol]
    score = get_cached_signal(first_symbol, strat.get("lever", "1"), strat.get("emo_area", "us"))
    return {"value": score, "name": f"Fear & Greed ({first_symbol})"}

@app.get("/api/account")
def get_account_status():
    """获取账户综合资产状态"""
    try:
        funds = api.get_account_funds(exchange_type="P")
        if not funds:
            cash, power, net_asset_from_api = 0.0, 0.0, 0.0
        else:
            cash = float(funds.get("enableBalance", 0)) 
            power = float(funds.get("buyPower", 0))      
            net_asset_from_api = float(funds.get("assetBalance", 0)) 

        holdings_res = api.get_position(exchange_type="P")
        pos_list = holdings_res.get("positionList", []) if holdings_res else []
        mkt_val = sum(float(pos.get("marketValue", 0)) for pos in pos_list)
        total_pnl = sum(float(pos.get("incomeBalance", 0)) for pos in pos_list)
        
        return {
            "total_asset": net_asset_from_api,
            "market_value": mkt_val,
            "cash": cash,
            "buying_power": power,
            "total_pnl": total_pnl,
            "currency": "USD"
        }
    except Exception as e:
        logger.error(f"Error fetching account status: {e}")
        return {"total_asset": 0, "market_value": 0, "cash": 0, "buying_power": 0, "total_pnl": 0, "error": str(e)}

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

if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("QuantTrade Backend Starting...")
    print("=" * 60)
    pwd = getpass.getpass("Enter Huasheng Trading Password (blank to skip): ")
    if pwd:
        api.log_in(password=pwd)
    else:
        print("Skipping login...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
