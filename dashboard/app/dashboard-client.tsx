'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import type {
  DashboardData,
  HistoryPoint,
  Position,
  StrategyFill,
  StrategyOrder,
} from './dashboard-data';

type Tab = 'overview' | 'positions' | 'orders' | 'logs';

const tabItems: Array<{ id: Tab; label: string }> = [
  { id: 'overview', label: '策略总览' },
  { id: 'positions', label: '持仓与目标' },
  { id: 'orders', label: '委托与成交' },
  { id: 'logs', label: '运行日志' },
];

function money(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return '未知';
  return `¥${value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function percent(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return '未知';
  return `${value >= 0 ? '+' : ''}${(value * 100).toFixed(2)}%`;
}

function dateTime(value?: string | null, timeOnly = false) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    month: timeOnly ? undefined : '2-digit',
    day: timeOnly ? undefined : '2-digit',
    hour: '2-digit', minute: '2-digit', second: timeOnly ? '2-digit' : undefined,
    hour12: false,
  }).format(date);
}

function strategyName(id: string) {
  if (id === 'good_etf_remote') return '很好ETF · Remote';
  if (id === 'good_etf_jq') return '很好ETF · JQ';
  return id.replaceAll('_', ' ');
}

function stateText(state?: string) {
  const labels: Record<string, string> = {
    READY: '就绪', ACTIVE: '运行中', EXECUTING: '执行中', COMPLETED: '已完成',
    FILLED: '已成交', PARTIALLY_FILLED: '部分成交', SUBMITTED: '已报',
    CANCELED: '已取消', REJECTED: '已拒绝', FAILED: '失败',
    RECONCILIATION_BLOCKED: '对账阻断', TRADING_BLOCKED: '交易阻断',
  };
  return state ? labels[state] ?? state : '未知';
}

function positionPnl(position: Position) {
  const cost = position.avg_cost * position.total_amount;
  return cost > 0 ? position.unrealized_pnl / cost : null;
}

function logLevel(line: string) {
  if (line.includes(' - ERROR - ') || line.includes(' ERROR ')) return 'ERROR';
  if (line.includes(' - WARNING - ') || line.includes(' WARNING ')) return 'WARNING';
  return 'INFO';
}

function PerformanceChart({ history, startingCash }: { history: HistoryPoint[]; startingCash: number }) {
  const points = history.slice(-60);
  const values = points.map((item) => item.nav != null ? item.nav - 1 : (item.total_value - startingCash) / startingCash);
  const minimum = Math.min(0, ...values);
  const maximum = Math.max(0.001, ...values);
  const span = maximum - minimum || 0.01;
  return (
    <>
      <div className="chart" aria-label="策略收益历史">
        <span className="axis-label top">{percent(maximum)}</span>
        <span className="axis-label middle">{percent((maximum + minimum) / 2)}</span>
        <span className="axis-label bottom">{percent(minimum)}</span>
        <div className="chart-bars">
          {points.length ? points.map((point, index) => {
            const value = values[index];
            const height = 12 + ((value - minimum) / span) * 84;
            return <span className={value < 0 ? 'loss' : ''} key={`${point.as_of}-${index}`} style={{ height: `${height}%` }} title={`${dateTime(point.as_of)} ${percent(value)}`} />;
          }) : <div className="empty-chart">收益曲线将从启用看板后开始按分钟记录</div>}
        </div>
      </div>
      <div className="chart-caption">
        <span>{points[0] ? dateTime(points[0].as_of) : '暂无历史'}</span>
        <span>{points.at(-1) ? dateTime(points.at(-1)?.as_of) : ''}</span>
      </div>
    </>
  );
}

function PositionsTable({ positions }: { positions: Position[] }) {
  if (!positions.length) return <div className="empty-state">当前没有策略归属持仓</div>;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>标的</th><th>名称</th><th>数量 / 可卖</th><th>成本 / 现价</th><th>市值</th><th>持仓盈亏</th><th>行情时间</th></tr></thead>
        <tbody>{positions.map((item) => {
          const pnl = positionPnl(item);
          return <tr key={item.security}>
            <td className="mono">{item.security}</td>
            <td>{item.name || '—'}</td>
            <td>{item.total_amount.toLocaleString()} <small>/ {item.closeable_amount.toLocaleString()}</small></td>
            <td>{item.avg_cost.toFixed(4)} <small>/ {item.price.toFixed(4)}</small></td>
            <td>{money(item.value)}</td>
            <td className={pnl != null && pnl >= 0 ? 'positive' : 'negative'}>{percent(pnl)}</td>
            <td>{dateTime(item.mark_as_of, true)}</td>
          </tr>;
        })}</tbody>
      </table>
    </div>
  );
}

function OrdersTable({ orders }: { orders: StrategyOrder[] }) {
  if (!orders.length) return <div className="empty-state">暂无策略委托</div>;
  return <div className="table-wrap"><table>
    <thead><tr><th>时间</th><th>标的</th><th>方向</th><th>委托 / 成交</th><th>限价</th><th>状态</th><th>客户端标识</th></tr></thead>
    <tbody>{orders.map((item) => <tr key={item.order_id}>
      <td>{dateTime(item.updated_at)}</td>
      <td><span className="mono">{item.security}</span><small className="block-note">{item.name || '—'}</small></td>
      <td className={item.side === 'BUY' ? 'positive' : 'negative'}>{item.side === 'BUY' ? '买入' : '卖出'}</td>
      <td>{item.requested_qty.toLocaleString()} <small>/ {item.filled_qty.toLocaleString()}</small></td>
      <td>{item.limit_price == null ? '市价' : item.limit_price.toFixed(4)}</td>
      <td><span className={`state state-${item.state.toLowerCase()}`}>{stateText(item.state)}</span></td>
      <td className="mono muted-cell">{item.client_tag}</td>
    </tr>)}</tbody>
  </table></div>;
}

function FillsTable({ fills }: { fills: StrategyFill[] }) {
  if (!fills.length) return <div className="empty-state">暂无策略成交</div>;
  return <div className="table-wrap"><table>
    <thead><tr><th>成交时间</th><th>标的</th><th>方向</th><th>数量</th><th>单价</th><th>成交额</th><th>费用</th></tr></thead>
    <tbody>{fills.map((item) => <tr key={item.fill_id}>
      <td>{dateTime(item.traded_at)}</td>
      <td><span className="mono">{item.security}</span><small className="block-note">{item.name || '—'}</small></td>
      <td className={item.side === 'BUY' ? 'positive' : 'negative'}>{item.side === 'BUY' ? '买入' : '卖出'}</td>
      <td>{item.quantity.toLocaleString()}</td><td>{item.price.toFixed(4)}</td><td>{money(item.amount)}</td>
      <td>{item.fees_known ? money((item.commission ?? 0) + (item.tax ?? 0)) : '未知'}</td>
    </tr>)}</tbody>
  </table></div>;
}

export default function DashboardClient({ initialData, user }: { initialData: DashboardData; user: { displayName: string; email: string } }) {
  const [data, setData] = useState(initialData);
  const [activeTab, setActiveTab] = useState<Tab>('overview');
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [logFilter, setLogFilter] = useState('ALL');
  const [logSearch, setLogSearch] = useState('');

  const refresh = useCallback(async (strategyId?: string | null) => {
    setRefreshing(true); setRefreshError(null);
    try {
      const query = strategyId ? `?strategy_id=${encodeURIComponent(strategyId)}` : '';
      const response = await fetch(`/api/dashboard${query}`, { cache: 'no-store' });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || '刷新失败');
      setData(body);
    } catch (error) {
      setRefreshError(error instanceof Error ? error.message : '刷新失败');
    } finally { setRefreshing(false); }
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => refresh(data.selected_strategy_id), 15000);
    return () => window.clearInterval(timer);
  }, [data.selected_strategy_id, refresh]);

  const snapshot = data.snapshot;
  const positions = useMemo(() => Object.values(snapshot?.positions ?? {}), [snapshot]);
  const latestIntent = data.activity.intents[0];
  const qmtReady = Boolean(data.server.qmt?.ready);
  const overallReady = data.server.process_alive && data.server.strategy_ledger_ready && qmtReady && !data.snapshot_error;
  const filteredLogs = useMemo(() => data.logs.filter((line) => {
    const matchesLevel = logFilter === 'ALL' || logLevel(line) === logFilter;
    return matchesLevel && line.toLowerCase().includes(logSearch.trim().toLowerCase());
  }).reverse(), [data.logs, logFilter, logSearch]);

  const metrics = [
    { label: '总资产', value: money(snapshot?.total_value), note: `初始资金 ${money(snapshot?.starting_cash)}` },
    { label: '累计收益', value: percent(snapshot?.returns), note: snapshot?.performance_ready ? `累计盈亏 ${money(snapshot.total_pnl)}` : '费用或成交价仍有未知项', tone: (snapshot?.returns ?? 0) >= 0 ? 'positive' : 'negative' },
    { label: '可用资金', value: money(snapshot?.available_cash), note: snapshot ? `现金占比 ${percent(snapshot.available_cash / snapshot.total_value)}` : '—' },
    { label: '持仓市值', value: money(snapshot?.positions_value), note: `${positions.length} 个策略归属持仓` },
  ];

  return <main className="shell">
    <aside className="sidebar">
      <div className="brand-mark">BT</div>
      <div className="brand-copy"><strong>白话量化</strong><span>策略监控台</span></div>
      <label className="strategy-picker-label" htmlFor="strategy-picker">当前策略</label>
      <select id="strategy-picker" className="strategy-picker" value={data.selected_strategy_id ?? ''} onChange={(event) => refresh(event.target.value)}>
        {data.strategies.map((strategy) => <option value={strategy.strategy_id} key={strategy.strategy_id}>{strategyName(strategy.strategy_id)}</option>)}
      </select>
      <nav aria-label="主导航">{tabItems.map((item) => <button type="button" className={`nav-item ${activeTab === item.id ? 'active' : ''}`} onClick={() => setActiveTab(item.id)} key={item.id}>{item.label}{item.id === 'orders' && data.activity.orders.length > 0 ? <em>{data.activity.orders.length}</em> : null}</button>)}</nav>
      <div className="sidebar-foot"><span className={`status-dot ${qmtReady ? '' : 'offline'}`} />QMT {qmtReady ? '已连接' : '未就绪'}</div>
    </aside>

    <section className="workspace">
      <header className="topbar">
        <div><p className="eyebrow">策略归属持仓 / 只读监控</p><h1>{strategyName(data.selected_strategy_id ?? '未选择策略')}</h1></div>
        <div className="topbar-actions">
          <div className="heartbeat"><span className={`status-dot ${overallReady ? '' : 'offline'}`} />数据更新于 {dateTime(data.generated_at, true)}</div>
          <button type="button" onClick={() => refresh(data.selected_strategy_id)} disabled={refreshing}>{refreshing ? '刷新中…' : '刷新数据'}</button>
          <div className="user-chip" title={user.email}>{user.displayName.slice(0, 1).toUpperCase()}<a href="/signout-with-chatgpt?return_to=%2F">退出</a></div>
        </div>
      </header>

      <div className="content">
        <section className={`notice ${overallReady ? '' : 'warning-notice'}`}>
          <div><strong>{overallReady ? '策略运行正常' : '部分数据暂未就绪'}</strong><span>{refreshError || data.snapshot_error || (data.demo ? '当前为页面演示数据，发布后接入服务器真实账本' : '最新行情、账本与 QMT 回报链路均已连接')}</span></div>
          <span className="pill">{data.demo ? '演示' : data.server.trading_enabled ? '交易已开启' : '交易已关闭'}</span>
        </section>

        {activeTab === 'overview' && <>
          <section className="metric-grid" aria-label="策略资金摘要">{metrics.map((item) => <article className="metric-card" key={item.label}><span>{item.label}</span><strong className={item.tone ?? ''}>{item.value}</strong><small>{item.note}</small></article>)}</section>
          <section className="main-grid">
            <article className="panel performance-panel"><div className="panel-heading"><div><p className="eyebrow">PERFORMANCE</p><h2>策略收益走势</h2></div><span className="range-note">按分钟采样 · 最近 60 点</span></div><PerformanceChart history={data.activity.history} startingCash={snapshot?.starting_cash || 1} /></article>
            <aside className="panel runtime-panel"><div className="panel-heading compact"><div><p className="eyebrow">RUNTIME</p><h2>运行状态</h2></div><span className="pill quiet">只读</span></div>
              <dl className="runtime-list"><div><dt>策略 ID</dt><dd>{data.selected_strategy_id || '—'}</dd></div><div><dt>策略账户</dt><dd className={snapshot ? 'ok' : ''}>{snapshot ? '已同步' : '不可用'}</dd></div><div><dt>QMT Broker</dt><dd className={qmtReady ? 'ok' : ''}>{stateText(data.server.qmt?.state)}</dd></div><div><dt>StrategyLedger</dt><dd className={data.server.strategy_ledger_ready ? 'ok' : ''}>{data.server.strategy_ledger_ready ? '已对账' : '未就绪'}</dd></div><div><dt>当前目标</dt><dd>{stateText(latestIntent?.state)}</dd></div><div><dt>最后行情</dt><dd>{dateTime(positions[0]?.mark_as_of, true)}</dd></div></dl>
            </aside>
          </section>
          <section className="panel positions-panel"><div className="panel-heading compact"><div><p className="eyebrow">POSITIONS</p><h2>当前持仓</h2></div><button className="link-button" type="button" onClick={() => setActiveTab('positions')}>查看目标持仓</button></div><PositionsTable positions={positions} /></section>
        </>}

        {activeTab === 'positions' && <section className="stacked-panels">
          <article className="panel positions-panel"><div className="panel-heading compact"><div><p className="eyebrow">OWNED POSITIONS</p><h2>策略归属持仓</h2></div><span className="range-note">以 StrategyLedger 为准</span></div><PositionsTable positions={positions} /></article>
          <article className="panel positions-panel"><div className="panel-heading compact"><div><p className="eyebrow">LATEST TARGET</p><h2>最新组合目标</h2></div><span className={`state state-${(latestIntent?.state || '').toLowerCase()}`}>{stateText(latestIntent?.state)}</span></div>
            {latestIntent ? <div className="table-wrap"><table><thead><tr><th>标的</th><th>名称</th><th>目标权重</th><th>目标数量</th><th>参考单价</th></tr></thead><tbody>{Object.entries(latestIntent.target_quantities).map(([security, quantity]) => <tr key={security}><td className="mono">{security}</td><td>{data.activity.security_names[security] || '—'}</td><td>{percent(latestIntent.weights[security])}</td><td>{quantity.toLocaleString()}</td><td>{latestIntent.reference_prices[security]?.toFixed(4) ?? '—'}</td></tr>)}</tbody></table></div> : <div className="empty-state">暂无组合目标</div>}
          </article>
        </section>}

        {activeTab === 'orders' && <section className="stacked-panels"><article className="panel positions-panel"><div className="panel-heading compact"><div><p className="eyebrow">ORDERS</p><h2>委托记录</h2></div><span className="range-note">最近 {data.activity.orders.length} 笔</span></div><OrdersTable orders={data.activity.orders} /></article><article className="panel positions-panel"><div className="panel-heading compact"><div><p className="eyebrow">FILLS</p><h2>成交记录</h2></div><span className="range-note">佣金缺失时显示“未知”</span></div><FillsTable fills={data.activity.fills} /></article></section>}

        {activeTab === 'logs' && <section className="panel log-panel"><div className="panel-heading compact"><div><p className="eyebrow">SERVER LOGS</p><h2>运行日志</h2></div><span className="range-note">服务器最近 {data.logs.length} 行</span></div><div className="log-toolbar"><select value={logFilter} onChange={(event) => setLogFilter(event.target.value)} aria-label="日志级别"><option value="ALL">全部级别</option><option value="INFO">INFO</option><option value="WARNING">WARNING</option><option value="ERROR">ERROR</option></select><input value={logSearch} onChange={(event) => setLogSearch(event.target.value)} placeholder="搜索标的、策略 ID 或错误信息" aria-label="搜索日志" /></div><div className="log-console">{filteredLogs.length ? filteredLogs.map((line, index) => <div className={`log-line log-${logLevel(line).toLowerCase()}`} key={`${line}-${index}`}><span>{logLevel(line)}</span><code>{line}</code></div>) : <div className="empty-state">没有匹配的日志</div>}</div></section>}
      </div>
    </section>
  </main>;
}
