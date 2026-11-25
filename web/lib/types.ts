export type Leader = {
  id: number;
  name: string;
  source: string;
  equity_usd: number;
  is_active: boolean;
  signal_count: number;
  last_signal_at: string | null;
  followers: number;
};

export type Signal = {
  signal_id: string;
  leader_name: string;
  leader_id: number;
  symbol: string | null;
  venue: string | null;
  action: string;
  side: string;
  size_frac: number | null;
  size_abs_usd: number | null;
  ref_price: number | null;
  ts: string;
};

export type Subscription = {
  id: number;
  leader_id: number;
  executor: string;
  mode: string;
  delivery: string;
  sizing: string;
  equity_usd: number;
  peak_equity: number;
  daily_loss_limit_pct: number;
  max_drawdown_pct: number;
  webhook_url: string | null;
};

export type Position = { symbol: string; side: string; qty: number; entry_price: number };

export type AccountStatus = {
  equity_usd: number;
  mode: string;
  positions: Position[];
};

export type Fill = {
  id: number;
  subscription_id: number;
  symbol: string;
  side: string;
  qty: number;
  price: number;
  fee_usd: number;
};

export type Me = { id: number; display_name: string };

export type BillingSummary = {
  subscription_id: number;
  accrued_platform: number;
  accrued_leader: number;
  entries: { kind: string; amount_usd: number; profit_usd: number; equity_usd: number; note: string | null; created_at: string }[];
};
