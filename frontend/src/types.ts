export interface SignalRow {
  id: number;
  symbol: string;
  strategy: string;
  strategy_label: string;
  composite_score: number;
  sub_scores: Record<string, number>;
  penalties: string[];
  entry: number;
  stop_loss: number;
  target_2r: number;
  target_3r: number;
  risk_reward: number;
  pct_risk: number;
  suggested_qty: number;
  indicators: Record<string, any>;
  reasons: string[];
  llm_rank: number | null;
  llm_conviction: string | null;
  llm_rationale: string | null;
  llm_conflicts: string[];
  llm_regime_note: string | null;
}

export interface ScanRunInfo {
  id: number;
  started_at: string;
  universe: string;
  strategy_filter: string;
  market_regime: string;
  regime_detail: Record<string, any>;
  scanned_count: number;
  signal_count: number;
  llm_used: boolean;
}

export interface ScanLatest {
  run: ScanRunInfo | null;
  signals: SignalRow[];
  disclaimer: string;
}

export interface Position {
  id: number;
  symbol: string;
  strategy: string;
  qty: number;
  entry_price: number;
  stop_loss: number;
  target: number;
  trailing: boolean;
  entry_time: string;
  ltp: number | null;
  unrealized_pnl: number | null;
  unrealized_pnl_pct: number | null;
  r_multiple: number | null;
  dist_to_sl_pct: number | null;
  dist_to_target_pct: number | null;
  broker_source: string;
}

export interface ClosedTrade {
  id: number;
  symbol: string;
  strategy: string;
  status: string;
  qty: number;
  entry_price: number;
  stop_loss: number;
  target: number;
  entry_time: string;
  exit_time: string | null;
  exit_price: number | null;
  exit_reason: string | null;
  pnl: number | null;
  costs: number;
  r_multiple: number | null;
  holding_days: number | null;
}

export interface Metrics {
  trades: number;
  wins?: number;
  losses?: number;
  win_rate: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  expectancy_r: number | null;
  profit_factor: number | null;
  max_drawdown_pct: number | null;
  total_return_pct: number | null;
  net_pnl: number;
  avg_holding_days: number | null;
  equity_curve: { t: string | null; equity: number }[];
}

export interface BacktestResult {
  strategy: string;
  years: number;
  symbols_tested: number;
  overall: Metrics;
  by_strategy: Record<string, Metrics>;
  trades: any[];
}

export interface ChartData {
  symbol: string;
  candles: { time: string; open: number; high: number; low: number; close: number; volume: number }[];
  emas: Record<string, { time: string; value: number }[]>;
  levels: { entry: number; stop_loss: number; target_2r: number; target_3r: number } | null;
}

export interface BrokerStatus {
  name: string;
  authenticated: boolean;
  detail: string;
  login_url: string | null;
}
