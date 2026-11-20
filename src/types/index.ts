export type TradingMode = 'paper' | 'live'
export type BotState = 'setup_required' | 'running' | 'paused' | 'error' | 'circuit_breaker'

export interface ScoreWeights {
  momentum: number
  trend: number
  volume: number
  regime: number
  volatility: number
  liquidity: number
  reward_risk: number
}

export interface Settings {
  trading_mode: TradingMode
  live_trading_enabled: boolean
  symbols: string[]
  max_trades_per_day: number
  max_open_positions: number
  standard_allocation_percent: number
  min_allocation_percent: number
  max_allocation_percent: number
  initial_stop_loss_percent: number
  trailing_stop_enabled: boolean
  trailing_activation_percent: number
  trailing_distance_percent: number
  fixed_profit_target_enabled: boolean
  fixed_profit_target_percent: number
  daily_profit_goal: number
  pause_after_daily_goal: boolean
  daily_loss_limit_percent: number
  weekly_loss_limit_percent: number
  max_consecutive_losses: number
  overnight_positions: boolean
  min_signal_score: number
  enabled_strategies: string[]
  score_weights: ScoreWeights
  options_provider: 'alpaca' | 'tradier'
  max_quote_age_seconds: number
  max_spread_percent: number
  max_slippage_percent: number
  min_absolute_delta: number
  max_absolute_delta: number
  min_open_interest: number
  min_contract_volume: number
  opening_range_minutes: number
  new_entry_cutoff_et: string
  liquidation_start_et: string
  target_flat_time_et: string
  close_positions_eod: boolean
  transaction_fee_per_contract: number
  notifications_enabled: boolean
  news_filter_enabled: boolean
  ml_filter_enabled: boolean
}

export interface Account {
  equity: number
  cash: number
  buying_power: number
  options_buying_power: number
  options_level: number
  blocked: boolean
  trade_blocked: boolean
}

export interface Position {
  id: string
  mode: TradingMode
  contract_symbol: string
  underlying: string
  option_type: 'call' | 'put'
  strike: number
  expiration: string
  qty: number
  initial_qty: number
  entry_premium: number
  current_bid: number | null
  current_ask: number | null
  highest_bid: number
  stop_threshold: number
  trailing_active: boolean
  exit_order_id: string | null
  status: string
  strategy: string
  score: number
  realized_pnl: number | null
  fees: number
  exit_reason: string | null
  opened_at: string
  closed_at: string | null
  recovered: boolean
  unrealized_pnl: number | null
  protected_pnl_before_costs_if_filled: number
  protection: Record<string, number | boolean>
}

export interface Signal {
  id: string
  underlying: string
  strategy: string
  direction: 'call' | 'put'
  score: number
  qualified: boolean
  regime: string
  factors: Record<string, number>
  explanation: { reasons?: string[]; contributions?: Record<string, number>; option?: Record<string, unknown> }
  rejection_reason: string | null
  created_at: string
}

export interface DailyState {
  day: string
  start_equity: number
  realized_pnl: number
  completed_trades: number
  consecutive_losses: number
  goal_reached: boolean
  circuit_breaker: boolean
  final_equity: number | null
}

export interface Status {
  state: BotState
  entries_enabled: boolean
  runtime_active: boolean
  mode: TradingMode
  last_error: string | null
  account: Account | null
  clock: { is_open?: boolean; next_open?: string; next_close?: string; timestamp?: string } | null
  data_ready: boolean
  reconciled: boolean
  provider: string | null
  stream_connected: boolean
  stream_error: string | null
  updated_at: string
  credentials_configured: boolean
  options_feed: string
  daily: DailyState | null
  active_position: Position | null
  latest_signals: Signal[]
  settings: Settings
}

export interface Trade {
  id: string
  position_id: string
  contract_symbol: string
  underlying: string
  option_type: 'call' | 'put'
  strike: number
  expiration: string
  strategy: string
  qty: number
  entry_price: number
  exit_price: number
  realized_pnl: number
  fees: number
  return_percent: number
  exit_reason: string
  entry_time: string
  exit_time: string
  intended_exit_price: number | null
  slippage: number | null
}

export interface Order {
  id: string
  client_order_id: string
  broker_order_id: string | null
  symbol: string
  side: 'buy' | 'sell'
  position_intent: string
  qty: number
  limit_price: number | null
  status: string
  filled_qty: number
  filled_avg_price: number | null
  position_id: string | null
  reason: string | null
  created_at: string
  updated_at: string
}

export interface EventItem {
  id: number
  level: string
  event_type: string
  message: string
  context: Record<string, unknown>
  created_at: string
}

export interface Performance {
  net_realized_pnl: number
  trade_count: number
  win_rate: number
  average_gain: number
  average_loss: number
  profit_factor: number | null
  maximum_drawdown: number
  average_trade_return: number
  average_holding_minutes: number
  average_slippage: number
  equity_curve: { time: string; value: number }[]
  account_curve: { time: string; equity: number; daily_pnl: number }[]
  daily_profit: { day: string; pnl: number }[]
  strategy_performance: { name: string; pnl: number; trades: number; win_rate: number }[]
  time_of_day: { hour: string; pnl: number; trades: number }[]
}

export interface Candle {
  timestamp: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface MarketData {
  symbol: string
  quote: { bid: number; ask: number; timestamp: string; age_seconds: number; spread_percent: number; fresh: boolean; provider: string }
  indicators: Record<string, number> | null
  candles: Candle[]
}

export interface PreflightCheck { name: string; passed: boolean; detail: string }
export interface Preflight { ready: boolean; checks: PreflightCheck[] }

export interface BacktestResult {
  id: string
  created_at: string
  initial_equity: number
  ending_equity_before_open_mark_value: number
  net_realized_pnl: number
  trade_count: number
  win_rate: number
  average_gain: number
  average_loss: number
  profit_factor: number | null
  maximum_drawdown_percent: number
  trades: Record<string, unknown>[]
  equity_curve: number[]
  open_position: Record<string, unknown> | null
  assumptions: Record<string, unknown>
}
