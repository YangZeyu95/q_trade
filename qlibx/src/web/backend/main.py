from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import json
import os
import csv
from typing import Dict, List, Optional
from datetime import datetime

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STRATEGY_FILE = "/Users/Zeyu/Documents/q_trade/qlibx/src/scripts/stock_strategy.json"
SCRIPTS_DIR = "/Users/Zeyu/Documents/q_trade/qlibx/src/scripts"

class StockStrategy(BaseModel):
    name: str
    buy_point: float
    sell_point: float
    buy_total: int
    sell_total: int
    buy_limit_price: float
    sell_limit_price: float
    buy_day_interval: int
    buy_price_interval: float
    max_position: float

def load_strategies():
    if os.path.exists(STRATEGY_FILE):
        with open(STRATEGY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_strategies(strategies):
    with open(STRATEGY_FILE, 'w', encoding='utf-8') as f:
        json.dump(strategies, f, indent=2, ensure_ascii=False)

@app.get("/api/strategies")
def get_strategies():
    return load_strategies()

@app.post("/api/strategies/{symbol}")
def update_strategy(symbol: str, strategy: StockStrategy):
    strategies = load_strategies()
    strategies[symbol] = strategy.dict()
    save_strategies(strategies)
    return {"status": "success", "message": f"Strategy for {symbol} updated"}

@app.delete("/api/strategies/{symbol}")
def delete_strategy(symbol: str):
    strategies = load_strategies()
    if symbol in strategies:
        del strategies[symbol]
        save_strategies(strategies)
        return {"status": "success", "message": f"Strategy for {symbol} deleted"}
    raise HTTPException(status_code=404, detail="Stock strategy not found")

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
    
    # Sort by timestamp descending
    all_history.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    return all_history

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
