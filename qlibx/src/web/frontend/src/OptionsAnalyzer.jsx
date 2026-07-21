import React, { useMemo, useState } from 'react';
import axios from 'axios';
import {
  ArrowLeft, ChevronDown, ChevronUp, Copy, Plus, RefreshCw,
  RotateCcw, Save, Search, Trash2, X,
} from 'lucide-react';
import {
  buildPayoffCurve,
  netEntryCashFlow,
  payoffMetrics,
  strategyPositions,
  totalPayoff,
} from './optionMath';

const API_BASE = 'http://localhost:8000/api';
const SAVED_STRATEGIES_KEY = 'qlibx_option_strategies_v1';

const TEMPLATE_LABELS = {
  covered_call: '备兑看涨',
  protective_put: '保护性看跌',
  bull_call: '牛市看涨价差',
  bear_put: '熊市看跌价差',
  straddle: '买入跨式',
  strangle: '买入宽跨式',
  iron_condor: '铁鹰',
  butterfly: '蝶式',
  collar: '领口策略',
};

const STRATEGY_TEMPLATES = Object.entries(TEMPLATE_LABELS);

const readSavedStrategies = () => {
  try {
    const parsed = JSON.parse(localStorage.getItem(SAVED_STRATEGIES_KEY) || '[]');
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
};

const money = (value, signed = false) => {
  if (value === Infinity || value === -Infinity) return '无限';
  const number = Number(value || 0);
  const sign = signed && number > 0 ? '+' : '';
  return `${sign}$${number.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};

const formatExpiration = (value) => String(value || '').replaceAll('/', '-');

const daysToExpiration = (value) => {
  if (!value) return null;
  const expiry = new Date(`${formatExpiration(value)}T23:59:59`);
  return Math.max(0, Math.ceil((expiry.getTime() - Date.now()) / 86400000));
};

const quotePrice = (quotes, source, fallback = 0) => {
  const value = Number(quotes?.[source]);
  if (Number.isFinite(value) && value > 0) return value;
  for (const key of ['mid', 'ask', 'bid', 'last']) {
    const candidate = Number(quotes?.[key]);
    if (Number.isFinite(candidate) && candidate > 0) return candidate;
  }
  return Number(fallback) || 0;
};

const preferredSource = (direction, quotes) => {
  if (direction === 'long' && Number(quotes?.ask) > 0) return 'ask';
  if (direction === 'short' && Number(quotes?.bid) > 0) return 'bid';
  return Number(quotes?.mid) > 0 ? 'mid' : 'last';
};

const recognizeStrategy = (positions) => {
  const options = positions.filter((leg) => leg.type !== 'stock');
  const stocks = positions.filter((leg) => leg.type === 'stock');
  const signature = options.map((leg) => `${leg.direction[0]}-${leg.type[0]}`).sort().join('|');
  if (stocks.some((leg) => leg.direction === 'long') && signature === 's-c') return '备兑看涨';
  if (stocks.some((leg) => leg.direction === 'long') && signature === 'l-p') return '保护性看跌';
  if (stocks.some((leg) => leg.direction === 'long') && signature === 'l-p|s-c') return '领口策略';
  if (signature === 'l-c|s-c') return '看涨价差';
  if (signature === 'l-p|s-p') return '看跌价差';
  if (signature === 'l-c|l-p') {
    return options[0]?.strike === options[1]?.strike ? '买入跨式' : '买入宽跨式';
  }
  if (signature === 'l-c|l-p|s-c|s-p') return '铁鹰 / 铁蝶';
  if (options.length === 3 && options.every((leg) => leg.type === 'call')) return '看涨蝶式';
  return positions.length ? '自定义组合' : '空组合';
};

function PayoffChart({ curve, spotPrice, breakevens }) {
  const width = 940;
  const height = 390;
  const padding = { left: 76, right: 28, top: 24, bottom: 50 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const maxPrice = curve.at(-1)?.price || 1;
  const pnlValues = curve.map((point) => point.pnl);
  const rawMin = Math.min(0, ...pnlValues);
  const rawMax = Math.max(0, ...pnlValues);
  const pnlPadding = Math.max(1, (rawMax - rawMin) * 0.08);
  const minPnl = rawMin - pnlPadding;
  const maxPnl = rawMax + pnlPadding;
  const x = (price) => padding.left + (price / maxPrice) * plotWidth;
  const y = (pnl) => padding.top + (maxPnl - pnl) / (maxPnl - minPnl) * plotHeight;
  const path = curve.map((point, index) => `${index ? 'L' : 'M'} ${x(point.price)} ${y(point.pnl)}`).join(' ');
  const zeroY = y(0);
  const xTicks = Array.from({ length: 6 }, (_, index) => maxPrice * index / 5);
  const yTicks = Array.from({ length: 5 }, (_, index) => minPnl + (maxPnl - minPnl) * index / 4);

  return (
    <div className="option-chart-wrap">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="期权组合到期损益图">
        <defs>
          <linearGradient id="optionPayoffFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#6366f1" stopOpacity="0.34" />
            <stop offset="100%" stopColor="#6366f1" stopOpacity="0.02" />
          </linearGradient>
        </defs>
        {yTicks.map((tick) => (
          <g key={tick}>
            <line x1={padding.left} x2={width - padding.right} y1={y(tick)} y2={y(tick)} className="option-grid-line" />
            <text x={padding.left - 10} y={y(tick) + 4} textAnchor="end" className="option-axis-label">{money(tick)}</text>
          </g>
        ))}
        {xTicks.map((tick) => (
          <g key={tick}>
            <line x1={x(tick)} x2={x(tick)} y1={padding.top} y2={height - padding.bottom} className="option-grid-line" />
            <text x={x(tick)} y={height - 23} textAnchor="middle" className="option-axis-label">${tick.toFixed(0)}</text>
          </g>
        ))}
        <line x1={padding.left} x2={width - padding.right} y1={zeroY} y2={zeroY} className="option-zero-line" />
        {spotPrice > 0 && spotPrice <= maxPrice && (
          <g>
            <line x1={x(spotPrice)} x2={x(spotPrice)} y1={padding.top} y2={height - padding.bottom} className="option-spot-line" />
            <text x={x(spotPrice) + 5} y={padding.top + 13} className="option-spot-label">现价 ${spotPrice.toFixed(2)}</text>
          </g>
        )}
        {breakevens.map((price) => price <= maxPrice && (
          <g key={price}>
            <line x1={x(price)} x2={x(price)} y1={padding.top} y2={height - padding.bottom} className="option-breakeven-line" />
            <text x={x(price) + 4} y={height - padding.bottom - 7} className="option-be-label">BE ${price.toFixed(2)}</text>
          </g>
        ))}
        <path d={`${path} L ${x(maxPrice)} ${zeroY} L ${x(0)} ${zeroY} Z`} fill="url(#optionPayoffFill)" />
        <path d={path} className="option-payoff-line" />
        <text x={width / 2} y={height - 2} textAnchor="middle" className="option-axis-title">到期时标的价格</text>
      </svg>
    </div>
  );
}

function PriceButton({ value, side, onClick, disabled }) {
  return (
    <button
      type="button"
      className={`chain-price-button ${side}`}
      disabled={disabled || !(Number(value) > 0)}
      onClick={onClick}
      title={side === 'buy' ? '按 Ask 加入买入腿' : '按 Bid 加入卖出腿'}
    >
      {Number(value) > 0 ? Number(value).toFixed(2) : '-'}
    </button>
  );
}

function OptionChain({ chain, spotPrice, onAdd }) {
  const rows = useMemo(() => {
    const byStrike = new Map();
    for (const call of chain?.calls || []) byStrike.set(Number(call.strike), { strike: Number(call.strike), call });
    for (const put of chain?.puts || []) {
      const strike = Number(put.strike);
      byStrike.set(strike, { ...(byStrike.get(strike) || { strike }), put });
    }
    return [...byStrike.values()].sort((a, b) => a.strike - b.strike);
  }, [chain]);

  const iv = (contract) => contract?.implied_volatility
    ? `${(Number(contract.implied_volatility) * (contract.implied_volatility <= 3 ? 100 : 1)).toFixed(1)}%`
    : '-';

  return (
    <div className="option-chain-container">
      <div className="chain-legend">
        <span><i className="buy-dot" /> 点击 Ask 买入</span>
        <span><i className="sell-dot" /> 点击 Bid 卖出</span>
        <span>仅加入分析，不会下单</span>
      </div>
      <div className="option-chain-scroll">
        <table className="unified-option-chain">
          <thead>
            <tr className="chain-side-header"><th colSpan="6">CALLS 看涨</th><th>STRIKE</th><th colSpan="6">PUTS 看跌</th></tr>
            <tr>
              <th>Delta</th><th>IV</th><th>OI</th><th>Vol</th><th>Bid</th><th>Ask</th>
              <th>行权价</th>
              <th>Bid</th><th>Ask</th><th>Vol</th><th>OI</th><th>IV</th><th>Delta</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ strike, call, put }) => {
              const nearAtm = Math.abs(strike - spotPrice) === Math.min(...rows.map((row) => Math.abs(row.strike - spotPrice)));
              return (
                <tr key={strike} className={nearAtm ? 'atm-row' : ''}>
                  <td>{call ? Number(call.delta || 0).toFixed(3) : '-'}</td>
                  <td>{iv(call)}</td><td>{call ? Number(call.open_interest || 0).toLocaleString() : '-'}</td>
                  <td>{call ? Number(call.volume || 0).toLocaleString() : '-'}</td>
                  <td><PriceButton value={call?.bid} side="sell" disabled={!call} onClick={() => onAdd(call, 'short', 'bid')} /></td>
                  <td><PriceButton value={call?.ask} side="buy" disabled={!call} onClick={() => onAdd(call, 'long', 'ask')} /></td>
                  <td className="strike-cell">${strike.toFixed(2)}{nearAtm && <small>ATM</small>}</td>
                  <td><PriceButton value={put?.bid} side="sell" disabled={!put} onClick={() => onAdd(put, 'short', 'bid')} /></td>
                  <td><PriceButton value={put?.ask} side="buy" disabled={!put} onClick={() => onAdd(put, 'long', 'ask')} /></td>
                  <td>{put ? Number(put.volume || 0).toLocaleString() : '-'}</td>
                  <td>{put ? Number(put.open_interest || 0).toLocaleString() : '-'}</td>
                  <td>{iv(put)}</td><td>{put ? Number(put.delta || 0).toFixed(3) : '-'}</td>
                </tr>
              );
            })}
            {!rows.length && <tr><td colSpan="13" className="option-empty-cell">查询标的后显示期权链</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function OptionsAnalyzer({ onBack }) {
  const [symbol, setSymbol] = useState('AAPL');
  const [spotPrice, setSpotPrice] = useState(0);
  const [chain, setChain] = useState(null);
  const [positions, setPositions] = useState([]);
  const [activeTemplate, setActiveTemplate] = useState('');
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('输入标的代码，通过 HS SDK 查询期权链');
  const [feePerContract, setFeePerContract] = useState(0.65);
  const [savedStrategies, setSavedStrategies] = useState(readSavedStrategies);
  const [saveName, setSaveName] = useState('');
  const [analysisView, setAnalysisView] = useState('payoff');

  const curve = useMemo(() => buildPayoffCurve(positions, spotPrice), [positions, spotPrice]);
  const metrics = useMemo(() => payoffMetrics(positions, spotPrice), [positions, spotPrice]);
  const netCashFlow = useMemo(() => netEntryCashFlow(positions), [positions]);
  const totalFees = positions.reduce((sum, leg) => sum + Number(leg.fees || 0), 0);
  const capitalAtRisk = Number.isFinite(metrics.maxLoss) ? Math.max(0, -metrics.maxLoss) : null;
  const maxReturn = capitalAtRisk > 0 && Number.isFinite(metrics.maxProfit)
    ? metrics.maxProfit / capitalAtRisk * 100
    : null;
  const strategyType = recognizeStrategy(positions);

  const portfolioGreeks = useMemo(() => positions.reduce((total, leg) => {
    const sign = leg.direction === 'short' ? -1 : 1;
    const multiplier = leg.type === 'stock' ? Number(leg.quantity || 0) : Number(leg.quantity || 0) * 100;
    if (leg.type === 'stock') total.delta += sign * multiplier;
    else {
      for (const greek of ['delta', 'gamma', 'theta', 'vega']) {
        total[greek] += sign * Number(leg[greek] || 0) * multiplier;
      }
    }
    return total;
  }, { delta: 0, gamma: 0, theta: 0, vega: 0 }), [positions]);

  const scenarios = useMemo(() => [-20, -10, -5, 0, 5, 10, 20].map((change) => {
    const price = spotPrice * (1 + change / 100);
    return { change, price, pnl: totalPayoff(positions, price, spotPrice) };
  }), [positions, spotPrice]);

  const fetchChain = async (expiration = '') => {
    const normalized = symbol.trim().toUpperCase();
    if (!normalized) return;
    setLoading(true);
    setMessage('正在读取 HS 期权链和买卖盘…');
    try {
      const response = await axios.get(`${API_BASE}/options/chain/${encodeURIComponent(normalized)}`, {
        params: expiration ? { expiration } : {},
      });
      const nextChain = response.data;
      const nextSpot = Number(nextChain.market_price);
      setChain(nextChain);
      setSymbol(nextChain.symbol || normalized);
      if (nextSpot > 0) setSpotPrice(nextSpot);
      setMessage(`HS SDK · ${nextChain.calls.length} Call / ${nextChain.puts.length} Put · ${nextChain.quote_time || '行情时间未知'}`);
    } catch (error) {
      setMessage(error.response?.data?.detail || 'HS SDK 查询失败，请检查 Gateway 和期权行情权限');
    } finally {
      setLoading(false);
    }
  };

  const contractToLeg = (contract, direction, source) => {
    const quotes = { bid: contract.bid, ask: contract.ask, mid: contract.mid, last: contract.last };
    const priceSource = source || preferredSource(direction, quotes);
    return {
      type: contract.option_type,
      direction,
      strike: Number(contract.strike),
      premium: quotePrice(quotes, priceSource, contract.last),
      quantity: 1,
      expiry: chain?.expiration || contract.expiration,
      contractSymbol: contract.contract_symbol,
      priceSource,
      quotes,
      fees: Number(feePerContract),
      delta: Number(contract.delta || 0), gamma: Number(contract.gamma || 0),
      theta: Number(contract.theta || 0), vega: Number(contract.vega || 0),
      impliedVolatility: Number(contract.implied_volatility || 0),
    };
  };

  const addContract = (contract, direction, source) => {
    if (!contract) return;
    setPositions((current) => [...current, contractToLeg(contract, direction, source)]);
    setActiveTemplate('');
  };

  const loadTemplate = (name) => {
    if (!chain || spotPrice <= 0) return;
    const template = strategyPositions(name, spotPrice, chain);
    if (!template.length) {
      setMessage(`当前 ${chain.expiration} 近价链不足，无法生成结构完整的${TEMPLATE_LABELS[name]}`);
      return;
    }
    const hydrated = template.map((leg) => {
      if (leg.type === 'stock') return { ...leg, fees: 0 };
      return {
        ...contractToLeg(leg.contract, leg.direction),
        quantity: leg.quantity,
        fees: feePerContract * leg.quantity,
      };
    });
    setPositions(hydrated);
    setActiveTemplate(name);
    setMessage(`${TEMPLATE_LABELS[name]}：已按当前真实行权价${hydrated.some((leg) => Math.abs(Number(leg.delta || 0)) > 0.001) ? '和 Delta' : ''}选腿`);
  };

  const updateLeg = (index, patch) => {
    setPositions((current) => current.map((leg, itemIndex) => {
      if (itemIndex !== index) return leg;
      const updated = { ...leg, ...patch };
      if ('quantity' in patch) updated.fees = Math.max(0, Number(patch.quantity) || 0) * feePerContract;
      if ('priceSource' in patch) updated.premium = quotePrice(updated.quotes, patch.priceSource, updated.premium);
      if ('direction' in patch && updated.priceSource !== 'manual') {
        updated.priceSource = preferredSource(patch.direction, updated.quotes);
        updated.premium = quotePrice(updated.quotes, updated.priceSource, updated.premium);
      }
      return updated;
    }));
    setActiveTemplate('');
  };

  const moveLeg = (index, direction) => {
    setPositions((current) => {
      const target = index + direction;
      if (target < 0 || target >= current.length) return current;
      const next = [...current];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  };

  const reverseStrategy = () => {
    setPositions((current) => current.map((leg) => {
      const direction = leg.direction === 'long' ? 'short' : 'long';
      const priceSource = leg.type === 'stock' ? leg.priceSource : preferredSource(direction, leg.quotes);
      return { ...leg, direction, priceSource, premium: leg.type === 'stock' ? leg.premium : quotePrice(leg.quotes, priceSource, leg.premium) };
    }));
    setActiveTemplate('');
  };

  const duplicateStrategy = () => {
    setPositions((current) => current.map((leg) => ({ ...leg, quantity: Number(leg.quantity || 0) * 2, fees: Number(leg.fees || 0) * 2 })));
  };

  const persistSaved = (next) => {
    setSavedStrategies(next);
    localStorage.setItem(SAVED_STRATEGIES_KEY, JSON.stringify(next));
  };

  const saveStrategy = () => {
    if (!positions.length) return setMessage('组合为空，无法保存');
    const name = saveName.trim() || `${symbol} ${strategyType}`;
    const record = { id: `${Date.now()}`, name, symbol, spotPrice, positions, savedAt: new Date().toISOString() };
    persistSaved([record, ...savedStrategies].slice(0, 30));
    setSaveName('');
    setMessage(`已保存策略：${name}`);
  };

  const loadSaved = (record) => {
    setSymbol(record.symbol);
    setSpotPrice(Number(record.spotPrice));
    setPositions(record.positions || []);
    setActiveTemplate('');
    setMessage(`已载入：${record.name}`);
  };

  const onFeeChange = (value) => {
    const fee = Math.max(0, Number(value) || 0);
    setFeePerContract(fee);
    setPositions((current) => current.map((leg) => ({ ...leg, fees: leg.type === 'stock' ? Number(leg.fees || 0) : fee * Number(leg.quantity || 0) })));
  };

  return (
    <div className="options-page-shell">
      <header className="options-page-header">
        <div className="options-brand">
          <button type="button" className="option-back-button" onClick={onBack}><ArrowLeft size={19} /></button>
          <div><h1>Options Lab</h1><p>HS SDK 期权策略工作台</p></div>
        </div>
        <form className="option-search-form" onSubmit={(event) => { event.preventDefault(); fetchChain(); }}>
          <Search size={18} />
          <input value={symbol} onChange={(event) => setSymbol(event.target.value.toUpperCase())} placeholder="输入标的，如 AAPL" maxLength="20" />
          <button type="submit" disabled={loading}>{loading ? <RefreshCw className="spin" size={17} /> : '查询'}</button>
        </form>
        <div className="option-quote-summary">
          <span>{chain?.name || symbol}</span>
          <strong>{chain ? `$${spotPrice.toFixed(2)}` : '--'}</strong>
          <em className={(chain?.market_change || 0) >= 0 ? 'positive' : 'negative'}>
            {chain ? `${chain.market_change >= 0 ? '+' : ''}${Number(chain.market_change).toFixed(2)} (${Number(chain.market_change_percent).toFixed(2)}%)` : '--'}
          </em>
        </div>
        <div className="read-only-badge">分析模式 · 不会下单</div>
      </header>

      <div className="option-status-line">
        <span className={message.includes('失败') ? 'error' : ''}>{message}</span>
        <button type="button" onClick={() => fetchChain(chain?.expiration)} disabled={loading || !chain}><RefreshCw size={14} /> 刷新买卖盘</button>
      </div>

      <nav className="expiration-tabs">
        {(chain?.expirations || []).map((expiration) => {
          const dte = daysToExpiration(expiration);
          return <button type="button" key={expiration} className={chain.expiration === expiration ? 'active' : ''} onClick={() => fetchChain(expiration)} disabled={loading}><strong>{formatExpiration(expiration).slice(5)}</strong><small>{dte} DTE</small></button>;
        })}
        {!chain && <span>查询后可按到期日快速切换</span>}
      </nav>

      <main className="options-terminal-grid">
        <section className="chain-workbench">
          <div className="workbench-title">
            <div><h2>{symbol} 期权链</h2><span>{chain ? `${formatExpiration(chain.expiration)} · 每侧近价 ${chain.calls.length} 个 · 全链 ${chain.total_call_contracts || '-'} / ${chain.total_put_contracts || '-'}` : '等待查询'}</span></div>
            <div className="template-strip">
              {STRATEGY_TEMPLATES.map(([name, label]) => <button type="button" key={name} disabled={!chain} className={activeTemplate === name ? 'active' : ''} onClick={() => loadTemplate(name)}>{label}</button>)}
            </div>
          </div>
          <OptionChain chain={chain} spotPrice={spotPrice} onAdd={addContract} />
        </section>

        <aside className="strategy-builder">
          <div className="builder-header">
            <div><h2>{strategyType}</h2><span>{positions.length} 条腿 · {symbol}</span></div>
            <div className="builder-icon-actions">
              <button type="button" title="数量加倍" onClick={duplicateStrategy}><Copy size={15} /></button>
              <button type="button" title="反转方向" onClick={reverseStrategy}><RotateCcw size={15} /></button>
              <button type="button" title="清空" onClick={() => { setPositions([]); setActiveTemplate(''); }}><Trash2 size={15} /></button>
            </div>
          </div>

          <div className="strategy-legs">
            {positions.map((leg, index) => (
              <div className={`strategy-leg ${leg.type}`} key={`${leg.contractSymbol || leg.type}-${index}`}>
                <div className="leg-main-row">
                  <div className="leg-reorder"><button type="button" onClick={() => moveLeg(index, -1)}><ChevronUp size={13} /></button><button type="button" onClick={() => moveLeg(index, 1)}><ChevronDown size={13} /></button></div>
                  <select value={leg.direction} onChange={(event) => updateLeg(index, { direction: event.target.value })}><option value="long">买入</option><option value="short">卖出</option></select>
                  <strong>{leg.type === 'stock' ? 'STOCK' : `${leg.type.toUpperCase()} $${Number(leg.strike).toFixed(2)}`}</strong>
                  <input aria-label="数量" type="number" min="1" step="1" value={leg.quantity} onChange={(event) => updateLeg(index, { quantity: event.target.value })} />
                  <button type="button" className="remove-leg" onClick={() => setPositions((current) => current.filter((_, itemIndex) => itemIndex !== index))}><X size={14} /></button>
                </div>
                <div className="leg-price-row">
                  {leg.type === 'stock' ? <><span>建仓价</span><input type="number" step="any" value={leg.entryPrice} onChange={(event) => updateLeg(index, { entryPrice: Number(event.target.value) })} /></> : <>
                    <select value={leg.priceSource || 'manual'} onChange={(event) => updateLeg(index, { priceSource: event.target.value })}><option value="ask">Ask</option><option value="mid">Mid</option><option value="bid">Bid</option><option value="last">Last</option><option value="manual">手动</option></select>
                    <span>权利金</span><input type="number" min="0" step="0.01" value={leg.premium} onChange={(event) => updateLeg(index, { premium: Number(event.target.value), priceSource: 'manual' })} />
                    <span className="leg-expiry">{formatExpiration(leg.expiry)}</span>
                  </>}
                </div>
                {leg.contractSymbol && <code title={leg.contractSymbol}>{leg.contractSymbol}</code>}
              </div>
            ))}
            {!positions.length && <div className="builder-empty"><Plus size={24} /><strong>从期权链添加合约</strong><span>点击 Ask 买入，点击 Bid 卖出</span></div>}
          </div>

          <button type="button" className="add-stock-leg" disabled={spotPrice <= 0} onClick={() => setPositions((current) => [...current, { type: 'stock', direction: 'long', quantity: 100, entryPrice: spotPrice, fees: 0 }])}><Plus size={15} /> 添加正股腿</button>

          <div className="builder-cost-settings">
            <label>每张手续费<input type="number" min="0" step="0.01" value={feePerContract} onChange={(event) => onFeeChange(event.target.value)} /></label>
            <span>组合手续费 {money(totalFees)}</span>
          </div>

          <div className="save-strategy-row">
            <input value={saveName} onChange={(event) => setSaveName(event.target.value)} placeholder="策略名称（可选）" />
            <button type="button" onClick={saveStrategy}><Save size={15} /> 保存</button>
          </div>
          {savedStrategies.length > 0 && <div className="saved-strategies"><span>已保存</span>{savedStrategies.slice(0, 5).map((record) => <div key={record.id}><button type="button" onClick={() => loadSaved(record)}><strong>{record.name}</strong><small>{record.symbol} · {new Date(record.savedAt).toLocaleDateString()}</small></button><button type="button" className="delete-saved" onClick={() => persistSaved(savedStrategies.filter((item) => item.id !== record.id))}><X size={13} /></button></div>)}</div>}
        </aside>
      </main>

      <section className="option-analysis-dock">
        <div className="analysis-tabs"><button type="button" className={analysisView === 'payoff' ? 'active' : ''} onClick={() => setAnalysisView('payoff')}>到期损益</button><button type="button" className={analysisView === 'scenario' ? 'active' : ''} onClick={() => setAnalysisView('scenario')}>情景分析</button><button type="button" className={analysisView === 'greeks' ? 'active' : ''} onClick={() => setAnalysisView('greeks')}>组合 Greeks</button></div>
        <div className="analysis-metrics-strip">
          <div><span>净权利金 / 现金流</span><strong className={netCashFlow >= 0 ? 'positive' : 'negative'}>{money(netCashFlow, true)}</strong></div>
          <div><span>最大盈利</span><strong className="positive">{money(metrics.maxProfit)}</strong></div>
          <div><span>最大亏损</span><strong className="negative">{metrics.maxLoss === -Infinity ? '无限' : money(metrics.maxLoss)}</strong></div>
          <div><span>资本占用估算</span><strong>{capitalAtRisk === null ? '需保证金模型' : money(capitalAtRisk)}</strong></div>
          <div><span>最大收益率</span><strong>{maxReturn === null ? '-' : `${maxReturn.toFixed(1)}%`}</strong></div>
          <div><span>盈亏平衡</span><strong>{metrics.breakevens.length ? metrics.breakevens.map((value) => `$${value.toFixed(2)}`).join(' / ') : '-'}</strong></div>
        </div>
        {analysisView === 'payoff' && <PayoffChart curve={curve} spotPrice={spotPrice} breakevens={metrics.breakevens} />}
        {analysisView === 'scenario' && <div className="scenario-grid">{scenarios.map((scenario) => <div key={scenario.change}><span>标的 {scenario.change > 0 ? '+' : ''}{scenario.change}%</span><strong>${scenario.price.toFixed(2)}</strong><em className={scenario.pnl >= 0 ? 'positive' : 'negative'}>{money(scenario.pnl, true)}</em></div>)}</div>}
        {analysisView === 'greeks' && <div className="greeks-grid">{Object.entries(portfolioGreeks).map(([name, value]) => <div key={name}><span>组合 {name.toUpperCase()}</span><strong className={value >= 0 ? 'positive' : 'negative'}>{value >= 0 ? '+' : ''}{value.toFixed(name === 'delta' ? 2 : 3)}</strong></div>)}</div>}
        <p className="analysis-disclaimer">资本占用为到期最大亏损估算；裸卖期权需要使用券商实时保证金模型。行情与 Greeks 以 HS SDK 返回为准。</p>
      </section>
    </div>
  );
}
