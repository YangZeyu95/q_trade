# QuantTrade Dashboard

The dashboard is the control surface for the trading system. Start the
Huasheng gateway, then start `web/backend/main.py` and this frontend. Log in
from the dashboard before starting either Dry Run or Live mode; the backend
starts and supervises the trading engine process.

Global risk controls are stored in `scripts/trading_config.json` and can be
changed from the dashboard. The trading engine reloads them each loop.

Trading is restricted to the US regular session, 09:30-16:00 in
`America/New_York`; pre-market and after-hours orders are skipped. The dashboard
reads `/api/market/status` for the current session state. The timezone library
automatically applies US daylight-saving and standard-time changes. Exchange
holiday and early-close calendars are not enabled yet.

Before enabling Live mode, an opt-in real gateway smoke test can be run:

```bash
RUN_LIVE_GATEWAY_TEST=1 python3 -m unittest scripts/test_gateway_live.py
```

It performs a read-only account query and fails when the local gateway is
offline or the trading session is no longer authenticated. The backend also
runs the same read-only check every 10 seconds by default (configurable with
`HUASHENG_HEARTBEAT_SECONDS`). A failed heartbeat clears the login state and
blocks new trading starts, but existing trading and manual-order processes get
a 60-second recovery grace period by default. Set
`HUASHENG_DISCONNECT_GRACE_SECONDS` to change it; after the grace period the
backend stops them if the gateway is still unavailable. The strategy process
also performs its own read-only health gate and skips strategy/order-status
work while the gateway is unavailable.

Pending order statuses are checked every 10 seconds by default, independently
of the 60-second strategy evaluation loop. This can be adjusted with
`ORDER_STATUS_CHECK_SECONDS` (values below 5 seconds are clamped to 5). The
dashboard refreshes trade history every 10 seconds so status changes appear
without a manual page refresh.

The backend owns a shared market snapshot. Holdings, account funds, batch
quotes, and strategy signals are fetched once per snapshot period and reused
by the dashboard and the separate strategy process. The default snapshot
period is 60 seconds (`MARKET_SNAPSHOT_SECONDS`). After a failed snapshot
refresh, consumers wait 180 seconds before retrying
(`MARKET_SNAPSHOT_RETRY_SECONDS`). The strategy confirms a
candidate stock's latest quote once immediately before submitting an order.
Fear/Greed scores default to a 1,800-second (30-minute) cache
(`SZDT_SIGNAL_CACHE_SECONDS`); this is a signal cache, not a real-time quote.

The dashboard's `实时订单` panel reads `/api/orders/realtime` every 2 seconds
and shows order status, filled quantity, remaining quantity, and latest price.

The `手动下单（限价）` panel is a one-shot test entry point. `Dry Run` only
writes the request to the engine log; `Live` requires the gateway login, a
second confirmation, and no running automatic engine. Manual orders are not
blocked by the local regular-session clock; the gateway may still reject or
handle an outside-session order according to its own rules.
The worker submits one limit order through the same `submit_order` and trade
recording path, listens for SDK updates, polls as a fallback, and automatically
cancels an order that remains open after the configured timeout. A gateway
heartbeat failure terminates the worker before another order can be submitted.

The trading engine also uses the official Huasheng Python SDK's TCP push for
order-status updates, with HTTP polling retained as a fallback. On this
machine it is installed with:

```bash
python3 -m pip install --no-deps \
  /Users/Zeyu/Downloads/py-hs-gateway-api-release-v2.3.0_20251023
```

`--no-deps` is intentional: this SDK ships old generated Protobuf files, so
the engine enables the SDK's pure-Python compatibility mode instead of
downgrading the environment's Protobuf 6 installation.

```bash
# from qlibx/src
python3 web/backend/main.py

# in another terminal
cd web/frontend
npm install
npm run dev
```

## Options analysis

The `Options Analysis` tab uses the official `hs` Python SDK exclusively. The
backend queries the HS Gateway for expiration dates, contract codes, underlying
quotes, option quotes, order-book Bid/Ask, open interest, IV, and Greeks. It
returns only the 12 contracts nearest spot on each side to keep the workbench
responsive while retaining the original HS contract code for a future order
ticket.

Gateway coordinates use the same environment variables as the trade-push
listener:

```bash
HUASHENG_GATEWAY_IP=127.0.0.1
HUASHENG_HTTP_PORT=11111
HUASHENG_TCP_PORT=11112
```

Adding a contract from the chain only adds a leg to the local payoff model; it
does not submit an order. US option legs use the standard 100-share contract
multiplier. Run the related checks with:

```bash
python3 -m unittest scripts/test_hs_option_api.py web/backend/test_main.py
cd web/frontend && npm test && npm run build
```

# React + Vite

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Babel](https://babeljs.io/) (or [oxc](https://oxc.rs) when used in [rolldown-vite](https://vite.dev/guide/rolldown)) for Fast Refresh
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/) for Fast Refresh

## React Compiler

The React Compiler is currently not compatible with SWC. See [this issue](https://github.com/vitejs/vite-plugin-react/issues/428) for tracking the progress.

## Expanding the ESLint configuration

If you are developing a production application, we recommend using TypeScript with type-aware lint rules enabled. Check out the [TS template](https://github.com/vitejs/vite/tree/main/packages/create-vite/template-react-ts) for information on how to integrate TypeScript and [`typescript-eslint`](https://typescript-eslint.io) in your project.
