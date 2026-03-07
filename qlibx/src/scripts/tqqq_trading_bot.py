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
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from huasheng_api import HuashengGatewayAPI

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('trading.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class TradingStrategy:

    def __init__(self, api, strategy_file="stock_strategy.json"):
        self.api = api
        self.data_type = 20002  # 美股
        self.exchange_type = "P"  # 美股交易所

        # 成交量阈值（单位：股）
        self.volume_threshold_buy = 60_000_000   # 60M
        self.volume_threshold_sell = 40_000_000  # 40M

        # 美东时区
        self.et_tz = pytz.timezone('America/New_York')

        self.strategy_file = strategy_file  # 策略文件
        # Initialize strategy configuration
        self.stock_strategies = self.load_stock_strategies()

    def load_stock_strategies(self):
        """Load stock-specific strategy parameters from a JSON file"""
        default_strategies = {
            "TQQQ": {
                "name": "Invesco QQQ Trust ETF",
                "buy_point": 83.0,  # 买入点位
                "sell_point": 85.0,  # 卖出点位
                "buy_total": 700,  # 买入总金额
                "sell_total": 0,  # 卖出总金额 (0表示卖出所有持仓)
                "buy_limit_price": 0.0,  # 买入限价 (0表示市价单)
                "sell_limit_price": 0.0,  # 卖出限价 (0表示市价单)
                "buy_day_interval": 1,  # 买入天数间隔
                "buy_price_interval": 2.0,  # 买入价格间隔百分比
                "max_position": 100.0  # 最大仓位百分比
            }
        }

        if os.path.exists(self.strategy_file):
            try:
                with open(self.strategy_file, 'r', encoding='utf-8') as f:
                    strategies = json.load(f)
                    # Merge with defaults to ensure all required fields are present
                    for symbol, defaults in default_strategies.items():
                        if symbol not in strategies:
                            strategies[symbol] = defaults
                        else:
                            # Update with defaults if any fields are missing
                            for key, default_value in defaults.items():
                                if key not in strategies[symbol]:
                                    strategies[symbol][key] = default_value
                    return strategies
            except Exception as e:
                logger.error(f"Failed to load strategy file: {str(e)}")
                return default_strategies
        else:
            # Create strategy file with defaults if it doesn't exist
            logger.warning(f"Strategy file {self.strategy_file} not found. Please create this file with stock strategy parameters.")
            return default_strategies

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
        Args:
            symbol: Stock symbol traded
            action: 'buy' or 'sell'
            quantity: Number of shares traded
            price: Price at which the trade was executed
            volume: Market volume at time of trade
            order_result: Result from the order placement API call
        """
        trade_record = [
            datetime.now(self.et_tz).isoformat(),
            symbol,
            action,
            quantity,
            price,
            volume,
            order_result
        ]

        # Define the CSV file path
        trade_log_file = f"{symbol.lower()}_trading.csv"  # Use CSV format for easy loading

        # Check if file exists to determine if we need to write headers
        file_exists = os.path.isfile(trade_log_file)

        # Write the trade record to the CSV file
        try:
            with open(trade_log_file, 'a', newline='', encoding='utf-8') as csvfile:
                writer = csv.writer(csvfile)

                # Write header if file is new
                if not file_exists:
                    writer.writerow(['timestamp', 'symbol', 'action', 'quantity', 'price', 'volume', 'order_result'])

                # Write the trade record
                writer.writerow(trade_record)

            logger.info(f"Trade recorded: {action} {quantity} shares of {symbol} at ${price}")
        except Exception as e:
            logger.error(f"Failed to save trade log: {str(e)}")

    def is_trading_time(self):
        """检查是否在交易时间"""
        now_et = datetime.now(self.et_tz)

        # 检查是否为工作日
        if now_et.weekday() >= 5:  # 周六日
            return False

        # 美股常规交易时间：9:30-16:00 ET
        market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)

        return market_open <= now_et <= market_close

    def is_near_close(self, minutes_before=10):
        """检查是否接近收盘"""
        now_et = datetime.now(self.et_tz)
        market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)

        time_to_close = (market_close - now_et).total_seconds() / 60

        return 0 < time_to_close <= minutes_before

    def load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                    if data.get('last_execution_date'):
                        return datetime.strptime(data['last_execution_date'], '%Y-%m-%d').date()
            except Exception as e:
                logger.error(f"Failed to load state: {str(e)}")
        return None

    def save_state(self):
        try:
            with open(self.state_file, 'w') as f:
                json.dump({'last_execution_date': self.last_execution_date.strftime('%Y-%m-%d') if self.last_execution_date else None}, f)
            logger.debug("State saved successfully")
        except Exception as e:
            logger.error(f"Failed to save state: {str(e)}")

    def get_signal_indicator(self, symbol):
        """
        Fetch the current signal indicator for a specific stock.
        """
        try:
            # Fetch from our backend's realtime endpoint which now includes signals
            response = requests.get("http://localhost:8000/api/realtime", timeout=5)
            if response.status_code == 200:
                data = response.json()
                return data.get(symbol, {}).get("signal", 0)
            return 0
        except Exception as e:
            logger.debug(f"Failed to fetch signal indicator for {symbol}: {e}")
            return 0

    def execute_strategy(self, symbol):
        """执行交易策略 for a specific stock"""
        # ... (中间代码省略) ...
        
        # 获取当日累计成交量
        volume = quote.get("volume", 0)
        last_price = quote.get("lastPrice", 0)
        
        # Get signal indicator for THIS symbol
        signal_value = self.get_signal_indicator(symbol)

        logger.info(f"{symbol} 价格: ${last_price:.2f}, 成交量: {volume:,}, 信号值: {signal_value:.1f}")

        # Get the strategy for this stock
        strategy = self.get_stock_strategy(symbol)
        if not strategy:
            logger.error(f"No strategy available for {symbol}, skipping trade")
            return

        # Calculate quantity based on strategy
        quantity = int(strategy['buy_total'] / last_price)

        # Check buy conditions: price AND signal indicator
        if last_price <= strategy['buy_point'] and signal_value <= strategy.get('fear_greed_buy', 100):
            # Check position control
            current_position_qty = self.api.get_stock_position_qty(symbol, self.exchange_type)
            current_position_value = current_position_qty * last_price
            total_portfolio_value = self.api.get_total_portfolio_value()
            max_position_pct = strategy['max_position']

            if max_position_pct < 100.0 and total_portfolio_value > 0:
                current_position_pct = (current_position_value / total_portfolio_value) * 100
                if current_position_pct >= max_position_pct:
                    logger.info(f"{symbol} 仓位 {current_position_pct:.2f}% 已达上限 {max_position_pct:.2f}%")
                    return

            if self.check_buy_conditions(symbol, strategy, last_price):
                logger.info(f"满足买入条件: 价格 ${last_price:.2f} <= {strategy['buy_point']:.2f}, 信号 {signal_value:.1f} <= {strategy.get('fear_greed_buy', 100)}")
                
                buy_price = strategy['buy_limit_price'] if strategy['buy_limit_price'] > 0 else str(last_price)
                result = self.api.place_order(
                    exchangeType=self.exchange_type,
                    stock_code=symbol,
                    entrustAmount=quantity,
                    entrustPrice=buy_price,
                    entrustBs="1",
                    entrustType="3",
                )
                if result:
                    self.record_trade(symbol, "buy", quantity, last_price, volume, result)

        elif last_price >= strategy['sell_point'] or signal_value >= strategy.get('fear_greed_sell', -100):
            # Check sell conditions
            position_qty = self.api.get_stock_position_qty(symbol, self.exchange_type)
            if position_qty > 0:
                reason = "价格" if last_price >= strategy['sell_point'] else "信号值"
                logger.info(f"满足卖出条件({reason}): 价格 ${last_price:.2f}, 信号 {signal_value:.1f}")

                sell_price = strategy['sell_limit_price'] if strategy['sell_limit_price'] > 0 else str(last_price)
                result = self.api.place_order(
                    exchangeType=self.exchange_type,
                    stock_code=symbol,
                    entrustAmount=position_qty, # Sell all position if sell condition met? 
                    # Actually strategy['sell_total'] could be used, but usually we sell all or a fixed amount.
                    # Here we use position_qty for simplicity as per common bot logic.
                    entrustPrice=sell_price,
                    entrustBs="2",
                    entrustType="3",
                )
                if result:
                    self.record_trade(symbol, "sell", position_qty, last_price, volume, result)

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

    def get_last_buy_date(self, symbol):
        """
        Get the last buy date for this stock from the trade history
        Args:
            symbol: Stock symbol
        Returns:
            Last buy date or None if no previous buy records
        """
        trade_log_file = f"{symbol.lower()}_trading.csv"
        if not os.path.exists(trade_log_file):
            return None

        try:
            # Read the last few lines of the CSV to find the most recent buy
            with open(trade_log_file, 'r', encoding='utf-8') as csvfile:
                lines = csvfile.readlines()

                # Skip header and read from the end
                for i in range(len(lines) - 1, 0, -1):
                    line = lines[i].strip()
                    if line:
                        parts = line.split(',')
                        # Format: timestamp, symbol, action, quantity, price, volume, order_result
                        if len(parts) >= 3 and parts[2].strip() == "buy":
                            # Extract the date from the timestamp (first field)
                            # Timestamp format is like: 2023-11-17T10:30:00-05:00
                            timestamp_str = parts[0].replace('"', '').strip()
                            # Parse the date part
                            date_str = timestamp_str.split('T')[0]
                            return datetime.strptime(date_str, '%Y-%m-%d').date()
        except Exception as e:
            logger.error(f"Error reading last buy date for {symbol}: {str(e)}")

        return None

    def get_last_buy_price(self, symbol):
        """
        Get the last buy price for this stock from the trade history
        Args:
            symbol: Stock symbol
        Returns:
            Last buy price or None if no previous buy records
        """
        trade_log_file = f"{symbol.lower()}_trading.csv"
        if not os.path.exists(trade_log_file):
            return None

        try:
            # Read the last few lines of the CSV to find the most recent buy
            with open(trade_log_file, 'r', encoding='utf-8') as csvfile:
                lines = csvfile.readlines()

                # Skip header and read from the end
                for i in range(len(lines) - 1, 0, -1):
                    line = lines[i].strip()
                    if line:
                        parts = line.split(',')
                        # Format: timestamp, symbol, action, quantity, price, volume, order_result
                        if len(parts) >= 5 and parts[2].strip() == "buy":
                            return float(parts[4])  # Return the price from the last buy
        except Exception as e:
            logger.error(f"Error reading last buy price for {symbol}: {str(e)}")

        return None


def main():
    """主程序"""
    logger.info("=" * 60)
    logger.info("量化交易程序启动")
    logger.info("=" * 60)
    logger.info("策略说明:")
    logger.info("  - 收盘前10分钟检查成交量")
    logger.info("  - 成交量 > 60M: 市价买入1股")
    logger.info("  - 成交量 < 40M: 市价卖出1股")
    logger.info("  - 每日最多执行一次")
    logger.info("=" * 60)

    # 初始化API
    api = HuashengGatewayAPI()

    # 创建策略实例
    strategy = TradingStrategy(api)

    # 获取所有配置的股票
    stocks = list(strategy.stock_strategies.keys())
    logger.info(f"配置的交易股票: {stocks}")

    # 订阅行情
    for stock in stocks:
        logger.info(f"订阅 {stock} 行情...")
        # api.subscribe_stock(stock, strategy.data_type)

    # 检查间隔（秒）
    check_interval = 60
    logger.info(f"检查间隔: {check_interval}秒\n")

    # 主循环
    try:
        while True:
            # 对每个配置的股票执行策略
            for stock in stocks:
                strategy.execute_strategy(stock)

            time.sleep(check_interval)

    except KeyboardInterrupt:
        logger.info("\n程序已停止")
    except Exception as e:
        logger.error(f"程序异常: {str(e)}", exc_info=True)


if __name__ == "__main__":
    main()
