"""Execute exactly one manually requested order.

This process deliberately does not run the strategy loop. It uses the same
TradingStrategy.submit_order/record_trade path as the automated strategy and
starts the SDK push listener in the same process, so the order can be tested
end-to-end without starting automatic trading.
"""

import argparse
import csv
import logging
import math
import os
import time

from huasheng_api import HuashengGatewayAPI
from tqqq_trading_bot import TradingStrategy
from hs_trade_push import TradePushListener


logger = logging.getLogger(__name__)


TERMINAL_STATUSES = {
    "Filled",
    "Canceled",
    "Cancelled",
    "Partially Cancelled",
    "Rejected",
    "Review Failed",
}


def _is_terminal_status(status):
    normalized = " ".join(str(status or "").strip().lower().split())
    return normalized in {item.lower() for item in TERMINAL_STATUSES}


def _order_status(strategy, symbol, order_id):
    path = os.path.join(strategy.trades_dir, f"{symbol.lower()}_trading.csv")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if str(row.get("order_id") or "").strip() == str(order_id):
                    return row.get("status")
    except Exception:
        return None
    return None


def _validate_manual_order(strategy, api, symbol, side, quantity, price):
    try:
        quote = api.get_realtime_quote(symbol, strategy.data_type)
    except Exception as exc:
        logger.warning("读取 %s 行情失败: %s", symbol, exc)
        return f"无法获取 {symbol} 实时行情，拒绝手动下单"
    if not quote:
        return f"无法获取 {symbol} 实时行情，拒绝手动下单"

    if side == "sell":
        try:
            available = api.get_stock_position_qty(symbol, strategy.exchange_type)
        except Exception as exc:
            logger.warning("读取 %s 可卖持仓失败: %s", symbol, exc)
            return f"无法获取 {symbol} 可卖持仓，拒绝手动卖出"
        if quantity > available:
            return f"卖出数量 {quantity} 超过可卖数量 {available}"
        return None

    risk_metrics = strategy._get_account_risk_metrics()
    if not risk_metrics:
        return "无法获取账户风控数据，拒绝手动买入"

    order_value = quantity * price
    try:
        current_quantity = api.get_stock_position_qty(symbol, strategy.exchange_type)
    except Exception as exc:
        logger.warning("读取 %s 当前持仓失败: %s", symbol, exc)
        return f"无法获取 {symbol} 当前持仓，拒绝手动买入"
    current_value = current_quantity * price
    account_equity = risk_metrics["equity"]
    gross_market_value = max(risk_metrics["gross_market_value"], current_value)
    projected_gross_value = gross_market_value + order_value
    projected_leverage = projected_gross_value / account_equity
    projected_margin_ratio = (
        account_equity / projected_gross_value
        if projected_gross_value > 0 else float("inf")
    )

    if projected_leverage > strategy.max_total_leverage:
        return (
            f"预计总杠杆 {projected_leverage:.2f}x 超过上限 "
            f"{strategy.max_total_leverage:.2f}x"
        )
    if projected_margin_ratio < strategy.min_maintenance_margin_ratio:
        return (
            f"预计保证金比例 {projected_margin_ratio:.2%} 低于最低要求 "
            f"{strategy.min_maintenance_margin_ratio:.2%}"
        )
    if order_value > risk_metrics["buying_power"]:
        return (
            f"订单金额 ${order_value:.2f} 超过可用购买力 "
            f"${risk_metrics['buying_power']:.2f}"
        )
    return None


def run(args):
    api = HuashengGatewayAPI()
    strategy = TradingStrategy(api)
    symbol = args.symbol.strip().upper()
    side = args.side.strip().lower()
    price = float(args.limit_price)

    if not symbol or any(
        not (char.isascii() and (char.isalnum() or char in ".-_"))
        for char in symbol
    ):
        logger.error("手动下单被拒绝: 股票代码包含非法字符")
        return 2
    if not math.isfinite(price) or price <= 0:
        logger.error("手动下单被拒绝: 限价必须是有限的正数")
        return 2

    if args.mode == "dry_run":
        logger.info(
            f"[DRY RUN] Would submit {side.upper()} {args.quantity} "
            f"{symbol} at ${price:.2f}"
        )
        return 0

    if not api.check_connection(exchange_type=strategy.exchange_type):
        logger.error("网关只读探活失败，拒绝真实手动下单")
        return 3

    validation_error = _validate_manual_order(
        strategy, api, symbol, side, args.quantity, price
    )
    if validation_error:
        logger.error("手动下单被拒绝: %s", validation_error)
        return 2

    listener = TradePushListener(strategy.handle_trade_push)
    if not listener.start():
        logger.error("交易推送 SDK 启动失败，拒绝手动下单")
        listener.stop()
        return 3

    try:
        if not api.check_connection(exchange_type=strategy.exchange_type):
            logger.error("提交前网关只读探活失败，拒绝真实手动下单")
            return 3
        quote = api.get_realtime_quote(symbol, strategy.data_type)
        volume = quote.get("volume", 0) if quote else 0
        result = strategy.submit_order(
            symbol=symbol,
            action=side,
            quantity=args.quantity,
            price=args.limit_price,
            volume=volume,
            entrust_type="3",
        )
        if result is None:
            logger.error("网关拒绝或未返回委托结果，停止等待并人工核对网关订单")
            return 4
        order_id = HuashengGatewayAPI.extract_order_id(result)
        if not order_id:
            logger.error("网关返回委托结果但没有解析到委托号，停止等待并人工核对网关订单")
            return 4

        deadline = time.monotonic() + args.auto_cancel_seconds
        status = _order_status(strategy, symbol, order_id)
        while not _is_terminal_status(status) and time.monotonic() < deadline:
            # SDK 推送通常会先更新；轮询用于推送短暂不可用时的兜底。
            strategy.update_pending_orders()
            status = _order_status(strategy, symbol, order_id)
            if _is_terminal_status(status):
                break
            time.sleep(1)

        # A final SDK push can arrive immediately after the timeout loop.
        # Re-read the CSV before sending a second cancel request.
        status = _order_status(strategy, symbol, order_id)
        if not _is_terminal_status(status):
            cancel_result = api.cancel_order(
                order_id=order_id,
                stock_code=symbol,
                exchange_type=strategy.exchange_type,
                entrust_amount=args.quantity,
                entrust_price=args.limit_price,
                entrust_type="3",
            )
            if cancel_result is None:
                status = _order_status(strategy, symbol, order_id)
                if _is_terminal_status(status):
                    logger.info(
                        "订单 %s 在自动撤单请求返回前已由推送更新为终态 %s",
                        order_id,
                        status,
                    )
                    return 0
                logger.error("订单 %s 未成交且自动撤单请求失败，请立即人工核对", order_id)
                return 5

            cancel_deadline = time.monotonic() + 15
            while time.monotonic() < cancel_deadline:
                strategy.update_pending_orders()
                status = _order_status(strategy, symbol, order_id)
                if _is_terminal_status(status):
                    break
                time.sleep(1)

            if not _is_terminal_status(status):
                logger.error(
                    "订单 %s 已请求撤单，但 15 秒内未确认终态，请立即人工核对",
                    order_id,
                )
                return 6

        logger.info("manual order %s: %s", order_id, status or "Submitted")
        return 0
    finally:
        listener.stop()


def main():
    parser = argparse.ArgumentParser(description="Submit one controlled manual order")
    parser.add_argument("--mode", choices=("dry_run", "live"), default="dry_run")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--side", choices=("buy", "sell"), required=True)
    parser.add_argument("--quantity", type=int, required=True)
    parser.add_argument("--limit-price", required=True)
    parser.add_argument("--auto-cancel-seconds", type=int, default=60)
    args = parser.parse_args()

    if args.quantity <= 0 or args.quantity > 1_000_000:
        parser.error("quantity must be between 1 and 1000000")
    try:
        parsed_price = float(args.limit_price)
    except (TypeError, ValueError):
        parser.error("limit-price must be a number")
    if not math.isfinite(parsed_price) or parsed_price <= 0:
        parser.error("limit-price must be a finite number greater than 0")
    if args.mode == "live" and not 10 <= args.auto_cancel_seconds <= 300:
        parser.error("live auto-cancel-seconds must be between 10 and 300")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
