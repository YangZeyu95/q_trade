import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { Plus, Edit2, Trash2, History, TrendingUp, DollarSign, Activity, X, List, LayoutGrid } from 'lucide-react';

const API_BASE = 'http://localhost:8000/api';

function App() {
  const [strategies, setStrategies] = useState({});
  const [history, setHistory] = useState([]);
  const [realtimeData, setRealtimeData] = useState({});
  const [holdings, setHoldings] = useState([]);
  const [indicator, setIndicator] = useState({ value: 0, name: 'Signal Indicator' });
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingSymbol, setEditingSymbol] = useState(null);
  const [viewMode, setViewMode] = useState('grid'); // 'grid' or 'table'
  const [activeTab, setActiveTab] = useState('strategies'); // 'strategies' or 'holdings'
  const [formData, setFormData] = useState({
    symbol: '',
    name: '',
    buy_point: 0,
    sell_point: 0,
    buy_total: 700,
    sell_total: 0,
    buy_limit_price: 0,
    sell_limit_price: 0,
    buy_day_interval: 1,
    sell_day_interval: 1,
    buy_price_interval: 2,
    max_position: 100,
    fear_greed_buy: -50,
    fear_greed_sell: 50
  });

  const fetchRealtime = React.useCallback(async () => {
    try {
      const realRes = await axios.get(`${API_BASE}/realtime`);
      setRealtimeData(realRes.data);
      const indRes = await axios.get(`${API_BASE}/indicator`);
      setIndicator(indRes.data);
      const holdRes = await axios.get(`${API_BASE}/holdings`);
      setHoldings(holdRes.data);
    } catch (err) {
      console.error('Failed to fetch realtime data', err);
    }
  }, []);

  const fetchData = React.useCallback(async () => {
    try {
      const stratRes = await axios.get(`${API_BASE}/strategies`);
      setStrategies(stratRes.data);
      const histRes = await axios.get(`${API_BASE}/all_history`);
      setHistory(histRes.data);
      fetchRealtime();
    } catch (err) {
      console.error('Failed to fetch data', err);
    }
  }, [fetchRealtime]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchData();
    const interval = setInterval(() => {
      fetchRealtime();
    }, 5000);
    return () => clearInterval(interval);
  }, [fetchData, fetchRealtime]);

  const [loadingName, setLoadingName] = useState(false);

  const handleSymbolBlur = async (e) => {
    const symbol = e.target.value.toUpperCase();
    if (symbol.length >= 1) {
      setLoadingName(true);
      try {
        const res = await axios.get(`${API_BASE}/stock_info/${symbol}`);
        setFormData(prev => ({ ...prev, name: res.data.name }));
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
      sell_total: 0,
      buy_limit_price: 0,
      sell_limit_price: 0,
      buy_day_interval: 1,
      sell_day_interval: 1,
      buy_price_interval: 2,
      max_position: 100,
      fear_greed_buy: -50,
      fear_greed_sell: 50
    });
    setEditingSymbol(null);
    setActiveTab('strategies');
    setIsModalOpen(true);
  };

  const handleOpenModal = (symbol = null) => {
    if (symbol) {
      setEditingSymbol(symbol);
      setFormData({ symbol, ...strategies[symbol] });
    } else {
      setEditingSymbol(null);
      setFormData({
        symbol: '',
        name: '',
        buy_point: 0,
        sell_point: 0,
        buy_total: 700,
        sell_total: 0,
        buy_limit_price: 0,
        sell_limit_price: 0,
        buy_day_interval: 1,
        sell_day_interval: 1,
        buy_price_interval: 2,
        max_position: 100,
        fear_greed_buy: -50,
        fear_greed_sell: 50
      });
    }
    setIsModalOpen(true);
  };

  const handleDelete = async (symbol) => {
    if (window.confirm(`Are you sure you want to delete ${symbol}?`)) {
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
              {indicator.value.toFixed(1)}
            </span>
          </div>
          <button className="btn-primary" onClick={() => handleOpenModal()}>
            <Plus size={20} /> Add Stock
          </button>
        </div>
      </header>

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
                        <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Holding</div>
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
                          {real.signal ? real.signal.toFixed(1) : '0.0'}
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
                        <span className="stat-label">Buy Total</span>
                        <span className="stat-value">${strat.buy_total}</span>
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
                        <span className="stat-label">Signal Thresholds (B/S)</span>
                        <span className="stat-value">{strat.fear_greed_buy} / {strat.fear_greed_sell}</span>
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
                      <th>Price / Weight</th>
                      <th>B/S Pt</th>
                      <th>Buy Tot</th>
                      <th>Intervals (B/S)</th>
                      <th>Max Pos</th>
                      <th>Signal (B/S)</th>
                      <th>Current Signal</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(strategies).map(([symbol, strat]) => {
                      const real = realtimeData[symbol] || { lastPrice: 0, weight: 0, signal: 0 };
                      return (
                        <tr key={symbol}>
                          <td style={{ fontWeight: 700, color: 'var(--primary)' }}>{symbol}</td>
                          <td style={{ fontSize: '0.875rem' }}>{strat.name}</td>
                          <td>
                            <div style={{ fontWeight: 600 }}>${real.lastPrice.toFixed(2)}</div>
                            <div style={{ fontSize: '0.75rem', color: 'var(--primary)' }}>{real.weight.toFixed(1)}%</div>
                          </td>
                          <td style={{ color: 'var(--success)' }}>${strat.buy_point} / <span style={{ color: 'var(--danger)' }}>${strat.sell_point}</span></td>
                          <td>${strat.buy_total}</td>
                          <td style={{ fontSize: '0.8125rem' }}>{strat.buy_day_interval}d / {strat.sell_day_interval}d | {strat.buy_price_interval}%</td>
                          <td>{strat.max_position}%</td>
                          <td>{strat.fear_greed_buy} / {strat.fear_greed_sell}</td>
                          <td style={{ 
                            fontWeight: 700,
                            color: real.signal > 0 ? 'var(--danger)' : 'var(--success)'
                          }}>
                            {real.signal ? real.signal.toFixed(1) : '0.0'}
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
      ) : (
        <section className="holdings-section animate-fade-in">
          <div style={{ overflowX: 'auto' }}>
            <table className="strategy-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Name</th>
                  <th>Quantity</th>
                  <th>Cost Price</th>
                  <th>Current Price</th>
                  <th>Market Value</th>
                  <th>Portfolio %</th>
                  <th>Floating P&L</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {holdings.length > 0 ? holdings.map((hold, i) => {
                  const income = parseFloat(hold.incomeBalance || 0);
                  const weight = hold.weight || 0;
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
                    <td colSpan="7" style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-muted)' }}>
                      No holdings found in the account.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="history-section animate-fade-in">
        <h2 className="section-title"><History size={24} style={{ verticalAlign: 'middle', marginRight: '0.75rem' }} /> Recent Trade History</h2>
        <div style={{ overflowX: 'auto' }}>
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Symbol</th>
                <th>Action</th>
                <th>Price</th>
                <th>Quantity</th>
                <th>Volume</th>
              </tr>
            </thead>
            <tbody>
              {history.slice(0, 10).map((trade, i) => (
                <tr key={i}>
                  <td style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>{new Date(trade.timestamp).toLocaleString()}</td>
                  <td style={{ fontWeight: 600 }}>{trade.symbol}</td>
                  <td>
                    <span className={trade.action === 'buy' ? 'badge-buy' : 'badge-sell'}>
                      {trade.action}
                    </span>
                  </td>
                  <td style={{ fontWeight: 500 }}>${Number(trade.price).toFixed(2)}</td>
                  <td>{trade.quantity}</td>
                  <td style={{ color: 'var(--text-muted)', fontSize: '0.8125rem' }}>{(trade.volume / 1000000).toFixed(1)}M</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {isModalOpen && (
        <div className="modal-overlay">
          <div className="modal-content animate-fade-in" style={{ maxWidth: '600px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem' }}>
              <h2>{editingSymbol ? `Edit ${editingSymbol}` : 'Add New Stock'}</h2>
              <button onClick={() => setIsModalOpen(false)} style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer' }}>
                <X size={24} />
              </button>
            </div>
            <form onSubmit={handleSubmit}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
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
                    readOnly
                    placeholder="Auto-fetched from API"
                    style={{ background: 'rgba(255,255,255,0.05)', cursor: 'not-allowed' }}
                  />
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
                <div className="form-group">
                  <label>Buy Point ($)</label>
                  <input
                    type="number" step="0.01"
                    value={formData.buy_point}
                    onChange={(e) => setFormData({ ...formData, buy_point: parseFloat(e.target.value) })}
                    required
                  />
                </div>
                <div className="form-group">
                  <label>Sell Point ($)</label>
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
                  <label>Sell Total ($)</label>
                  <input
                    type="number"
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
                    type="number"
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
