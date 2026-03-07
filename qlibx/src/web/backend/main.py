from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import json
import os
import csv
import sys
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

# Initialize API
api = HuashengGatewayAPI()

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

def load_strategies():
    if os.path.exists(STRATEGY_FILE):
        with open(STRATEGY_FILE, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except Exception:
                return {}
    return {}

def save_strategies(strategies):
    with open(STRATEGY_FILE, 'w', encoding='utf-8') as f:
        json.dump(strategies, f, indent=2, ensure_ascii=False)

@app.get("/api/strategies")
def get_strategies():
    return load_strategies()

@app.get("/api/stock_info/{symbol}")
def get_stock_info(symbol: str):
    """根据代码获取股票名称"""
    name = api.get_stock_name(symbol.upper())
    return {"symbol": symbol.upper(), "name": name}

@app.post("/api/strategies/{symbol}")
def update_strategy(symbol: str, strategy: StockStrategy):
    try:
        symbol = symbol.strip().upper()
        if not symbol:
            raise HTTPException(status_code=400, detail="Symbol cannot be empty")
        
        # 自动刷新股票名字
        try:
            official_name = api.get_stock_name(symbol)
            if official_name:
                strategy.name = official_name
        except Exception as e:
            logger.warning(f"Failed to fetch name for {symbol}: {e}")
            if not strategy.name:
                strategy.name = symbol

        # 数值基础校验
        if strategy.buy_point < 0 or strategy.sell_point < 0:
            raise HTTPException(status_code=400, detail="Buy/Sell points cannot be negative")

        strategies = load_strategies()
        # 存储转换后的字典
        strategies[symbol] = strategy.model_dump()
        save_strategies(strategies)
        logger.info(f"Strategy for {symbol} saved successfully")
        return {"status": "success", "message": f"Strategy for {symbol} updated"}
    except Exception as e:
        logger.error(f"Error saving strategy {symbol}: {str(e)}")
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/strategies/{symbol}")
def delete_strategy(symbol: str):
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
    
    # 1. 直接获取持仓列表来计算总市值 (比调用 API 方法更可靠)
    total_portfolio_value = 0.0
    try:
        holdings_res = api.get_position(exchange_type="P")
        pos_list = holdings_res.get("positionList", []) if holdings_res else []
        total_portfolio_value = sum(float(pos.get("marketValue", 0)) for pos in pos_list)
    except Exception as e:
        logger.error(f"Error calculating total portfolio value: {e}")

    for symbol in symbols:
        try:
            quote = api.get_realtime_quote(symbol)
            last_price = quote.get("lastPrice", 0) if quote else 0
        except Exception:
            last_price = 0
            
        try:
            qty = api.get_stock_position_qty(symbol, exchange_type="P")
        except Exception:
            qty = 0
        
        stock_value = qty * last_price
        # 2. 计算权重
        weight = (stock_value / total_portfolio_value * 100) if total_portfolio_value > 0 else 0
        
        import random
        stock_signal = random.uniform(-100, 100)
        
        realtime_data[symbol] = {
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
        
        # 计算总市值
        total_mkt_val = sum(float(pos.get("marketValue", 0)) for pos in pos_list)
        
        # 为每项增加占比
        for pos in pos_list:
            mkt_val = float(pos.get("marketValue", 0))
            pos["weight"] = (mkt_val / total_mkt_val * 100) if total_mkt_val > 0 else 0
            
        return pos_list
    except Exception as e:
        logger.error(f"Error fetching full holdings: {e}")
        return []

@app.get("/api/indicator")
def get_indicator():
    import random
    return {"value": random.uniform(-100, 100), "name": "Fear & Greed Index"}

@app.get("/api/history/{symbol}")
def get_history(symbol: str):
    history_file = os.path.join(SCRIPTS_DIR, f"{symbol.lower()}_trading.csv")
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
    if not os.path.exists(SCRIPTS_DIR):
        return []
    for filename in os.listdir(SCRIPTS_DIR):
        if filename.endswith("_trading.csv"):
            symbol = filename.replace("_trading.csv", "").upper()
            filepath = os.path.join(SCRIPTS_DIR, filename)
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
