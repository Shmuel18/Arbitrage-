export interface BotStatus {
  bot_running: boolean;
  connected_exchanges: string[];
  active_positions: number;
  uptime: number;
}

export interface Position {
  id: string;
  symbol: string;
  exchanges: {
    long: string;
    short: string;
  };
  entry_time: string;
  size: number;
  entry_price: {
    long: number;
    short: number;
  };
  current_price: {
    long: number;
    short: number;
  };
  pnl: number;
  pnl_percentage: number;
  funding_rate: number;
}

export interface Trade {
  id: string;
  symbol: string;
  mode?: string;
  exchanges: {
    long: string;
    short: string;
  };
  long_exchange?: string | null;
  short_exchange?: string | null;
  open_time: string;
  close_time: string;
  size: number;
  entry_spread: number;
  exit_spread: number;
  pnl: number;
  pnl_percentage: number;
  status: string;
  // entry / exit prices
  entry_price_long?: string | null;
  entry_price_short?: string | null;
  exit_price_long?: string | null;
  exit_price_short?: string | null;
  // quantity
  long_qty?: string | null;
  short_qty?: string | null;
  // PnL breakdown
  price_pnl?: number | null;
  funding_net?: number | null;
  invested?: number | null;
  total_pnl?: number | null;
  // fees & funding
  fees_paid_total?: string | null;
  funding_received_total?: string | null;
  funding_paid_total?: string | null;
  long_funding_rate?: string | null;
  short_funding_rate?: string | null;
  // collections tracking
  funding_collections?: number | null;
  funding_collected_usd?: number | null;
  // exit metadata
  exit_reason?: string | null;
  hold_minutes?: number | null;
  // timestamps (raw)
  opened_at?: string | null;
  closed_at?: string | null;
}

export interface PerformanceData {
  timestamp: number;
  pnl: number;
  cumulative_pnl: number;
  active_positions: number;
}

export interface Summary {
  total_pnl: number;
  total_trades: number;
  win_rate: number;
  active_positions: number;
  uptime_hours: number;
}
