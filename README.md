# q_trade: Quantitative Trading Platform

`q_trade` is a professional quantitative trading study and execution platform. It integrates the **Huasheng (Hstong) Quant Gateway API** for live trading execution and **Microsoft Qlib** for quantitative research and backtesting.

The project features a modern Web Dashboard for real-time strategy management, a robust multi-stock trading bot, and a shared API layer for secure brokerage interaction.

## 🏗 Project Architecture

- **Web Dashboard (Full-Stack):**
  - **Frontend:** React + Vite + Vanilla CSS (Widescreen layout).
  - **Backend:** FastAPI (Python) with Pydantic validation and real-time portfolio calculation.
- **Trading Core:**
  - **`huasheng_api.py`:** Shared brokerage wrapper with AES-ECB-PKCS7 encryption for secure trade logins.
  - **`tqqq_trading_bot.py`:** Multi-stock automated execution engine driven by signal indicators.
- **Research:**
  - **`qlibx/qlib/`:** Local Qlib copy for data processing and backtesting.
  - **`qlib_backtest_TQQQ.py`:** Strategy verification scripts.

## ✨ Key Features

- **Multi-Stock Management:** Manage multiple trading strategies (TQQQ, DPST, MSTU, etc.) through a single interface.
- **Real-time Monitoring:** 5-second polling for live prices, account holdings, and portfolio weights.
- **Strategy Dashboard:** 
  - **CRUD Operations:** Easily add, edit, or delete trading rules.
  - **Auto-Fetch Names:** Automatically retrieves stock names from the brokerage gateway.
  - **Import from Holdings:** One-click to convert an existing holding into a tracked trading strategy.
- **Signal-Driven Execution:** Custom indicators (e.g., Fear & Greed Index) integrated into the trading decision loop.
- **Security First:** Uses `getpass` for terminal-based password entry; no credentials are ever stored in plain text or committed to Git.

## 🚀 Getting Started

### 1. Prerequisites
- Python 3.8+
- Node.js (for the frontend)
- A running **Huasheng OpenAPI Gateway** instance (defaults to `http://127.0.0.1:11111`).

### 2. Installation

#### Clone and Install Hstong SDK
```bash
cd py-hs-gateway-api-dev
./local_install_build.sh
pip install -r requirements.txt
```

#### Install Backend Dependencies
```bash
cd qlibx/src/web/backend
pip install fastapi uvicorn pydantic requests pycryptodome
```

#### Install Frontend Dependencies
```bash
cd qlibx/src/web/frontend
npm install
```

### 3. Running the Platform

#### Step A: Start the Backend
The backend will prompt you for your Huasheng trading password for secure login.
```bash
cd qlibx/src/web/backend
python main.py
```

#### Step B: Start the Frontend
```bash
cd qlibx/src/web/frontend
npm run dev
```
Navigate to `http://localhost:5173` to access the dashboard.

#### Step C: Start the Trading Bot
The bot runs in a loop, polling strategies from the backend and executing trades during market hours.
```bash
cd qlibx/src/scripts
python tqqq_trading_bot.py
```

## ⚙️ Strategy Configuration (`stock_strategy.json`)
Trading rules are stored in `qlibx/src/scripts/stock_strategy.json`. 

| Field | Description |
| :--- | :--- |
| `buy_point` | Price threshold to trigger buy logic. |
| `buy_total` | Amount (in USD) to buy per transaction. |
| `buy_day_interval` | Minimum days between buy actions. |
| `fear_greed_buy` | Signal threshold for buying (e.g., -50 for Fear). |
| `max_position` | Maximum percentage of total portfolio allowed for this stock. |

## 🧪 Testing
- **Backend Tests:** `python qlibx/src/web/backend/test_main.py`
- **Bot Tests:** `python qlibx/src/scripts/test_tqqq_trading_bot.py`

---
**Disclaimer:** *Trading involves significant risk. This platform is for educational and research purposes. Always verify strategies in a paper trading environment before using real capital.*
