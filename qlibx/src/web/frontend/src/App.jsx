import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { Plus, Edit2, Trash2, History, TrendingUp, DollarSign, Activity, X, List, LayoutGrid, Play, Square, Save, LogIn, RefreshCw, ShieldCheck, ArrowUpDown, ArrowUp, ArrowDown } from 'lucide-react';
import OptionsAnalyzer from './OptionsAnalyzer';
import { DEFAULT_ACCOUNT, normalizeAccount } from './dashboardData.js';

const API_BASE = 'http://localhost:8000/api';
const DEFAULT_RISK_CONFIG = {
  max_total_leverage: 2.0,
  min_maintenance_margin_ratio: 0.30,
  szdt_auth_key_configured: false,
  szdt_auth_key_source: 'unset'
};

const formatMarketTime = (value) => value
  ? new Date(value).toLocaleString('zh-CN', {
      timeZone: 'America/New_York',
      hour12: false
    })
  : '-';

const normalizeSymbol = (symbol) => String(symbol || '')
  .toUpperCase()
  .replace(/^US\./, '')
  .replace(/\.US$/, '')
  .replace(/^HK\./, '')
  .replace(/\.HK$/, '');

const getRealtimeForHolding = (stockCode, realtimeData) => {
  const target = normalizeSymbol(stockCode);
  const match = Object.entries(realtimeData).find(([symbol]) => normalizeSymbol(symbol) === target);
  return match ? match[1] : { signal: null, signal_available: false };
};

const toSortableNumber = (value) => {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
};

const getHoldingSortValue = (holding, key, realtimeData) => {
  switch (key) {
    case 'symbol':
      return String(holding.stockCode || '');
    case 'name':
      return String(holding.stockName || '');
    case 'quantity':
      return toSortableNumber(holding.enableAmount || holding.canSellAmount || 0);
    case 'costPrice':
      return toSortableNumber(holding.costPrice);
    case 'currentPrice':
      return toSortableNumber(holding.lastPrice);
    case 'marketValue':
      return toSortableNumber(holding.marketValue);
    case 'weight':
      return toSortableNumber(holding.weight);
    case 'pnl':
      return toSortableNumber(holding.incomeBalance || 0);
    case 'signal': {
      const realtime = getRealtimeForHolding(holding.stockCode, realtimeData);
      return realtime.signal_available ? toSortableNumber(realtime.signal) : null;
    }
    default:
      return null;
  }
};

function App() {
  const [strategies, setStrategies] = useState({});
  const [history, setHistory] = useState([]);
  const [realtimeOrders, setRealtimeOrders] = useState({
    orders: [],
    active_count: 0,
    updated_at: null
  });
  const [realtimeData, setRealtimeData] = useState({});
  const [holdings, setHoldings] = useState([]);
  const [holdingSort, setHoldingSort] = useState({ key: 'symbol', direction: 'asc' });
  const [indicator, setIndicator] = useState({ value: 0, available: false, name: 'Signal Indicator' });
  const [account, setAccount] = useState(() => ({ ...DEFAULT_ACCOUNT }));
  const [runtimeStatus, setRuntimeStatus] = useState({
    state: 'stopped',
    mode: null,
    pid: null,
    gateway_logged_in: false,
    config: DEFAULT_RISK_CONFIG
  });
  const [authStatus, setAuthStatus] = useState({
    logged_in: false,
    connection_state: 'not_logged_in',
    last_heartbeat_at: null,
    last_error: null
  });
  const [marketStatus, setMarketStatus] = useState({
    is_open: false,
    session: 'closed',
    timezone: 'America/New_York',
    extended_hours_enabled: false
  });
  const [riskConfig, setRiskConfig] = useState(DEFAULT_RISK_CONFIG);
  const [riskDraft, setRiskDraft] = useState(DEFAULT_RISK_CONFIG);
  const [manualOrderDraft, setManualOrderDraft] = useState({
    symbol: '',
    side: 'buy',
    quantity: 1,
    limit_price: '',
    mode: 'dry_run',
    auto_cancel_seconds: 60
  });
  const [manualOrderStatus, setManualOrderStatus] = useState({ state: 'idle' });
  const riskDraftTouched = useRef(false);
  const szdtAuthKeyTouched = useRef(false);
  const [tradingLogs, setTradingLogs] = useState([]);
  const [loginPassword, setLoginPassword] = useState('');
  const [systemMessage, setSystemMessage] = useState('');
  const [systemBusy, setSystemBusy] = useState(false);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingSymbol, setEditingSymbol] = useState(null);
  const [viewMode, setViewMode] = useState('grid'); // 'grid' or 'table'
  const [activeTab, setActiveTab] = useState(() => window.location.hash === '#options' ? 'options' : 'strategies');
  const [formData, setFormData] = useState({
    symbol: '',
    name: '',
    buy_point: 0,
    sell_point: 0,
    buy_total: 700,
    sell_total: 700,
    buy_limit_price: 0,
    sell_limit_price: 0,
    buy_day_interval: 10,
    sell_day_interval: 10,
    buy_price_interval: 10,
    max_position: 10,
    fear_greed_buy: -60,
    fear_greed_sell: 60,
    lever: '3',
    emo_area: 'us'
  });

  const fetchHistory = React.useCallback(async () => {
    try {
      const histRes = await axios.get(`${API_BASE}/all_history`);
      setHistory(histRes.data);
    } catch (err) {
      console.error('Failed to fetch trade history', err);
    }
  }, []);

  const fetchRealtimeOrders = React.useCallback(async () => {
    try {
      const ordersRes = await axios.get(`${API_BASE}/orders/realtime?limit=100`);
      setRealtimeOrders(ordersRes.data || { orders: [], active_count: 0, updated_at: null });
    } catch (err) {
      console.error('Failed to fetch realtime orders', err);
    }
  }, []);

  const fetchManualOrderStatus = React.useCallback(async () => {
    try {
      const statusRes = await axios.get(`${API_BASE}/orders/manual/status`);
      setManualOrderStatus(statusRes.data || { state: 'idle' });
    } catch (err) {
      console.error('Failed to fetch manual order status', err);
    }
  }, []);

  const fetchRealtime = React.useCallback(async () => {
    try {
      const realRes = await axios.get(`${API_BASE}/realtime`);
      setRealtimeData(realRes.data);
      const indRes = await axios.get(`${API_BASE}/indicator`);
      setIndicator(indRes.data);
      const holdRes = await axios.get(`${API_BASE}/holdings`);
      setHoldings(Array.isArray(holdRes.data) ? holdRes.data : []);
      const accRes = await axios.get(`${API_BASE}/account`);
      setAccount(normalizeAccount(accRes.data));
    } catch (err) {
      console.error('Failed to fetch realtime data', err);
    }
  }, []);

  const fetchSystem = React.useCallback(async () => {
    try {
      const [statusRes, configRes, authRes, logsRes, marketRes] = await Promise.all([
        axios.get(`${API_BASE}/trading/status`),
        axios.get(`${API_BASE}/trading/config`),
        axios.get(`${API_BASE}/auth/status`),
        axios.get(`${API_BASE}/trading/logs?limit=120`),
        axios.get(`${API_BASE}/market/status`)
      ]);
      const config = configRes.data || DEFAULT_RISK_CONFIG;
      setRuntimeStatus(statusRes.data);
      setManualOrderStatus(statusRes.data?.manual_order || { state: 'idle' });
      setRiskConfig(config);
      if (!riskDraftTouched.current) setRiskDraft({ ...config, szdt_auth_key: '' });
      setAuthStatus(authRes.data);
      setTradingLogs(logsRes.data?.lines || []);
      setMarketStatus(marketRes.data);
    } catch (err) {
      console.error('Failed to fetch trading system status', err);
    }
  }, []);

  const fetchData = React.useCallback(async () => {
    try {
      const stratRes = await axios.get(`${API_BASE}/strategies`);
      setStrategies(stratRes.data);
      fetchHistory();
      fetchRealtimeOrders();
      fetchManualOrderStatus();
      fetchRealtime();
      fetchSystem();
    } catch (err) {
      console.error('Failed to fetch data', err);
    }
  }, [fetchHistory, fetchManualOrderStatus, fetchRealtime, fetchRealtimeOrders, fetchSystem]);

  useEffect(() => {
    fetchData();
    const interval = setInterval(() => {
      fetchRealtime();
      fetchHistory();
      fetchSystem();
    }, 10000);
    return () => clearInterval(interval);
  }, [fetchData, fetchHistory, fetchRealtime, fetchSystem]);

  useEffect(() => {
    // SDK 推送先更新本地订单文件，前端只需轻量读取即可看到最新状态。
    const interval = setInterval(() => {
      fetchRealtimeOrders();
      fetchManualOrderStatus();
    }, 2000);
    return () => clearInterval(interval);
  }, [fetchManualOrderStatus, fetchRealtimeOrders]);

  const handleLogin = async (e) => {
    e.preventDefault();
    setSystemBusy(true);
    setSystemMessage('');
    try {
      await axios.post(`${API_BASE}/auth/login`, { password: loginPassword });
      setLoginPassword('');
      setSystemMessage('Huasheng 登录成功');
      await fetchSystem();
      await fetchRealtime();
    } catch (err) {
      setSystemMessage(err.response?.data?.detail || 'Huasheng 登录失败');
    } finally {
      setSystemBusy(false);
    }
  };

  const handleStartTrading = async (mode) => {
    if (mode === 'live' && !window.confirm('即将启动真实交易，后端将提交真实订单。确认继续吗？')) return;
    setSystemBusy(true);
    setSystemMessage('');
    try {
      await axios.post(`${API_BASE}/trading/start`, { mode });
      setSystemMessage(mode === 'live' ? '真实交易引擎已启动' : 'Dry Run 引擎已启动');
      await fetchSystem();
    } catch (err) {
      setSystemMessage(err.response?.data?.detail || '启动交易引擎失败');
    } finally {
      setSystemBusy(false);
    }
  };

  const handleStopTrading = async () => {
    setSystemBusy(true);
    try {
      await axios.post(`${API_BASE}/trading/stop`);
      setSystemMessage('交易引擎已停止');
      await fetchSystem();
    } catch (err) {
      setSystemMessage(err.response?.data?.detail || '停止交易引擎失败');
    } finally {
      setSystemBusy(false);
    }
  };

  const handleManualOrder = async (e) => {
    e.preventDefault();
    const symbol = String(manualOrderDraft.symbol || '').trim().toUpperCase();
    const quantity = Number(manualOrderDraft.quantity);
    const limitPrice = Number(manualOrderDraft.limit_price);
    if (!symbol || !Number.isInteger(quantity) || quantity <= 0 || !Number.isFinite(limitPrice) || limitPrice <= 0) {
      setSystemMessage('请填写有效的股票代码、数量和限价');
      return;
    }
    if (manualOrderDraft.mode === 'live') {
      const sideText = manualOrderDraft.side === 'buy' ? '买入' : '卖出';
      const confirmed = window.confirm(
        `即将真实${sideText} ${quantity} 股 ${symbol}，限价 $${limitPrice.toFixed(2)}。\n\n` +
        '这会提交真实订单，确认继续吗？'
      );
      if (!confirmed) return;
    }

    setSystemBusy(true);
    setSystemMessage('正在提交手动订单请求…');
    try {
      const res = await axios.post(`${API_BASE}/orders/manual`, {
        symbol,
        side: manualOrderDraft.side,
        quantity,
        limit_price: limitPrice,
        mode: manualOrderDraft.mode,
        auto_cancel_seconds: Number(manualOrderDraft.auto_cancel_seconds),
        confirm_live: manualOrderDraft.mode === 'live'
      });
      setManualOrderStatus(res.data.manual_order || { state: 'running' });
      setSystemMessage('手动订单已提交，状态会在实时订单区域更新');
      await fetchRealtimeOrders();
    } catch (err) {
      setSystemMessage(err.response?.data?.detail || '手动订单提交失败');
    } finally {
      setSystemBusy(false);
    }
  };

  const handleRiskDraftChange = (field, value) => {
    riskDraftTouched.current = true;
    if (field === 'szdt_auth_key') szdtAuthKeyTouched.current = true;
    setRiskDraft(prev => ({ ...prev, [field]: value }));
  };

  const handleSaveRiskConfig = async () => {
    setSystemBusy(true);
    setSystemMessage('');
    try {
      const payload = {
        max_total_leverage: Number(riskDraft.max_total_leverage),
        min_maintenance_margin_ratio: Number(riskDraft.min_maintenance_margin_ratio)
      };
      if (szdtAuthKeyTouched.current) {
        payload.szdt_auth_key = String(riskDraft.szdt_auth_key || '').trim();
      }
      const res = await axios.put(`${API_BASE}/trading/config`, payload);
      const saved = res.data.config;
      setRiskConfig(saved);
      setRiskDraft({ ...saved, szdt_auth_key: '' });
      riskDraftTouched.current = false;
      szdtAuthKeyTouched.current = false;
      setSystemMessage('风控配置已保存，交易引擎下一轮检查时生效');
      await fetchSystem();
    } catch (err) {
      setSystemMessage(err.response?.data?.detail || '风控配置保存失败');
    } finally {
      setSystemBusy(false);
    }
  };

  const [loadingName, setLoadingName] = useState(false);

  const handleSymbolBlur = async (e) => {
    const symbol = e.target.value.toUpperCase();
    if (symbol.length >= 1) {
      setLoadingName(true);
      try {
        const res = await axios.get(`${API_BASE}/stock_info/${symbol}`);
        if (res.data.name && res.data.name !== symbol) {
          setFormData(prev => ({ ...prev, name: res.data.name }));
        } else if (!formData.name) {
          setFormData(prev => ({ ...prev, name: symbol }));
        }
      } catch (err) {
        console.error('Failed to fetch stock name', err);
      } finally {
        setLoadingName(false);
      }
    }
  };

  const handleImportHolding = (hold) => {
    const symbol = hold.stockCode.split('.')[0].toUpperCase();
    setFormData({
      symbol: symbol,
      name: hold.stockName || symbol,
      buy_point: parseFloat(hold.lastPrice) || 0,
      sell_point: parseFloat(hold.lastPrice) * 1.1 || 0,
      buy_total: 700,
      sell_total: 700,
      buy_limit_price: 0,
      sell_limit_price: 0,
      buy_day_interval: 10,
      sell_day_interval: 10,
      buy_price_interval: 10,
      max_position: 10,
      fear_greed_buy: -60,
      fear_greed_sell: 60,
      lever: '3',
      emo_area: 'us'
    });
    setEditingSymbol(null);
    setActiveTab('strategies');
    setIsModalOpen(true);
  };

  const handleOpenModal = (symbol = null) => {
    if (symbol) {
      setEditingSymbol(symbol);
      setFormData({ 
        symbol, 
        lever: '3', 
        emo_area: 'us',
        ...strategies[symbol] 
      });
    } else {
      setEditingSymbol(null);
      setFormData({
        symbol: '',
        name: '',
        buy_point: 0,
        sell_point: 0,
        buy_total: 700,
        sell_total: 700,
        buy_limit_price: 0,
        sell_limit_price: 0,
        buy_day_interval: 10,
        sell_day_interval: 10,
        buy_price_interval: 10,
        max_position: 10,
        fear_greed_buy: -60,
        fear_greed_sell: 60,
        lever: '3',
        emo_area: 'us'
      });
    }
    setIsModalOpen(true);
  };

  const handleDelete = async (symbol) => {
    if (window.confirm(`Delete strategy for ${symbol}?`)) {
      try {
        await axios.delete(`${API_BASE}/strategies/${symbol}`);
        fetchData();
      } catch {
        alert('Delete failed');
      }
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    const { symbol, ...data } = formData;
    try {
      await axios.post(`${API_BASE}/strategies/${symbol}`, data);
      setIsModalOpen(false);
      fetchData();
    } catch {
      alert('Save failed');
    }
  };

  const toggleHoldingSort = (key) => {
    setHoldingSort((current) => ({
      key,
      direction: current.key === key && current.direction === 'asc' ? 'desc' : 'asc'
    }));
  };

  const sortedHoldings = React.useMemo(() => {
    return [...holdings].sort((a, b) => {
      const aValue = getHoldingSortValue(a, holdingSort.key, realtimeData);
      const bValue = getHoldingSortValue(b, holdingSort.key, realtimeData);

      // Keep unavailable numeric values (for example, missing Fear & Greed)
      // at the bottom in both directions.
      if (aValue === null && bValue === null) return 0;
      if (aValue === null) return 1;
      if (bValue === null) return -1;

      const comparison = typeof aValue === 'string' && typeof bValue === 'string'
        ? aValue.localeCompare(bValue, undefined, { numeric: true, sensitivity: 'base' })
        : aValue - bValue;
      return holdingSort.direction === 'asc' ? comparison : -comparison;
    });
  }, [holdings, holdingSort, realtimeData]);

  const renderHoldingSortHeader = (key, label) => {
    const active = holdingSort.key === key;
    const sortIcon = active
      ? (holdingSort.direction === 'asc' ? <ArrowUp size={14} /> : <ArrowDown size={14} />)
      : <ArrowUpDown size={14} />;
    return (
      <th key={key} aria-sort={active ? `${holdingSort.direction}ending` : 'none'}>
        <button
          type="button"
          className={`table-sort-button${active ? ' active' : ''}`}
          onClick={() => toggleHoldingSort(key)}
        >
          <span>{label}</span>
          {sortIcon}
        </button>
      </th>
    );
  };

  if (activeTab === 'options') {
    return <OptionsAnalyzer onBack={() => { window.location.hash = ''; setActiveTab('strategies'); }} />;
  }

  return (
    <div className="app-container">
      <header>
        <div>
          <h1>QuantTrade Dashboard</h1>
          <p style={{ color: 'var(--text-muted)', marginTop: '0.5rem' }}>Full control over your qlibx trading strategies</p>
        </div>
        <div style={{ display: 'flex', gap: '1rem', alignItems: 'center' }}>
          <div className="indicator-badge" style={{
            background: 'var(--card-bg)',
            padding: '0.5rem 1rem',
            borderRadius: '0.75rem',
            border: '1px solid var(--glass-border)',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center'
          }}>
            <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{indicator.name}</span>
            <span style={{ 
              fontSize: '1.25rem', 
              fontWeight: 700, 
              color: indicator.value > 0 ? 'var(--danger)' : 'var(--success)' 
            }}>
              {indicator.available ? Number(indicator.value).toFixed(1) : 'N/A'}
            </span>
          </div>
          <button className="btn-secondary" onClick={() => { window.location.hash = 'options'; setActiveTab('options'); }}>
            <TrendingUp size={20} /> 期权分析
          </button>
          <button className="btn-primary" onClick={() => handleOpenModal()}>
            <Plus size={20} /> Add Stock
          </button>
        </div>
      </header>

      <section className="system-control-panel animate-fade-in">
        <div className="system-panel-header">
          <div>
            <h2 style={{ margin: 0 }}>交易系统控制中心</h2>
            <p style={{ color: 'var(--text-muted)', marginTop: '0.35rem' }}>
              后端统一管理登录、交易引擎、风控配置和运行日志
            </p>
          </div>
          <div className={`runtime-status ${runtimeStatus.state === 'running' ? 'is-running' : 'is-stopped'}`}>
            <span className="status-dot" />
            {runtimeStatus.state === 'running'
              ? `${runtimeStatus.mode === 'live' ? 'LIVE' : 'DRY RUN'} 运行中`
              : '交易引擎已停止'}
          </div>
          <div className={`market-status ${marketStatus.is_open ? 'is-open' : 'is-closed'}`}>
            <span className="status-dot" />
            美股常规盘：{marketStatus.is_open ? '盘中' : '休市'}
            <small>
              {formatMarketTime(marketStatus.local_time)} ET
            </small>
          </div>
        </div>

        <div className="system-control-grid">
          <div className="system-control-card">
            <div className="card-label"><LogIn size={16} /> Huasheng 网关</div>
            {authStatus.logged_in ? (
              <div className="connection-ok">
                <ShieldCheck size={18} /> 已登录，可启动交易引擎
                {authStatus.login_at && <small>{new Date(authStatus.login_at).toLocaleString()}</small>}
                {authStatus.last_heartbeat_at && (
                  <small>最近探活：{new Date(authStatus.last_heartbeat_at).toLocaleString()}</small>
                )}
              </div>
            ) : (
              <>
                {authStatus.last_error && (
                  <div className="connection-error">网关不可用：{authStatus.last_error}</div>
                )}
                <form onSubmit={handleLogin} className="login-form">
                  <input
                    type="password"
                    value={loginPassword}
                    onChange={(e) => setLoginPassword(e.target.value)}
                    placeholder="Huasheng 交易密码"
                    required
                  />
                  <button className="btn-primary" type="submit" disabled={systemBusy}>
                    <LogIn size={16} /> 登录
                  </button>
                </form>
              </>
            )}
          </div>

          <div className="system-control-card">
            <div className="card-label"><Activity size={16} /> 引擎操作</div>
            <div className="engine-meta">
              <span>PID: {runtimeStatus.pid || '-'}</span>
              <span>模式: {runtimeStatus.mode || '-'}</span>
            </div>
            <div className="engine-actions">
              <button
                className="btn-secondary"
                onClick={() => handleStartTrading('dry_run')}
                disabled={!authStatus.logged_in || runtimeStatus.state === 'running' || systemBusy}
              >
                <Play size={16} /> 启动 Dry Run
              </button>
              <button
                className="btn-danger"
                onClick={() => handleStartTrading('live')}
                disabled={!authStatus.logged_in || runtimeStatus.state === 'running' || systemBusy}
              >
                <Play size={16} /> 启动 Live
              </button>
              <button
                className="btn-secondary"
                onClick={handleStopTrading}
                disabled={runtimeStatus.state !== 'running' || systemBusy}
              >
                <Square size={16} /> 停止
              </button>
            </div>
          </div>

          <div className="system-control-card">
            <div className="card-label"><ShieldCheck size={16} /> 全局风控配置</div>
            <div className="risk-form-grid">
              <label>
                总杠杆上限（倍）
                <input
                  type="number"
                  min="0.1"
                  step="any"
                  inputMode="decimal"
                  value={riskDraft.max_total_leverage}
                  onChange={(e) => handleRiskDraftChange('max_total_leverage', e.target.value)}
                />
              </label>
              <label>
                最低保证金比例（%）
                <input
                  type="number"
                  min="1"
                  max="100"
                  step="1"
                  value={(Number(riskDraft.min_maintenance_margin_ratio) * 100).toFixed(1)}
                  onChange={(e) => handleRiskDraftChange('min_maintenance_margin_ratio', Number(e.target.value) / 100)}
                />
              </label>
              <label className="risk-key-field">
                SZDT Auth Key（可选）
                <input
                  type="password"
                  value={riskDraft.szdt_auth_key || ''}
                  placeholder={riskConfig.szdt_auth_key_configured ? '已配置，输入新值可覆盖' : '未配置'}
                  autoComplete="new-password"
                  onChange={(e) => handleRiskDraftChange('szdt_auth_key', e.target.value)}
                />
              </label>
            </div>
            <div className="risk-effective">
              当前生效：{Number(riskConfig.max_total_leverage).toFixed(1)}x / {(Number(riskConfig.min_maintenance_margin_ratio) * 100).toFixed(1)}%
              <span className="auth-key-status">
                贪恐指数：{riskConfig.szdt_auth_key_configured ? `已配置（${riskConfig.szdt_auth_key_source}）` : '未配置'}
              </span>
              <button className="btn-secondary compact-button" onClick={handleSaveRiskConfig} disabled={systemBusy}>
                <Save size={15} /> 保存
              </button>
            </div>
          </div>

          <div className="system-control-card">
            <div className="card-label"><Activity size={16} /> 手动下单（限价）</div>
            <p style={{ color: 'var(--text-muted)', fontSize: '0.78rem', margin: '0 0 0.75rem' }}>
              只执行一笔，不启动自动策略循环；真实下单前会二次确认。
            </p>
            <form onSubmit={handleManualOrder}>
              <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 0.8fr', gap: '0.6rem' }}>
                <label>
                  股票代码
                  <input
                    value={manualOrderDraft.symbol}
                    onChange={(e) => setManualOrderDraft(prev => ({ ...prev, symbol: e.target.value.toUpperCase() }))}
                    placeholder="例如 TQQQ"
                    required
                  />
                </label>
                <label>
                  方向
                  <select
                    value={manualOrderDraft.side}
                    onChange={(e) => setManualOrderDraft(prev => ({ ...prev, side: e.target.value }))}
                    style={{ background: 'var(--card-bg)', color: 'white', padding: '0.55rem', borderRadius: '0.5rem', border: '1px solid var(--glass-border)', width: '100%' }}
                  >
                    <option value="buy">买入</option>
                    <option value="sell">卖出</option>
                  </select>
                </label>
                <label>
                  数量
                  <input
                    type="number"
                    min="1"
                    step="1"
                    value={manualOrderDraft.quantity}
                    onChange={(e) => setManualOrderDraft(prev => ({ ...prev, quantity: e.target.value }))}
                    required
                  />
                </label>
                <label>
                  限价（美元）
                  <input
                  type="number"
                  min="0.0001"
                  step="any"
                  inputMode="decimal"
                  value={manualOrderDraft.limit_price}
                    onChange={(e) => setManualOrderDraft(prev => ({ ...prev, limit_price: e.target.value }))}
                    placeholder="必须大于 0"
                    required
                  />
                </label>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.6rem', marginTop: '0.6rem' }}>
                <label>
                  模式
                  <select
                    value={manualOrderDraft.mode}
                    onChange={(e) => setManualOrderDraft(prev => ({ ...prev, mode: e.target.value }))}
                    style={{ background: 'var(--card-bg)', color: 'white', padding: '0.55rem', borderRadius: '0.5rem', border: '1px solid var(--glass-border)', width: '100%' }}
                  >
                    <option value="dry_run">Dry Run（不下单）</option>
                    <option value="live">Live（真实下单）</option>
                  </select>
                </label>
                <label>
                  未成交自动撤单（秒）
                  <input
                    type="number"
                    min="10"
                    max="300"
                    step="1"
                    value={manualOrderDraft.auto_cancel_seconds}
                    onChange={(e) => setManualOrderDraft(prev => ({ ...prev, auto_cancel_seconds: e.target.value }))}
                    required
                  />
                </label>
              </div>
              <div className="risk-effective" style={{ marginTop: '0.75rem', justifyContent: 'space-between' }}>
                <span style={{ color: manualOrderStatus.state === 'running' ? 'var(--primary)' : 'var(--text-muted)' }}>
                  手动订单：{manualOrderStatus.state === 'running' ? '处理中' : manualOrderStatus.state === 'stopped' ? `已结束（${manualOrderStatus.exit_code ?? 0}）` : '空闲'}
                </span>
                <button
                  type="submit"
                  className={manualOrderDraft.mode === 'live' ? 'btn-danger' : 'btn-secondary'}
                  disabled={systemBusy || manualOrderStatus.state === 'running' || (manualOrderDraft.mode === 'live' && !authStatus.logged_in)}
                >
                  {manualOrderDraft.mode === 'live' ? '提交真实订单' : '模拟提交'}
                </button>
              </div>
            </form>
          </div>
        </div>

        {systemMessage && <div className="system-message">{systemMessage}</div>}

        <div className="log-panel">
          <div className="log-panel-header">
            <span>交易引擎日志</span>
            <button className="btn-icon-small" onClick={fetchSystem} title="刷新状态和日志">
              <RefreshCw size={16} />
            </button>
          </div>
          <pre className="log-viewer">{tradingLogs.length ? tradingLogs.slice(-80).join('') : '暂无交易日志'}</pre>
        </div>
      </section>

      <section className="account-summary" style={{ 
        display: 'grid', 
        gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', 
        gap: '1rem', 
        marginBottom: '2rem' 
      }}>
        <div className="stat-card" style={{ background: 'rgba(59, 130, 246, 0.1)', border: '1px solid rgba(59, 130, 246, 0.2)', padding: '1.25rem', borderRadius: '1rem' }}>
          <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem', marginBottom: '0.25rem' }}>Net Assets</div>
          <div style={{ fontSize: '1.5rem', fontWeight: 800 }}>${account.total_asset.toLocaleString(undefined, {minimumFractionDigits: 2})}</div>
        </div>
        <div className="stat-card" style={{ background: 'var(--card-bg)', padding: '1.25rem', borderRadius: '1rem', border: '1px solid var(--glass-border)' }}>
          <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem', marginBottom: '0.25rem' }}>Holdings Value</div>
          <div style={{ fontSize: '1.5rem', fontWeight: 700 }}>${account.market_value.toLocaleString(undefined, {minimumFractionDigits: 2})}</div>
        </div>
        <div className="stat-card" style={{ background: 'var(--card-bg)', padding: '1.25rem', borderRadius: '1rem', border: '1px solid var(--glass-border)' }}>
          <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem', marginBottom: '0.25rem' }}>Total Floating P&L</div>
          <div style={{ fontSize: '1.5rem', fontWeight: 800, color: account.total_pnl >= 0 ? 'var(--success)' : 'var(--danger)' }}>
            {account.total_pnl >= 0 ? '+' : ''}${account.total_pnl.toLocaleString(undefined, {minimumFractionDigits: 2})}
          </div>
        </div>
        <div className="stat-card" style={{ background: 'var(--card-bg)', padding: '1.25rem', borderRadius: '1rem', border: '1px solid var(--glass-border)' }}>
          <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem', marginBottom: '0.25rem' }}>Cash (USD)</div>
          <div style={{ fontSize: '1.5rem', fontWeight: 700, color: account.cash >= 0 ? 'var(--success)' : 'var(--danger)' }}>
            {account.cash < 0 ? '-' : ''}${Math.abs(account.cash).toLocaleString(undefined, {minimumFractionDigits: 2})}
          </div>
        </div>
        <div className="stat-card" style={{ background: 'var(--card-bg)', padding: '1.25rem', borderRadius: '1rem', border: '1px solid var(--glass-border)' }}>
          <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem', marginBottom: '0.25rem' }}>Buying Power</div>
          <div style={{ fontSize: '1.5rem', fontWeight: 700, color: 'var(--primary)' }}>${account.buying_power.toLocaleString(undefined, {minimumFractionDigits: 2})}</div>
        </div>
        <div className="stat-card" style={{ background: 'var(--card-bg)', padding: '1.25rem', borderRadius: '1rem', border: '1px solid var(--glass-border)' }}>
          <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem', marginBottom: '0.25rem' }}>Gross Leverage</div>
          <div style={{ fontSize: '1.5rem', fontWeight: 700, color: account.gross_leverage > account.max_total_leverage ? 'var(--danger)' : 'var(--primary)' }}>
            {Number(account.gross_leverage || 0).toFixed(2)}x
          </div>
          <small style={{ color: 'var(--text-muted)' }}>limit {Number(account.max_total_leverage || 0).toFixed(1)}x</small>
        </div>
        <div className="stat-card" style={{ background: 'var(--card-bg)', padding: '1.25rem', borderRadius: '1rem', border: '1px solid var(--glass-border)' }}>
          <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem', marginBottom: '0.25rem' }}>Margin Ratio</div>
          <div style={{ fontSize: '1.5rem', fontWeight: 700, color: account.maintenance_margin_ratio !== null && account.maintenance_margin_ratio < account.min_maintenance_margin_ratio ? 'var(--danger)' : 'var(--success)' }}>
            {account.maintenance_margin_ratio === null ? '-' : `${(Number(account.maintenance_margin_ratio) * 100).toFixed(1)}%`}
          </div>
          <small style={{ color: 'var(--text-muted)' }}>minimum {(Number(account.min_maintenance_margin_ratio || 0) * 100).toFixed(1)}%</small>
        </div>
      </section>

      <div className="tabs" style={{ 
        display: 'flex', 
        gap: '2rem', 
        marginBottom: '2rem', 
        borderBottom: '1px solid var(--glass-border)',
        padding: '0 1rem'
      }}>
        <button 
          onClick={() => setActiveTab('strategies')}
          style={{
            padding: '1rem 0.5rem',
            background: 'none',
            border: 'none',
            color: activeTab === 'strategies' ? 'var(--primary)' : 'var(--text-muted)',
            fontWeight: 600,
            cursor: 'pointer',
            borderBottom: activeTab === 'strategies' ? '2px solid var(--primary)' : 'none',
            fontSize: '1rem'
          }}
        >
          Trading Strategies
        </button>
        <button 
          onClick={() => setActiveTab('holdings')}
          style={{
            padding: '1rem 0.5rem',
            background: 'none',
            border: 'none',
            color: activeTab === 'holdings' ? 'var(--primary)' : 'var(--text-muted)',
            fontWeight: 600,
            cursor: 'pointer',
            borderBottom: activeTab === 'holdings' ? '2px solid var(--primary)' : 'none',
            fontSize: '1rem'
          }}
        >
          Real-time Holdings
        </button>
        <button
          onClick={() => setActiveTab('options')}
          style={{
            padding: '1rem 0.5rem',
            background: 'none',
            border: 'none',
            color: activeTab === 'options' ? 'var(--primary)' : 'var(--text-muted)',
            fontWeight: 600,
            cursor: 'pointer',
            borderBottom: activeTab === 'options' ? '2px solid var(--primary)' : 'none',
            fontSize: '1rem'
          }}
        >
          Options Analysis
        </button>
      </div>

      {activeTab === 'strategies' ? (
        <>
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '1rem' }}>
            <div className="view-toggle" style={{
              display: 'flex',
              background: 'var(--card-bg)',
              borderRadius: '0.75rem',
              padding: '0.25rem',
              border: '1px solid var(--glass-border)'
            }}>
              <button
                onClick={() => setViewMode('grid')}
                style={{
                  background: viewMode === 'grid' ? 'var(--primary)' : 'transparent',
                  border: 'none',
                  color: 'white',
                  padding: '0.5rem',
                  borderRadius: '0.5rem',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center'
                }}
              >
                <LayoutGrid size={18} />
              </button>
              <button
                onClick={() => setViewMode('table')}
                style={{
                  background: viewMode === 'table' ? 'var(--primary)' : 'transparent',
                  border: 'none',
                  color: 'white',
                  padding: '0.5rem',
                  borderRadius: '0.5rem',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center'
                }}
              >
                <List size={18} />
              </button>
            </div>
          </div>

          {viewMode === 'grid' ? (
            <div className="stock-grid">
              {Object.entries(strategies).map(([symbol, strat]) => {
                const real = realtimeData[symbol] || { lastPrice: 0, quantity: 0, value: 0 };
                return (
                  <div key={symbol} className="stock-card animate-fade-in">
                    <div className="stock-header">
                      <div>
                        <div className="stock-symbol">{symbol}</div>
                        <div className="stock-name">{strat.name}</div>
                      </div>
                      <div style={{ display: 'flex', gap: '0.5rem' }}>
                        <button onClick={() => handleOpenModal(symbol)} className="btn-icon">
                          <Edit2 size={18} />
                        </button>
                        <button onClick={() => handleDelete(symbol)} className="btn-icon danger">
                          <Trash2 size={18} />
                        </button>
                      </div>
                    </div>

                    <div className="realtime-info" style={{ 
                      margin: '1rem 0', 
                      padding: '1rem', 
                      background: 'rgba(255,255,255,0.05)', 
                      borderRadius: '0.5rem',
                      display: 'grid',
                      gridTemplateColumns: '1fr 1fr',
                      gap: '0.5rem'
                    }}>
                      <div>
                        <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Price</div>
                        <div style={{ fontSize: '1.25rem', fontWeight: 700 }}>${real.lastPrice.toFixed(2)}</div>
                      </div>
                      <div>
                        <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Holding / Equity %</div>
                        <div style={{ fontSize: '1.25rem', fontWeight: 700 }}>
                          {real.quantity}
                          <span style={{ fontSize: '0.8rem', color: 'var(--primary)', marginLeft: '0.5rem' }}>
                            ({real.weight ? real.weight.toFixed(1) : '0'}%)
                          </span>
                        </div>
                      </div>

                      <div style={{ marginTop: '0.5rem', borderTop: '1px solid rgba(255,255,255,0.1)', paddingTop: '0.5rem' }}>
                        <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Market Value</div>
                        <div style={{ fontSize: '1rem', fontWeight: 600 }}>${real.value.toLocaleString(undefined, {minimumFractionDigits: 2})}</div>
                      </div>
                      <div style={{ marginTop: '0.5rem', borderTop: '1px solid rgba(255,255,255,0.1)', paddingTop: '0.5rem', textAlign: 'right' }}>
                        <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Stock Signal</div>
                        <div style={{ 
                          fontSize: '1.1rem', 
                          fontWeight: 700,
                          color: real.signal > 0 ? 'var(--danger)' : 'var(--success)'
                        }}>
                          {real.signal_available ? Number(real.signal).toFixed(1) : 'N/A'}
                        </div>
                      </div>
                    </div>

                    <div className="strategy-stats" style={{ marginTop: '1.5rem' }}>
                      <div className="stat-item">
                        <span className="stat-label">Buy / Sell Point</span>
                        <span className="stat-value">
                          <span style={{ color: 'var(--success)' }}>${strat.buy_point}</span>
                          <span style={{ color: 'var(--text-muted)', margin: '0 0.5rem' }}>/</span>
                          <span style={{ color: 'var(--danger)' }}>${strat.sell_point}</span>
                        </span>
                      </div>
                      <div className="stat-item">
                        <span className="stat-label">Buy / Sell Total</span>
                        <span className="stat-value">${strat.buy_total} / <span style={{ color: strat.sell_total > 0 ? 'inherit' : 'var(--text-muted)' }}>${strat.sell_total || 0}</span></span>
                      </div>
                      <div className="stat-item" style={{ marginTop: '1rem' }}>
                        <span className="stat-label">Intervals (B/S)</span>
                        <span className="stat-value">{strat.buy_day_interval}d / {strat.sell_day_interval}d | {strat.buy_price_interval}%</span>
                      </div>
                      <div className="stat-item" style={{ marginTop: '1rem' }}>
                        <span className="stat-label">Max Pos</span>
                        <span className="stat-value">{strat.max_position}%</span>
                      </div>
                      <div className="stat-item" style={{ marginTop: '1rem' }}>
                        <span className="stat-label">Signal (B/S) | Area</span>
                        <span className="stat-value">{strat.fear_greed_buy} / {strat.fear_greed_sell} | {strat.emo_area}</span>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <section className="history-section animate-fade-in" style={{ marginBottom: '3rem', padding: '1rem' }}>
              <div style={{ overflowX: 'auto' }}>
                <table className="strategy-table">
                  <thead>
                    <tr>
                      <th>Symbol</th>
                      <th>Name</th>
                      <th>Price / Equity %</th>
                      <th>B/S Pt</th>
                      <th>B/S Tot</th>
                      <th>Intervals (B/S)</th>
                      <th>Max Pos</th>
                      <th>Signal (B/S)</th>
                      <th>Current Signal</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(strategies).map(([symbol, strat]) => {
                      const real = realtimeData[symbol] || { lastPrice: 0, weight: 0, signal: 0, signal_available: false };
                      return (
                        <tr key={symbol}>
                          <td style={{ fontWeight: 700, color: 'var(--primary)' }}>{symbol}</td>
                          <td style={{ fontSize: '0.875rem' }}>{strat.name}</td>
                          <td>
                            <div style={{ fontWeight: 600 }}>${real.lastPrice.toFixed(2)}</div>
                            <div style={{ fontSize: '0.75rem', color: 'var(--primary)' }}>{real.weight.toFixed(1)}%</div>
                          </td>
                          <td style={{ color: 'var(--success)' }}>${strat.buy_point} / <span style={{ color: 'var(--danger)' }}>${strat.sell_point}</span></td>
                          <td>${strat.buy_total} / <span style={{ color: strat.sell_total > 0 ? 'inherit' : 'var(--text-muted)' }}>${strat.sell_total || 0}</span></td>
                          <td style={{ fontSize: '0.8125rem' }}>{strat.buy_day_interval}d / {strat.sell_day_interval}d | {strat.buy_price_interval}%</td>
                          <td>{strat.max_position}%</td>
                          <td>{strat.fear_greed_buy} / {strat.fear_greed_sell}</td>
                          <td style={{ 
                            fontWeight: 700,
                            color: real.signal_available && real.signal > 0 ? 'var(--danger)' : 'var(--success)'
                          }}>
                            {real.signal_available ? Number(real.signal).toFixed(1) : 'N/A'}
                          </td>
                          <td>
                            <div style={{ display: 'flex', gap: '0.5rem' }}>
                              <button onClick={() => handleOpenModal(symbol)} className="btn-icon-small">
                                <Edit2 size={14} />
                              </button>
                              <button onClick={() => handleDelete(symbol)} className="btn-icon-small danger">
                                <Trash2 size={14} />
                              </button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </>
      ) : activeTab === 'holdings' ? (
        <section className="holdings-section animate-fade-in">
          <div style={{ overflowX: 'auto' }}>
            <table className="strategy-table">
              <thead>
                <tr>
                  {renderHoldingSortHeader('symbol', 'Symbol')}
                  {renderHoldingSortHeader('name', 'Name')}
                  {renderHoldingSortHeader('quantity', 'Quantity')}
                  {renderHoldingSortHeader('costPrice', 'Cost Price')}
                  {renderHoldingSortHeader('currentPrice', 'Current Price')}
                  {renderHoldingSortHeader('marketValue', 'Market Value')}
                  {renderHoldingSortHeader('weight', 'Equity %')}
                  {renderHoldingSortHeader('pnl', 'Floating P&L')}
                  {renderHoldingSortHeader('signal', 'Fear & Greed')}
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {sortedHoldings.length > 0 ? sortedHoldings.map((hold, i) => {
                  const income = parseFloat(hold.incomeBalance || 0);
                  const weight = hold.weight || 0;
                  const realtime = getRealtimeForHolding(hold.stockCode, realtimeData);
                  const signalAvailable = realtime.signal_available && realtime.signal !== null;
                  return (
                    <tr key={i}>
                      <td style={{ fontWeight: 700, color: 'var(--primary)' }}>{hold.stockCode}</td>
                      <td style={{ fontSize: '0.8125rem', maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={hold.stockName}>
                        {hold.stockName}
                      </td>
                      <td style={{ fontWeight: 600 }}>{hold.enableAmount || hold.canSellAmount}</td>
                      <td>${parseFloat(hold.costPrice).toFixed(3)}</td>
                      <td style={{ fontWeight: 600 }}>${parseFloat(hold.lastPrice).toFixed(3)}</td>
                      <td>${parseFloat(hold.marketValue).toLocaleString(undefined, {minimumFractionDigits: 2})}</td>
                      <td style={{ fontWeight: 600, color: 'var(--primary)' }}>{weight.toFixed(1)}%</td>
                      <td style={{ 
                        fontWeight: 700, 
                        color: income >= 0 ? 'var(--success)' : 'var(--danger)' 
                      }}>
                        {income >= 0 ? '+' : ''}{income.toFixed(2)}
                        <span style={{ fontSize: '0.75rem', marginLeft: '0.25rem' }}>
                          ({hold.incomeRatio})
                        </span>
                      </td>
                      <td style={{
                        fontWeight: 700,
                        color: signalAvailable
                          ? (realtime.signal > 0 ? 'var(--danger)' : 'var(--success)')
                          : 'var(--text-muted)'
                      }}>
                        {signalAvailable ? Number(realtime.signal).toFixed(1) : 'N/A'}
                      </td>
                      <td>
                        <button 
                          onClick={() => handleImportHolding(hold)}
                          className="btn-icon-small"
                          title="Import to Strategy"
                          style={{ background: 'var(--primary)', color: 'white' }}
                        >
                          <Plus size={14} />
                        </button>
                      </td>
                    </tr>
                  );
                }) : (
                  <tr>
                    <td colSpan="10" style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-muted)' }}>
                      No holdings found in the account.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      ) : (
        <OptionsAnalyzer />
      )}

      {/* Real-time Orders Section */}
      <section className="history-section animate-fade-in" style={{ marginTop: '3rem', padding: '1rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '1rem', marginBottom: '1.5rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <Activity size={24} className="text-primary" />
            <div>
              <h2 style={{ margin: 0 }}>实时订单</h2>
              <small style={{ color: 'var(--text-muted)' }}>
                SDK 推送优先，当前活动订单：{realtimeOrders.active_count || 0}
                {realtimeOrders.updated_at && ` · 更新于 ${new Date(realtimeOrders.updated_at).toLocaleTimeString()}`}
              </small>
            </div>
          </div>
          <button className="btn-icon-small" onClick={fetchRealtimeOrders} title="刷新实时订单">
            <RefreshCw size={16} />
          </button>
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table className="strategy-table">
            <thead>
              <tr>
                <th>更新时间</th>
                <th>Symbol</th>
                <th>方向</th>
                <th>订单号</th>
                <th>委托数量</th>
                <th>已成交</th>
                <th>剩余</th>
                <th>成交/委托价</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {(realtimeOrders.orders || []).length > 0 ? realtimeOrders.orders.map((item, i) => {
                const status = item.status || 'Unknown';
                const isFilled = status === 'Filled';
                const isActive = Boolean(item.is_active);
                return (
                  <tr key={`${item.order_id || item.record_no || 'order'}-${i}`}>
                    <td style={{ color: 'var(--text-muted)', fontSize: '0.8125rem' }}>
                      {item.last_updated ? new Date(item.last_updated).toLocaleString() : '-'}
                    </td>
                    <td style={{ fontWeight: 700 }}>{item.symbol || '-'}</td>
                    <td>
                      <span style={{ color: item.action === 'buy' ? 'var(--success)' : 'var(--danger)', fontWeight: 700 }}>
                        {(item.action || '-').toUpperCase()}
                      </span>
                    </td>
                    <td style={{ fontFamily: 'monospace', fontSize: '0.8rem' }}>{item.order_id || item.record_no || '-'}</td>
                    <td>{item.entrust_quantity || item.quantity || '-'}</td>
                    <td>{item.filled_quantity || '-'}</td>
                    <td>{item.remaining_quantity || (isFilled ? '0' : '-')}</td>
                    <td>${item.business_price || item.entrust_price || item.price || '-'}</td>
                    <td>
                      <span style={{
                        fontSize: '0.75rem',
                        padding: '0.2rem 0.5rem',
                        borderRadius: '0.25rem',
                        background: isFilled
                          ? 'rgba(34, 197, 94, 0.15)'
                          : isActive
                            ? 'rgba(59, 130, 246, 0.15)'
                            : 'rgba(255, 255, 255, 0.05)',
                        color: isFilled ? 'var(--success)' : isActive ? 'var(--primary)' : 'var(--text-muted)',
                        border: `1px solid ${isFilled ? 'var(--success)' : isActive ? 'var(--primary)' : 'var(--glass-border)'}`
                      }}>
                        {status}
                      </span>
                    </td>
                  </tr>
                );
              }) : (
                <tr>
                  <td colSpan="9" style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-muted)' }}>
                    暂无订单。下单后这里会实时显示委托和成交状态。
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {/* Recent Trade History Section */}
      <section className="history-section animate-fade-in" style={{ marginTop: '3rem', padding: '1rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1.5rem' }}>
          <History size={24} className="text-primary" />
          <h2 style={{ margin: 0 }}>Recent Trade History</h2>
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table className="strategy-table">
            <thead>
              <tr>
                <th>Timestamp</th>
                <th>Symbol</th>
                <th>Action</th>
                <th>Qty</th>
                <th>Price</th>
                <th>Status</th>
                <th>Total</th>
              </tr>
            </thead>
            <tbody>
              {history.length > 0 ? history.map((item, i) => (
                <tr key={i}>
                  <td style={{ color: 'var(--text-muted)', fontSize: '0.8125rem' }}>{new Date(item.timestamp).toLocaleString()}</td>
                  <td style={{ fontWeight: 700 }}>{item.symbol}</td>
                  <td>
                    <span className={`badge ${item.action === 'buy' ? 'badge-success' : 'badge-danger'}`} style={{
                      padding: '0.25rem 0.75rem',
                      borderRadius: '1rem',
                      fontSize: '0.75rem',
                      fontWeight: 700,
                      background: item.action === 'buy' ? 'rgba(34, 197, 94, 0.1)' : 'rgba(239, 68, 68, 0.1)',
                      color: item.action === 'buy' ? 'var(--success)' : 'var(--danger)'
                    }}>
                      {item.action.toUpperCase()}
                    </span>
                  </td>
                  <td>{item.quantity}</td>
                  <td>${parseFloat(item.price).toFixed(2)}</td>
                  <td>
                    <span style={{ 
                      fontSize: '0.75rem', 
                      padding: '0.2rem 0.5rem', 
                      borderRadius: '0.25rem',
                      background: item.status === 'Filled' ? 'rgba(34, 197, 94, 0.15)' : 'rgba(255, 255, 255, 0.05)',
                      color: item.status === 'Filled' ? 'var(--success)' : 'var(--text-muted)',
                      border: `1px solid ${item.status === 'Filled' ? 'var(--success)' : 'var(--glass-border)'}`
                    }}>
                      {item.status || 'Unknown'}
                    </span>
                  </td>
                  <td style={{ fontWeight: 600 }}>${(parseFloat(item.price) * parseInt(item.quantity)).toLocaleString(undefined, {minimumFractionDigits: 2})}</td>
                </tr>
              )) : (
                <tr>
                  <td colSpan="7" style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-muted)' }}>No trade history found.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {/* Strategy Modal */}
      {isModalOpen && (
        <div className="modal-overlay">
          <div className="modal-content animate-slide-up" style={{ maxWidth: '800px' }}>
            <div className="modal-header">
              <h2>{editingSymbol ? `Edit Strategy: ${editingSymbol}` : 'Add New Stock Strategy'}</h2>
              <button className="btn-icon" onClick={() => setIsModalOpen(false)}><X size={24} /></button>
            </div>
            
            <form onSubmit={handleSubmit}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.5fr', gap: '1rem' }}>
                <div className="form-group">
                  <label>Stock Symbol</label>
                  <input
                    disabled={!!editingSymbol}
                    value={formData.symbol}
                    onChange={(e) => setFormData({ ...formData, symbol: e.target.value.toUpperCase() })}
                    onBlur={handleSymbolBlur}
                    placeholder="e.g. TQQQ"
                    required
                  />
                </div>
                <div className="form-group">
                  <label>Name</label>
                  <input
                    value={loadingName ? 'Fetching name...' : formData.name}
                    onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                    placeholder="Auto-fetched or manual input"
                    style={{ background: loadingName ? 'rgba(255,255,255,0.05)' : 'var(--card-bg)' }}
                  />
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
                <div className="form-group">
                  <label>Buy Point (Price)</label>
                  <input
                    type="number" step="0.01"
                    value={formData.buy_point}
                    onChange={(e) => setFormData({ ...formData, buy_point: parseFloat(e.target.value) })}
                    required
                  />
                </div>
                <div className="form-group">
                  <label>Sell Point (Price)</label>
                  <input
                    type="number" step="0.01"
                    value={formData.sell_point}
                    onChange={(e) => setFormData({ ...formData, sell_point: parseFloat(e.target.value) })}
                    required
                  />
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
                <div className="form-group">
                  <label>Buy Total ($)</label>
                  <input
                    type="number"
                    value={formData.buy_total}
                    onChange={(e) => setFormData({ ...formData, buy_total: parseInt(e.target.value) })}
                    required
                  />
                </div>
                <div className="form-group">
                  <label>Sell Total ($) (must be greater than 0)</label>
                  <input
                    type="number"
                    min="1"
                    value={formData.sell_total}
                    onChange={(e) => setFormData({ ...formData, sell_total: parseInt(e.target.value) })}
                    required
                  />
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
                <div className="form-group">
                  <label>Buy Limit Price (0=Market)</label>
                  <input
                    type="number" step="0.01"
                    value={formData.buy_limit_price}
                    onChange={(e) => setFormData({ ...formData, buy_limit_price: parseFloat(e.target.value) })}
                    required
                  />
                </div>
                <div className="form-group">
                  <label>Sell Limit Price (0=Market)</label>
                  <input
                    type="number" step="0.01"
                    value={formData.sell_limit_price}
                    onChange={(e) => setFormData({ ...formData, sell_limit_price: parseFloat(e.target.value) })}
                    required
                  />
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: '1rem' }}>
                <div className="form-group">
                  <label>Buy Interval (d)</label>
                  <input
                    type="number"
                    value={formData.buy_day_interval}
                    onChange={(e) => setFormData({ ...formData, buy_day_interval: parseInt(e.target.value) })}
                    required
                  />
                </div>
                <div className="form-group">
                  <label>Sell Interval (d)</label>
                  <input
                    type="number"
                    value={formData.sell_day_interval}
                    onChange={(e) => setFormData({ ...formData, sell_day_interval: parseInt(e.target.value) })}
                    required
                  />
                </div>
                <div className="form-group">
                  <label>Price Intv (%)</label>
                  <input
                    type="number" step="0.1"
                    value={formData.buy_price_interval}
                    onChange={(e) => setFormData({ ...formData, buy_price_interval: parseFloat(e.target.value) })}
                    required
                  />
                </div>
                <div className="form-group">
                  <label>Max Pos (%)</label>
                  <input
                    type="number" step="0.1"
                    value={formData.max_position}
                    onChange={(e) => setFormData({ ...formData, max_position: parseFloat(e.target.value) })}
                    required
                  />
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
                <div className="form-group">
                  <label>Fear & Greed Buy Limit</label>
                  <input
                    type="number" step="0.1"
                    value={formData.fear_greed_buy}
                    onChange={(e) => setFormData({ ...formData, fear_greed_buy: parseFloat(e.target.value) })}
                    required
                  />
                </div>
                <div className="form-group">
                  <label>Fear & Greed Sell Limit</label>
                  <input
                    type="number" step="0.1"
                    value={formData.fear_greed_sell}
                    onChange={(e) => setFormData({ ...formData, fear_greed_sell: parseFloat(e.target.value) })}
                    required
                  />
                </div>
              </div>

              {/* Added API Config for Fear/Greed */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem', marginTop: '1rem', padding: '1rem', background: 'rgba(255,255,255,0.03)', borderRadius: '0.75rem' }}>
                <div className="form-group">
                  <label>Lever (杠杆倍数)</label>
                  <select 
                    value={formData.lever} 
                    onChange={(e) => setFormData({ ...formData, lever: e.target.value })}
                    style={{ background: 'var(--card-bg)', color: 'white', padding: '0.5rem', borderRadius: '0.5rem', border: '1px solid var(--glass-border)', width: '100%' }}
                  >
                    <option value="1">1x (Normal)</option>
                    <option value="2">2x (Bull/Bear)</option>
                    <option value="3">3x (TQQQ/INDL)</option>
                    <option value="4">4x</option>
                  </select>
                </div>
                <div className="form-group">
                  <label>Emo Area (资产类型)</label>
                  <select 
                    value={formData.emo_area} 
                    onChange={(e) => setFormData({ ...formData, emo_area: e.target.value })}
                    style={{ background: 'var(--card-bg)', color: 'white', padding: '0.5rem', borderRadius: '0.5rem', border: '1px solid var(--glass-border)', width: '100%' }}
                  >
                    <option value="us">US Tech (美概/纳指)</option>
                    <option value="a">China (中概)</option>
                    <option value="coin">Crypto (币股)</option>
                    <option value="other">Other (INDL/Japan/Global)</option>
                  </select>
                </div>
              </div>

              <div className="modal-actions">
                <button type="button" className="btn-ghost" onClick={() => setIsModalOpen(false)}>Cancel</button>
                <button type="submit" className="btn-primary btn-submit">Save Strategy</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
