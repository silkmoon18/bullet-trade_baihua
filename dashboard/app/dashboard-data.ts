export type StrategySummary = {
  strategy_id: string;
  status: string;
  initial_capital: number;
  cash: number;
  updated_at: string;
};

export type Position = {
  security: string;
  name?: string;
  total_amount: number;
  closeable_amount: number;
  avg_cost: number;
  price: number;
  value: number;
  unrealized_pnl: number;
  mark_as_of: string;
  mark_source: string;
};

export type StrategySnapshot = {
  strategy_id: string;
  as_of: string;
  available_cash: number;
  cash: number;
  positions_value: number;
  total_value: number;
  starting_cash: number;
  total_pnl: number | null;
  realized_pnl: number | null;
  unrealized_pnl: number | null;
  fees: number | null;
  fees_known: boolean;
  nav: number | null;
  returns: number | null;
  performance_ready: boolean;
  performance_blockers: string[];
  positions: Record<string, Position>;
};

export type Intent = {
  intent_id: string;
  state: string;
  trading_day?: string;
  weights: Record<string, number>;
  target_quantities: Record<string, number>;
  reference_prices: Record<string, number>;
  created_at: string;
  updated_at: string;
};

export type StrategyOrder = {
  order_id: string;
  client_tag: string;
  broker_order_id?: string | null;
  security: string;
  name?: string;
  side: 'BUY' | 'SELL';
  requested_qty: number;
  filled_qty: number;
  limit_price?: number | null;
  state: string;
  trading_day: string;
  updated_at: string;
};

export type StrategyFill = {
  fill_id: string;
  security: string;
  name?: string;
  side: 'BUY' | 'SELL';
  quantity: number;
  price: number;
  amount: number;
  commission: number | null;
  tax: number | null;
  fees_known: boolean;
  traded_at: string;
};

export type HistoryPoint = {
  as_of: string;
  total_value: number;
  cash: number;
  positions_value: number;
  total_pnl: number | null;
  nav: number | null;
  fees: number | null;
  performance_ready: boolean;
};

export type DashboardData = {
  generated_at: string;
  demo?: boolean;
  server: {
    process_alive: boolean;
    uptime_seconds?: number;
    backend_type?: string;
    qmt?: { ready?: boolean; state?: string; last_error?: string | null };
    strategy_ledger_ready?: boolean;
    trading_enabled?: boolean;
    enabled_strategy_ids?: string[];
    dashboard_read_only?: boolean;
  };
  strategies: StrategySummary[];
  selected_strategy_id: string | null;
  snapshot: StrategySnapshot | null;
  snapshot_error?: string | null;
  activity: {
    security_names: Record<string, string>;
    intents: Intent[];
    orders: StrategyOrder[];
    fills: StrategyFill[];
    history: HistoryPoint[];
  };
  logs: string[];
};

const now = '2026-09-02T09:31:08+08:00';

export const demoDashboardData: DashboardData = {
  generated_at: now,
  demo: true,
  server: {
    process_alive: true,
    backend_type: 'qmt',
    qmt: { ready: true, state: 'READY', last_error: null },
    strategy_ledger_ready: true,
    trading_enabled: true,
    enabled_strategy_ids: ['good_etf_remote'],
    dashboard_read_only: true,
  },
  strategies: [
    { strategy_id: 'good_etf_remote', status: 'ACTIVE', initial_capital: 10000, cash: 612.4, updated_at: now },
    { strategy_id: 'good_etf_jq', status: 'ACTIVE', initial_capital: 10000, cash: 508.2, updated_at: now },
  ],
  selected_strategy_id: 'good_etf_remote',
  snapshot: {
    strategy_id: 'good_etf_remote', as_of: now, available_cash: 612.4, cash: 612.4,
    positions_value: 9571.86, total_value: 10184.26, starting_cash: 10000,
    total_pnl: 184.26, realized_pnl: 42.6, unrealized_pnl: 141.66, fees: null,
    fees_known: false, nav: 1.018426, returns: 0.018426, performance_ready: true,
    performance_blockers: [],
    positions: {
      '561920.XSHG': { security: '561920.XSHG', name: '疫苗龙头', total_amount: 5900, closeable_amount: 5900, avg_cost: .674, price: .686, value: 4047.4, unrealized_pnl: 70.8, mark_as_of: now, mark_source: 'QMT_TICK' },
      '520670.XSHG': { security: '520670.XSHG', name: '港科技ETF', total_amount: 3700, closeable_amount: 3700, avg_cost: .771, price: .778, value: 2878.6, unrealized_pnl: 25.9, mark_as_of: now, mark_source: 'QMT_TICK' },
      '159148.XSHE': { security: '159148.XSHE', name: '石油ETF富国', total_amount: 2700, closeable_amount: 2700, avg_cost: .990, price: .980, value: 2646, unrealized_pnl: -27, mark_as_of: now, mark_source: 'QMT_TICK' },
    },
  },
  activity: {
    security_names: { '561920.XSHG': '疫苗龙头', '520670.XSHG': '港科技ETF', '159148.XSHE': '石油ETF富国' },
    intents: [{ intent_id: 'demo-intent', state: 'EXECUTING', trading_day: '2026-09-02', weights: { '561920.XSHG': .4, '520670.XSHG': .3, '159148.XSHE': .25 }, target_quantities: { '561920.XSHG': 5900, '520670.XSHG': 3700, '159148.XSHE': 2700 }, reference_prices: { '561920.XSHG': .674, '520670.XSHG': .771, '159148.XSHE': .990 }, created_at: now, updated_at: now }],
    orders: [
      { order_id: 'demo-order-1', client_tag: 'good-etf-buy-1', security: '561920.XSHG', name: '疫苗龙头', side: 'BUY', requested_qty: 5900, filled_qty: 5900, limit_price: .676, state: 'FILLED', trading_day: '2026-09-02', updated_at: now },
      { order_id: 'demo-order-2', client_tag: 'good-etf-buy-2', security: '520670.XSHG', name: '港科技ETF', side: 'BUY', requested_qty: 3700, filled_qty: 2800, limit_price: .773, state: 'PARTIALLY_FILLED', trading_day: '2026-09-02', updated_at: now },
    ],
    fills: [{ fill_id: 'demo-fill-1', security: '561920.XSHG', name: '疫苗龙头', side: 'BUY', quantity: 5900, price: .674, amount: 3976.6, commission: null, tax: null, fees_known: false, traded_at: now }],
    history: Array.from({ length: 18 }, (_, index) => ({ as_of: `2026-08-${String(index + 10).padStart(2, '0')}T15:00:00+08:00`, total_value: 10000 + [0, 22, 18, 54, 49, 71, 88, 82, 113, 105, 129, 142, 137, 162, 153, 177, 170, 184][index], cash: 612.4, positions_value: 9400 + index * 10, total_pnl: [0, 22, 18, 54, 49, 71, 88, 82, 113, 105, 129, 142, 137, 162, 153, 177, 170, 184][index], nav: 1 + [0, 22, 18, 54, 49, 71, 88, 82, 113, 105, 129, 142, 137, 162, 153, 177, 170, 184][index] / 10000, fees: null, performance_ready: true })),
  },
  logs: [
    '2026-09-02 09:31:08 - INFO - 最新行情 | 561920.XSHG(疫苗龙头) price=0.686 source=QMT_TICK',
    '2026-09-02 09:30:09 - INFO - 真实组合目标已提交 | state=EXECUTING 标的数=3',
    '2026-09-02 09:30:08 - INFO - QMT成交回报 | 561920.XSHG(疫苗龙头) 数量=5900 单价=0.674',
    '2026-09-02 09:30:01 - WARNING - 费用字段未知，策略收益暂不扣除未知佣金',
  ],
};

export function unavailableDashboardData(message: string): DashboardData {
  return {
    generated_at: new Date().toISOString(),
    server: { process_alive: false, dashboard_read_only: true },
    strategies: [],
    selected_strategy_id: null,
    snapshot: null,
    snapshot_error: message,
    activity: { security_names: {}, intents: [], orders: [], fills: [], history: [] },
    logs: [],
  };
}

export async function loadDashboardData(strategyId?: string | null): Promise<DashboardData> {
  const endpoint = process.env.BULLET_TRADE_DASHBOARD_URL;
  const token = process.env.BULLET_TRADE_DASHBOARD_TOKEN;
  if (!endpoint || !token) return demoDashboardData;

  const url = new URL('/api/v1/dashboard', endpoint);
  const selectedStrategy = strategyId || process.env.BULLET_TRADE_DEFAULT_STRATEGY_ID;
  if (selectedStrategy) url.searchParams.set('strategy_id', selectedStrategy);
  url.searchParams.set('log_limit', '500');
  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${token}` },
    cache: 'no-store',
  });
  if (!response.ok) throw new Error(`看板数据服务返回 ${response.status}`);
  return (await response.json()) as DashboardData;
}
