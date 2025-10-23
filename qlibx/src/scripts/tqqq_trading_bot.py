"""
华盛量化 OpenAPI - 自动交易程序
功能：收盘前10分钟，根据当日成交量执行交易策略
- 成交量 > 60M：市价买入1股
- 成交量 < 40M：市价卖出1股
"""

import requests
import time
from datetime import datetime
import pytz
import logging
import json
import os
import base64
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

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


class HuashengGatewayAPI:
    """华盛 OpenAPI Gateway 接口封装"""
    
    def __init__(self, gateway_url="http://127.0.0.1:11111"):
        """
        初始化 API 客户端
        Args:
            gateway_url: OpenAPI Gateway 地址，默认本地运行
        """
        self.gateway_url = gateway_url
        self.timeout = 10
        self._log_in()
    
    def _encrypt_password(self, password):
        """
        使用AES加密密码
        :param password: 原始密码
        :return: 加密后的Base64字符串
        """
        # 1. Base64解码AES密钥
        aes_key_base64 = "m+qS04/2CH1OweCnmXZ3TDZkCQS+hBzY"
        aes_key = base64.b64decode(aes_key_base64)
        
        # 2. 创建AES加密器 (ECB模式)
        cipher = AES.new(aes_key, AES.MODE_ECB)
        
        # 3. 对密码进行PKCS7填充
        password_bytes = password.encode('utf-8')
        padded_password = pad(password_bytes, AES.block_size)
        
        # 4. 加密
        encrypted_bytes = cipher.encrypt(padded_password)
        
        # 5. Base64编码加密结果
        encrypted_base64 = base64.b64encode(encrypted_bytes).decode('utf-8')
    
        return encrypted_base64
    
    def _log_in(self):
        """登录华盛 OpenAPI"""
        params = {
            "password": self._encrypt_password("123456")
        }
        result = self._post_request("trade/TradeLogin", params)
        if result:
            logger.info("华盛 OpenAPI 登录成功")
        else:
            logger.error("华盛 OpenAPI 登录失败")

    def _post_request(self, endpoint, params):
        """统一的POST请求方法"""
        url = f"{self.gateway_url}/{endpoint}"
        data = {
            "timeout_sec": self.timeout,
            "params": params
        }
        try:
            response = requests.post(url, json=data, timeout=self.timeout)
            result = response.json()
            
            if not result.get("ok", False):
                error_msg = result.get("err", "Unknown error")
                logger.error(f"API请求失败: {endpoint}, 错误: {error_msg}")
                return None
            
            return result.get("data")
        except Exception as e:
            logger.error(f"请求异常: {endpoint}, {str(e)}")
            return None
    
    def subscribe_stock(self, stock_code, data_type=2):
        """
        订阅股票行情
        Args:
            stock_code: 股票代码，如 "TQQQ"
            data_type: 股票类型，2=美股
        """
        params = {
            "security": [{
                "dataType": data_type,
                "code": stock_code
            }]
        }
        return self._post_request("hq/Subscribe", params)
    
    def get_realtime_quote(self, stock_code, data_type=2):
        """
        获取实时报价（包含成交量）
        Args:
            stock_code: 股票代码，如 "TQQQ"
            data_type: 股票类型，2=美股
        Returns:
            包含报价信息的字典，重点字段：
            - volume: 当日累计成交量
            - turnover: 成交额
            - lastPrice: 最新价
        """
        params = {
            "security": [{
                "dataType": data_type,
                "code": stock_code
            }],
            "mktTmType": 1  # 1=盘中
        }
        data = self._post_request("hq/BasicQot", params)
        
        if data and "basicQot" in data and len(data["basicQot"]) > 0:
            return data["basicQot"][0]
        return None
    
    def place_order(self, exchangeType, stock_code, entrustAmount, entrustPrice, entrustBs, entrustType):

        params = {
            "exchangeType": exchangeType,
            "stockCode": stock_code,
            "entrustAmount": entrustAmount,
            "entrustPrice": entrustPrice,
            "entrustBs": entrustBs,
            "entrustType": entrustType
        }
        result = self._post_request("trade/TradeEntrust", params)
        
        if result:
            logger.info(f"下单成功: {stock_code}, 方向: {'买入' if entrustBs == '1' else '卖出'}, 数量: {entrustAmount}")
        
        return result
    
    def get_position(self, exchange_type="N"):
        """
        查询持仓
        Args:
            exchange_type: 交易所类型
        Returns:
            持仓列表
        """
        params = {
            "exchangeType": exchange_type,
            "queryCount": 100,
            "queryParamStr": "0"
        }
        return self._post_request("trade/TradeQueryPositionList", params)
    
    def get_stock_position_qty(self, stock_code, exchange_type="N"):
        """
        查询指定股票的持仓数量
        Args:
            stock_code: 股票代码
            exchange_type: 交易所类型
        Returns:
            持仓数量（int），无持仓返回0
        """
        positions = self.get_position(exchange_type)
        
        if not positions or "positionList" not in positions:
            return 0
        
        for pos in positions["positionList"]:
            if pos.get("stockCode") == stock_code:
                return int(pos.get("canSellAmount", 0))
        
        return 0


class TradingStrategy:
    
    def __init__(self, api, state_file="state.json"):
        self.api = api
        self.symbol = "DPST"
        self.data_type = 20002  # 美股
        self.exchange_type = "P"  # 美股交易所
        
        # 成交量阈值（单位：股）
        self.volume_threshold_buy = 60_000_000   # 60M
        self.volume_threshold_sell = 40_000_000  # 40M
        
        # 美东时区
        self.et_tz = pytz.timezone('America/New_York')

        self.state_file = state_file
        self.et_tz = pytz.timezone('America/New_York')
        self.last_execution_date = self.load_state()
        self.executed_today = True  # 今日是否已执行交易
    
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

    def reset_daily_flag(self):
        now_et = datetime.now(self.et_tz)
        current_date = now_et.date()
        if self.last_execution_date != current_date:
            self.executed_today = False
            logger.info(f"新交易日：{current_date}，重置执行标志")

    def execute_strategy(self):
        """执行交易策略"""
        self.reset_daily_flag()

        # 检查是否在交易时间
        if not self.is_trading_time():
            logger.debug("非交易时间，跳过")
            return
        
        # 检查是否接近收盘
        if not self.is_near_close():
            logger.debug("未到收盘前10分钟，跳过")
            return
        
        # 检查今日是否已执行
        if self.executed_today:
            logger.info("今日已执行交易，跳过")
            return
        
        # 获取实时报价
        quote = self.api.get_realtime_quote(self.symbol, self.data_type)
        
        if not quote:
            logger.error("无法获取实时报价")
            return
        
        # 获取当日累计成交量
        volume = quote.get("volume", 0)
        last_price = quote.get("lastPrice", 0)
        
        logger.info(f"当前价格: ${last_price:.2f}, 当日成交量: {volume:,}")
        
        # 判断交易条件
        quantity = int(700 / last_price)
        if last_price <= 83:
            # 成交量大于60M，买入
            logger.info(f" {volume:,} > {self.volume_threshold_buy:,}，执行买入")
            result = self.api.place_order(
                exchangeType=self.exchange_type,
                stock_code=self.symbol,
                entrustAmount=quantity,
                entrustPrice=str(last_price-1),
                entrustBs="1",
                entrustType="3",
            )
            
            if result:
                self.executed_today = True
                self.last_execution_date = datetime.now(self.et_tz).date()
                self.save_state()
                logger.info("买入订单已提交")
            
        elif volume < self.volume_threshold_sell:
            # 成交量小于40M，卖出（需先检查持仓）
            logger.info(f"成交量 {volume:,} < {self.volume_threshold_sell:,}，检查持仓")
            
            position_qty = self.api.get_stock_position_qty(
                self.symbol, 
                self.exchange_type
            )
            
            if position_qty > 0:
                logger.info(f"当前持仓: {position_qty} 股，执行卖出")
                # result = self.api.place_order(
                #     stock_code=self.symbol,
                #     side="2",  # 卖出
                #     quantity=quantity,
                #     exchange_type=self.exchange_type
                # )
                
                if result:
                    self.executed_today = True
                    self.last_execution_date = datetime.now(self.et_tz).date()
                    self.save_state()
                    logger.info("卖出订单已提交")
            else:
                logger.info("无持仓，跳过卖出")
        
        else:
            # 成交量在40M-60M之间，不操作
            logger.info(f"成交量 {volume:,} 在阈值之间，不执行交易")


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

    # 订阅行情
    logger.info(f"订阅 {strategy.symbol} 行情...")
    # api.subscribe_stock(strategy.symbol, strategy.data_type)
    
    # 检查间隔（秒）
    check_interval = 60
    logger.info(f"检查间隔: {check_interval}秒\n")
    
    # 主循环
    try:
        while True:
            strategy.execute_strategy()
            time.sleep(check_interval)
            
    except KeyboardInterrupt:
        logger.info("\n程序已停止")
    except Exception as e:
        logger.error(f"程序异常: {str(e)}", exc_info=True)


if __name__ == "__main__":
    main()
