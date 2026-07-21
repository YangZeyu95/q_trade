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
        # Keep the last gateway error for the backend heartbeat/UI.  This is
        # diagnostic text only; request parameters (including credentials)
        # are deliberately not retained here.
        self.last_error = None

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

    @staticmethod
    def _clean_security_code(stock_code):
        """Normalize the code shape used by the strategy and Gateway."""
        return str(stock_code or "").upper().replace("US.", "").replace(".US", "")

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
                self.last_error = f"{endpoint}: {error_msg}"
                logger.error(f"API请求失败: {endpoint}, 错误: {error_msg}")
                return None
            self.last_error = None
            return result.get("data")
        except Exception as e:
            self.last_error = f"{endpoint}: {type(e).__name__}: {e}"
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

    def get_realtime_quotes(self, stock_codes, data_type=None):
        """Batch-fetch quotes and return ``{normalized_code: quote}``.

        The Gateway BasicQot endpoint accepts multiple securities in one
        request.  The strategy uses this once per evaluation cycle so each
        symbol decision reads the same quote snapshot instead of opening one
        Gateway request per symbol.
        """
        codes = []
        seen = set()
        for stock_code in stock_codes or []:
            raw_code = str(stock_code or "").strip().upper()
            if not raw_code:
                continue
            if data_type is None:
                if ".HK" in raw_code or "HK." in raw_code or (
                    raw_code.isdigit() and len(raw_code) <= 5
                ):
                    current_type = 10000
                else:
                    current_type = 20002
            else:
                current_type = data_type

            clean_code = self._clean_security_code(raw_code)
            if current_type < 20000:
                clean_code = clean_code.replace("HK.", "")
                if clean_code.isdigit():
                    clean_code = f"{clean_code.zfill(5)}.HK"
                elif not clean_code.endswith(".HK"):
                    clean_code = f"{clean_code}.HK"
            if clean_code and clean_code not in seen:
                seen.add(clean_code)
                codes.append((clean_code, current_type))

        if not codes:
            return {}

        # A strategy cycle currently contains one market/data type.  If a
        # caller mixes markets, issue one request per type rather than
        # silently sending an invalid mixed payload to the Gateway.
        by_type = {}
        for clean_code, current_type in codes:
            by_type.setdefault(current_type, []).append(clean_code)

        result = {}
        for current_type, clean_codes in by_type.items():
            def _fetch(dt):
                params = {
                    "security": [
                        {"dataType": dt, "code": code} for code in clean_codes
                    ],
                    "mktTmType": 0,
                }
                return self._post_request("hq/BasicQot", params)

            data = _fetch(current_type)
            # Keep the same US stock/ETF compatibility as the single quote
            # method, but only retry once for the whole batch.
            if not data and 20000 <= current_type <= 20002:
                alternate_type = 20000 if current_type == 20002 else 20002
                data = _fetch(alternate_type)

            if not isinstance(data, dict):
                continue
            for quote in data.get("basicQot") or []:
                if not isinstance(quote, dict):
                    continue
                security = quote.get("security") or {}
                returned_code = (
                    security.get("code")
                    or quote.get("code")
                    or quote.get("stockCode")
                )
                normalized = self._clean_security_code(returned_code)
                if normalized:
                    result[normalized] = quote

        return result

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
            target_code = (
                stock_code.upper()
                .replace(".US", "")
                .replace("US.", "")
                .replace(".HK", "")
                .replace("HK.", "")
            )
            
            if target_code == clean_pos_code:
                qty = pos.get("enableAmount") or pos.get("canSellAmount") or pos.get("canSellQty") or pos.get("currentAmount") or 0
                try:
                    quantity = float(qty)
                    if not quantity.is_integer() or quantity < 0:
                        return 0
                    return int(quantity)
                except (TypeError, ValueError, OverflowError):
                    logger.warning(
                        "Invalid sellable quantity for %s: %r", stock_code, qty
                    )
                    return 0
        return 0

    def fetch_fear_greed_index(self, symbol, lever, emo_area, auth_key=None):
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
        if auth_key is None:
            auth_key = os.getenv("SZDT_AUTH_KEY", "")
        auth_key = str(auth_key).strip()
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

    def check_connection(self, exchange_type="P"):
        """通过只读账户查询检查网关和当前交易会话是否可用。

        This deliberately does not submit an order. ``False`` means the
        gateway request failed or the trading session is no longer valid.
        """
        result = self._post_request(
            "trade/TradeQueryMarginFundInfo",
            {"exchangeType": exchange_type},
        )
        return result is not None

    def get_total_portfolio_value(self, exchange_type="P"):
        """计算全账户总持仓市值，查询失败时返回 None。"""
        try:
            res = self.get_position(exchange_type)
            if res is None or "positionList" not in res:
                return None
            return sum(abs(float(pos.get("marketValue", 0))) for pos in res["positionList"])
        except Exception:
            return None

    def get_order_details(self, order_id, exchange_type="P"):
        """查询当日委托状态。

        The gateway does not expose ``TradeQueryOrderDetails`` on this API
        version. Use the documented current-day entrust query with a specific
        entrust id instead, then normalize its response for the strategy's
        existing polling code.
        """
        params = {
            "exchangeType": exchange_type,
            "entrustId": [str(order_id)],
        }
        res = self._post_request("trade/TradeQueryRealEntrustList", params)
        if not res:
            return res

        records = []
        if isinstance(res, list):
            records = res
        elif isinstance(res, dict):
            # Depending on gateway release, the list is returned directly
            # under data or one level deeper under data.data.
            candidates = [res]
            nested = res.get("data")
            if isinstance(nested, (dict, list)):
                candidates.append(nested)
                if isinstance(nested, dict) and isinstance(nested.get("data"), list):
                    candidates.append(nested["data"])
            for candidate in candidates:
                if isinstance(candidate, list):
                    records.extend(candidate)
                elif isinstance(candidate, dict):
                    records.append(candidate)

        target_id = str(order_id).strip()
        for record in records:
            if not isinstance(record, dict):
                continue
            candidate_id = (
                record.get("entrustId")
                or record.get("entrustNo")
                or record.get("orderId")
                or record.get("recordNo")
            )
            if str(candidate_id or "").strip() != target_id:
                continue

            normalized = dict(record)
            raw_status = record.get("status") or record.get("entrustStatus")
            if raw_status is not None:
                # update_pending_orders already normalizes numeric gateway
                # status codes; keep both names for compatibility.
                normalized["orderStatus"] = raw_status
                normalized["orderStatusName"] = raw_status
            elif record.get("statusDesc"):
                normalized["orderStatusName"] = record["statusDesc"]
            logger.debug(f"Order Details for {order_id}: {normalized}")
            return normalized

        logger.warning("No current-day order found for %s", order_id)
        return None

    @staticmethod
    def extract_order_id(result):
        """Extract an order number from the gateway's response variants.

        Depending on the gateway version, a successful order response may be
        ``{"orderId": "..."}``, ``{"data": "..."}``, or a nested
        ``{"data": {"data": "..."}}`` after the common response wrapper
        has been removed.
        """
        if result is None:
            return ""
        if isinstance(result, (str, int)):
            return str(result).strip()
        if isinstance(result, dict):
            for key in ("orderId", "order_id", "entrustId", "entrustNo", "recordNo"):
                value = result.get(key)
                if value is not None and str(value).strip():
                    return str(value).strip()
            for key in ("data", "result"):
                if key in result:
                    order_id = HuashengGatewayAPI.extract_order_id(result[key])
                    if order_id:
                        return order_id
        return ""

    def cancel_order(
        self,
        order_id,
        stock_code,
        exchange_type="P",
        entrust_amount=None,
        entrust_price=None,
        entrust_type="3",
    ):
        """Cancel a regular order using its original order parameters."""
        params = {
            "exchangeType": exchange_type,
            "stockCode": stock_code,
            "entrustId": str(order_id),
        }
        if entrust_amount is not None:
            params["entrustAmount"] = str(entrust_amount)
        if entrust_price is not None:
            params["entrustPrice"] = str(entrust_price)
        if entrust_type is not None:
            params["entrustType"] = str(entrust_type)
        return self._post_request("trade/TradeCancelEntrust", params)

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
            logger.info(
                f"下单成功: {stock_code}, 方向: {action}, 数量: {entrustAmount}, "
                f"价格: {entrustPrice}, 委托号: {self.extract_order_id(result) or '未知'}"
            )
        return result
