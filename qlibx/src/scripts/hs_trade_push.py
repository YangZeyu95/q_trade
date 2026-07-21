"""华盛官方 SDK 的交易状态推送适配器。

订单提交仍由现有的 HTTP 封装完成；这个模块只负责通过 SDK 的 TCP 长连接
订阅 TradeStockDeliverNotify，并把成交/委托状态交给交易策略落盘。HTTP
订单查询轮询继续保留，作为网关断线或推送丢失时的兜底。
"""

import logging
import os
from typing import Callable, Optional


# 这个 SDK 内置的 *_pb2.py 是旧版 protoc 生成的。必须在第一次导入 hs
# 之前设置，否则 protobuf 6 会拒绝旧式 Descriptor 构造。
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

try:
    from hs.api.hs_open_api import OpenAPI
    from hs.common.protobuf_utils import parse_payload
    from hs.common.pb.common.constant.NotifyMsgType_pb2 import (
        TradeStockDeliverMsgType,
    )

    SDK_AVAILABLE = True
    SDK_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - only exercised on an uninstalled SDK
    OpenAPI = None
    parse_payload = None
    TradeStockDeliverMsgType = None
    SDK_AVAILABLE = False
    SDK_IMPORT_ERROR = exc


logger = logging.getLogger(__name__)


class TradePushListener:
    """订阅华盛交易推送，并将成交/委托状态转交给调用方。"""

    def __init__(
        self,
        callback: Callable[[object], None],
        ip: Optional[str] = None,
        http_port: Optional[str] = None,
        tcp_port: Optional[str] = None,
    ):
        if not SDK_AVAILABLE:
            raise RuntimeError(f"华盛 SDK 不可用: {SDK_IMPORT_ERROR}")
        if not callable(callback):
            raise TypeError("交易推送 callback 必须可调用")

        self.callback = callback
        self.ip = ip or os.getenv("HUASHENG_GATEWAY_IP", "127.0.0.1")
        try:
            # SDK 的 OpenAPI 注解虽写 str，但 socket.connect_ex 实际要求
            # TCP 端口为 int；HTTP 端口也统一转为整数。
            self.http_port = int(http_port or os.getenv("HUASHENG_HTTP_PORT", "11111"))
            self.tcp_port = int(tcp_port or os.getenv("HUASHENG_TCP_PORT", "11112"))
        except (TypeError, ValueError) as exc:
            raise ValueError("HUASHENG_HTTP_PORT/HUASHENG_TCP_PORT 必须是整数") from exc
        if not 1 <= self.http_port <= 65535 or not 1 <= self.tcp_port <= 65535:
            raise ValueError("HUASHENG_HTTP_PORT/HUASHENG_TCP_PORT 必须在 1-65535 范围内")
        sdk_log_file = os.getenv("HUASHENG_TRADE_PUSH_LOG")
        self._open_api = OpenAPI(
            ip=self.ip,
            http_port=self.http_port,
            tcp_port=self.tcp_port,
            logging_filename=sdk_log_file,
        )
        self._subscribed = False

    @staticmethod
    def _is_success(response) -> bool:
        if not isinstance(response, dict) or not response.get("ok"):
            return False
        data = response.get("data")
        if isinstance(data, dict) and "success" in data:
            return bool(data["success"])
        return True

    def start(self) -> bool:
        """启动 TCP 长连接并订阅交易推送。"""
        try:
            # SDK 文档要求在 start 前注册回调，避免启动后刚好到达的消息丢失。
            self._open_api.add_notify_callback(self._on_notify)
            self._open_api.start()
            if not self._open_api.is_alive():
                logger.error(
                    "华盛交易推送 SDK TCP 未连接: %s:%s",
                    self.ip,
                    self.tcp_port,
                )
                return False

            response = self._open_api.trade_subscribe()
            if not self._is_success(response):
                logger.error("订阅交易推送失败: %s", response)
                return False

            self._subscribed = True
            logger.info("华盛交易推送 SDK 已连接并完成订阅: %s:%s", self.ip, self.tcp_port)
            return True
        except Exception as exc:
            # TCP 推送不可用时轮询仍可工作，这里保持为单行告警，避免把
            # 预期的网关断线刷成一长串 traceback。
            logger.warning("启动华盛交易推送 SDK 失败: %s", exc)
            return False

    def stop(self) -> None:
        """取消订阅并关闭 SDK 长连接。"""
        try:
            if self._subscribed and self._open_api.is_alive():
                response = self._open_api.trade_unsubscribe()
                if not self._is_success(response):
                    logger.warning("取消交易推送订阅失败: %s", response)
        except Exception as exc:
            logger.warning("关闭交易推送订阅时异常: %s", exc)
        finally:
            self._subscribed = False
            try:
                self._open_api.stop()
            except Exception as exc:
                logger.warning("停止华盛交易推送 SDK 时异常: %s", exc)

    def _on_notify(self, pb_notify) -> None:
        """SDK 回调入口，只转发股票交易成交/委托状态推送。"""
        try:
            if pb_notify is None or pb_notify.notifyMsgType != TradeStockDeliverMsgType:
                return
            payload = parse_payload(pb_notify)
            if payload is not None:
                self.callback(payload)
        except Exception as exc:
            # 回调线程不能因业务落盘异常退出；轮询兜底仍会继续工作。
            logger.exception("处理华盛交易推送消息失败: %s", exc)
