import requests
import logging
import base64
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

logger = logging.getLogger(__name__)

class HuashengGatewayAPI:
    """华盛 OpenAPI Gateway 接口封装 (同步 tqqq_bot 成功登录逻辑)"""

    def __init__(self, gateway_url="http://127.0.0.1:11111", data_type=20002):
        self.gateway_url = gateway_url.rstrip('/')
        self.timeout = 10
        self.data_type = data_type

    def _encrypt_password(self, password):
        """AES ECB PKCS7 加密"""
        aes_key_base64 = "m+qS04/2CH1OweCnmXZ3TDZkCQS+hBzY"
        aes_key = base64.b64decode(aes_key_base64)
        cipher = AES.new(aes_key, AES.MODE_ECB)
        password_bytes = password.encode('utf-8')
        padded_password = pad(password_bytes, AES.block_size)
        encrypted_bytes = cipher.encrypt(padded_password)
        return base64.b64encode(encrypted_bytes).decode('utf-8')

    def log_in(self, password=""):
        """使用短路径登录"""
        if not password:
            return False
            
        params = {
            "password": self._encrypt_password(password)
        }
        # 使用 tqqq_bot 验证过的短路径
        result = self._post_request("trade/TradeLogin", params)
        if result:
            logger.info("华盛 OpenAPI 登录成功")
            return True
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

    def get_realtime_quote(self, stock_code, data_type=20002):
        """获取实时报价"""
        params = {
            "security": [{"dataType": data_type, "code": stock_code}],
            "mktTmType": 1
        }
        data = self._post_request("hq/BasicQot", params)
        if data and "basicQot" in data and len(data["basicQot"]) > 0:
            quote = data["basicQot"][0]
            # 强制打印报价原始数据，检查字段名
            logger.info(f"Quote Data for {stock_code}: {quote}")
            return quote
        return None

    def get_stock_name(self, stock_code):
        """从多渠道尝试获取股票名称"""
        # 1. 尝试行情接口
        quote = self.get_realtime_quote(stock_code)
        if quote:
            # 兼容更多字段: name, nameCh, stockName, desCh, name_ch
            name = quote.get("name") or quote.get("nameCh") or quote.get("stockName") or quote.get("desCh")
            if name: return name

        # 2. 如果行情没拿到，尝试从现有持仓里找 (可能已经买过了)
        try:
            res = self.get_position("P")
            pos_list = res.get("positionList") or []
            for pos in pos_list:
                if stock_code.upper() in pos.get("stockCode", "").upper():
                    return pos.get("stockName") or pos.get("name")
        except Exception:
            pass

        return stock_code # 最后保底返回代码本身

    def get_position(self, exchange_type="P"):
        """查询持仓列表"""
        params = {
            "exchangeType": exchange_type,
            "queryCount": 100,
            "queryParamStr": "0"
        }
        res = self._post_request("trade/TradeQueryHoldsList", params)
        # 适配 holdsList 字段
        if res:
            pos_list = res.get("positionList") or res.get("holdsList")
            if pos_list:
                logger.info(f"Raw Position Data ({exchange_type}): {pos_list}")
                return {"positionList": pos_list} # 统一返回格式
            else:
                logger.info(f"Positions API response (no known list field): {res}")
        return res

    def get_stock_position_qty(self, stock_code, exchange_type="P"):
        """获取指定股票的持仓数量"""
        res = self.get_position(exchange_type)
        if not res or "positionList" not in res:
            return 0
            
        for pos in res["positionList"]:
            # 兼容字段名: stockCode, securityCode
            pos_code = pos.get("stockCode") or pos.get("securityCode") or ""
            
            clean_pos_code = pos_code.upper().replace(".US", "").replace("US.", "").replace(".HK", "").replace("HK.", "")
            target_code = stock_code.upper()
            
            if target_code == clean_pos_code:
                # 兼容字段名: enableAmount, canSellAmount, canSellQty, currentAmount
                qty = pos.get("enableAmount") or pos.get("canSellAmount") or pos.get("canSellQty") or pos.get("currentAmount") or 0
                return int(float(qty)) # 某些版本返回的是字符串，先转float再转int
        return 0
