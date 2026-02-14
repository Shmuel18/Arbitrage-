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
  exchanges: {
    long: string;
    short: string;
  };
  open_time: string;
  close_time: string;
  size: number;
  entry_spread: number;
  exit_spread: number;
  pnl: number;
  pnl_percentage: number;
  status: string;
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
