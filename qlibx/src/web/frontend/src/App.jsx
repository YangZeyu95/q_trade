import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { Plus, Edit2, Trash2, History, TrendingUp, DollarSign, Activity, X, List, LayoutGrid } from 'lucide-react';

const API_BASE = 'http://localhost:8000/api';

function App() {
  const [strategies, setStrategies] = useState({});
  const [history, setHistory] = useState([]);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingSymbol, setEditingSymbol] = useState(null);
  const [viewMode, setViewMode] = useState('grid'); // 'grid' or 'table'
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
    buy_price_interval: 2,
    max_position: 100
  });

  useEffect(() => {
    fetchData();
  }, []);

  const fetchData = async () => {
    try {
      const stratRes = await axios.get(`${API_BASE}/strategies`);
      setStrategies(stratRes.data);
      const histRes = await axios.get(`${API_BASE}/all_history`);
      setHistory(histRes.data);
    } catch (err) {
      console.error('Failed to fetch data', err);
    }
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
        buy_price_interval: 2,
        max_position: 100
      });
    }
    setIsModalOpen(true);
  };

  const handleDelete = async (symbol) => {
    if (window.confirm(`Are you sure you want to delete ${symbol}?`)) {
      try {
        await axios.delete(`${API_BASE}/strategies/${symbol}`);
        fetchData();
      } catch (err) {
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
    } catch (err) {
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
        <div style={{ display: 'flex', gap: '1rem' }}>
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
          <button className="btn-primary" onClick={() => handleOpenModal()}>
            <Plus size={20} /> Add Stock
          </button>
        </div>
      </header>

      {viewMode === 'grid' ? (
        <div className="stock-grid">
          {Object.entries(strategies).map(([symbol, strat]) => (
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

              <div className="strategy-stats">
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
                  <span className="stat-label">Intervals</span>
                  <span className="stat-value">{strat.buy_day_interval}d / {strat.buy_price_interval}%</span>
                </div>
                <div className="stat-item" style={{ marginTop: '1rem' }}>
                  <span className="stat-label">Max Pos</span>
                  <span className="stat-value">{strat.max_position}%</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <section className="history-section animate-fade-in" style={{ marginBottom: '3rem', padding: '1rem' }}>
          <div style={{ overflowX: 'auto' }}>
            <table className="strategy-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Name</th>
                  <th>Buy Pt</th>
                  <th>Sell Pt</th>
                  <th>Buy Tot</th>
                  <th>Sell Tot</th>
                  <th>Intervals</th>
                  <th>Max Pos</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(strategies).map(([symbol, strat]) => (
                  <tr key={symbol}>
                    <td style={{ fontWeight: 700, color: 'var(--primary)' }}>{symbol}</td>
                    <td style={{ fontSize: '0.875rem' }}>{strat.name}</td>
                    <td style={{ color: 'var(--success)' }}>${strat.buy_point}</td>
                    <td style={{ color: 'var(--danger)' }}>${strat.sell_point}</td>
                    <td>${strat.buy_total}</td>
                    <td>${strat.sell_total}</td>
                    <td style={{ fontSize: '0.8125rem' }}>{strat.buy_day_interval}d / {strat.buy_price_interval}%</td>
                    <td>{strat.max_position}%</td>
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
                ))}
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
                    placeholder="e.g. TQQQ"
                    required
                  />
                </div>
                <div className="form-group">
                  <label>Name</label>
                  <input
                    value={formData.name}
                    onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                    placeholder="Stock Name"
                    required
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

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '1rem' }}>
                <div className="form-group">
                  <label>Day Interval</label>
                  <input
                    type="number"
                    value={formData.buy_day_interval}
                    onChange={(e) => setFormData({ ...formData, buy_day_interval: parseInt(e.target.value) })}
                    required
                  />
                </div>
                <div className="form-group">
                  <label>Price Interval (%)</label>
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
