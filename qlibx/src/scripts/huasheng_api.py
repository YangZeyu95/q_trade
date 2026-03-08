import requests
import logging
import base64
import json
import os
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

logger = logging.getLogger(__name__)

class HuashengGatewayAPI:
    """华盛 OpenAPI Gateway 接口封装"""

    def __init__(self, gateway_url="http://127.0.0.1:11111"):
        self.gateway_url = gateway_url.rstrip('/')
        self.timeout = 10

    def _encrypt_password(self, password):
        """使用AES加密密码"""
        aes_key_base64 = "m+qS04/2CH1OweCnmXZ3TDZkCQS+hBzY"
        aes_key = base64.b64decode(aes_key_base64)
        cipher = AES.new(aes_key, AES.MODE_ECB)
        password_bytes = password.encode('utf-8')
        padded_password = pad(password_bytes, AES.block_size)
        encrypted_bytes = cipher.encrypt(padded_password)
        return base64.b64encode(encrypted_bytes).decode('utf-8')

    def log_in(self, password=""):
        """登录华盛 OpenAPI"""
        if not password:
            logger.warning("No password provided for login")
            return False
        params = {
            "password": self._encrypt_password(password)
        }
        result = self._post_request("trade/TradeLogin", params)
        if result:
            logger.info("华盛 OpenAPI 登录成功")
            return True
        else:
            logger.error("华盛 OpenAPI 登录失败")
            return False

    def _post_request(self, endpoint, params):
        url = f"{self.gateway_url}/{endpoint.lstrip('/')}"
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

    def get_realtime_quote(self, stock_code, data_type=None):
        """获取实时报价 (包含盘前盘后)"""
        upper_code = stock_code.upper()
        
        # 1. 自动推断 data_type (如果未提供)
        if data_type is None:
            if ".HK" in upper_code or "HK." in upper_code:
                data_type = 10000  # 港股股票
            elif upper_code.isdigit() and len(upper_code) <= 5:
                data_type = 10000  # 可能是港股纯数字代码
            else:
                data_type = 20002  # 默认美股 ETF (兼容原有习惯)

        # 2. 清洗代码
        clean_code = upper_code.replace("US.", "").replace(".US", "")
        if data_type >= 20000: # 美股
            clean_code = clean_code.replace("HK.", "").replace(".HK", "")
        else: # 港股
            clean_code = clean_code.replace("HK.", "")
            if ".HK" not in clean_code and clean_code.isdigit():
                clean_code = f"{clean_code.zfill(5)}.HK"
            elif not clean_code.endswith(".HK") and not clean_code.isdigit():
                # 处理如 "TENCENT.HK" 这种非纯数字但带 .HK 的
                if not clean_code.endswith(".HK"): clean_code += ".HK"

        def _fetch(dt, code):
            params = {
                "security": [{"dataType": dt, "code": code}],
                "mktTmType": 0 # 0 代表包含盘前盘后行情
            }
            return self._post_request("hq/BasicQot", params)

        data = _fetch(data_type, clean_code)
        
        # 3. 如果美股没拿到，尝试在 Stock(20000) 和 ETF(20002) 之间切换再试一次
        if not data and 20000 <= data_type <= 20002:
            alt_type = 20000 if data_type == 20002 else 20002
            data = _fetch(alt_type, clean_code)
            if data: data_type = alt_type # 更新成功的类型

        if data and "basicQot" in data and len(data["basicQot"]) > 0:
            quote = data["basicQot"][0]
            logger.debug(f"Quote Data for {clean_code} (type {data_type}): {quote}")
            return quote
        else:
            logger.warning(f"No quote data returned for {clean_code} (raw: {stock_code}, type: {data_type})")
        return None

    def get_stock_name(self, stock_code):
        """从多渠道尝试获取股票名称"""
        # 1. 尝试行情接口 (get_realtime_quote 内部现在会自动尝试多种类型)
        quote = self.get_realtime_quote(stock_code)
        if quote:
            name = quote.get("name") or quote.get("nameCh") or quote.get("stockName") or quote.get("desCh")
            if name: return name

        # 2. 如果行情没拿到，尝试从现有持仓里找
        # 自动清洗代码
        clean_code = stock_code.upper().replace("US.", "").replace(".US", "").replace("HK.", "").replace(".HK", "")
        try:
            res = self.get_position("P")
            pos_list = res.get("positionList") or []
            for pos in pos_list:
                pos_code = pos.get("stockCode", "").upper()
                clean_pos_code = pos_code.replace(".US", "").replace("US.", "").replace(".HK", "").replace("HK.", "")
                if clean_code == clean_pos_code:
                    return pos.get("stockName") or pos.get("name")
        except Exception:
            pass

        return clean_code

    def get_position(self, exchange_type="P"):
        """查询持仓列表"""
        params = {
            "exchangeType": exchange_type,
            "queryCount": 100,
            "queryParamStr": "0"
        }
        res = self._post_request("trade/TradeQueryHoldsList", params)
        if res:
            pos_list = res.get("positionList") or res.get("holdsList")
            if pos_list:
                logger.debug(f"Raw Position Data ({exchange_type}): {pos_list}")
                return {"positionList": pos_list}
            else:
                logger.debug(f"Positions API response (no known list field): {res}")
        return res

    def get_stock_position_qty(self, stock_code, exchange_type="P"):
        """获取指定股票的持仓数量"""
        res = self.get_position(exchange_type)
        if not res or "positionList" not in res:
            return 0
            
        for pos in res["positionList"]:
            pos_code = pos.get("stockCode") or pos.get("securityCode") or ""
            clean_pos_code = pos_code.upper().replace(".US", "").replace("US.", "").replace(".HK", "").replace("HK.", "")
            target_code = stock_code.upper()
            
            if target_code == clean_pos_code:
                qty = pos.get("enableAmount") or pos.get("canSellAmount") or pos.get("canSellQty") or pos.get("currentAmount") or 0
                return int(float(qty))
        return 0

    def fetch_fear_greed_index(self, symbol, lever, emo_area):
        """
        从 szdt.tech 获取实时贪恐指数
        emo_area 支持: 
          - us: 美股
          - a: 中概
          - coin: 币股
          - hk: 港股
          - other: 其他
        """
        # 优先从环境变量获取
        auth_key = os.getenv("SZDT_AUTH_KEY", "")
        if not auth_key:
            logger.error("SZDT_AUTH_KEY (X-Auth) is missing. Cannot fetch real index.")
            return None

        url = "https://szdt.tech/api/partner/invest/stock/scan"
        headers = {
            "X-Auth": auth_key
        }
        
        # 自动补全代码格式: 
        # 1. 港股 (hk) -> HK.00700
        # 2. 其他所有类型 (us/a/coin/other) -> US.XXX (szdt.tech 对这些类型的 code 要求)
        clean_symbol = symbol.upper().replace(".US", "").replace("US.", "").replace(".HK", "").replace("HK.", "")
        if emo_area == "hk":
            code = f"HK.{clean_symbol.zfill(5)}"
        else:
            code = f"US.{clean_symbol}"

        payload = {
            "code": code,
            "lever": str(lever),
            "emo_area": emo_area
        }

        try:
            # 使用 data 参数发送 form-data
            response = requests.post(url, headers=headers, data=payload, timeout=10)
            if response.status_code == 200:
                res_json = response.json()
                if res_json.get("status") == 1:
                    score = res_json.get("data", {}).get("score", 0)
                    logger.info(f"API Success: {code} (Area: {emo_area}, Lever: {lever}) Score = {score}")
                    return float(score)
                else:
                    logger.error(f"API Logic Error for {code}: {res_json.get('msg')}")
            else:
                logger.error(f"API HTTP Error {response.status_code} for {code}")
        except Exception as e:
            logger.error(f"Failed to fetch Fear/Greed for {code}: {e}")
        
        return None

    def get_account_funds(self, exchange_type="P"):
        """获取账户资金信息 (融资融券版)"""
        params = {
            "exchangeType": exchange_type
        }
        # 使用你提供的最新路径
        res = self._post_request("trade/TradeQueryMarginFundInfo", params)
        if res:
            logger.debug(f"Margin Fund Info: {res}")
        return res

    def get_total_portfolio_value(self):
        """计算全账户总市值"""
        try:
            res = self.get_position("P")
            if not res or "positionList" not in res:
                return 0.0
            return sum(float(pos.get("marketValue", 0)) for pos in res["positionList"])
        except Exception:
            return 0.0

    def get_order_details(self, order_id, exchange_type="P"):
        """查询订单详情"""
        params = {
            "exchangeType": exchange_type,
            "orderId": order_id
        }
        res = self._post_request("trade/TradeQueryOrderDetails", params)
        if res:
            logger.debug(f"Order Details for {order_id}: {res}")
        return res

    def place_order(self, exchangeType, stock_code, entrustAmount, entrustPrice, entrustBs, entrustType):
        """下单"""
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
            action = "买入" if entrustBs == "1" else "卖出"
            logger.info(f"下单成功: {stock_code}, 方向: {action}, 数量: {entrustAmount}, 价格: {entrustPrice}")
        return result
