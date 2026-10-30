import axios from 'axios';

const api = axios.create({ baseURL: '/api' });

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token');
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401 && !error.config?.url?.includes('/auth/login')) {
      localStorage.removeItem('token');
      localStorage.removeItem('user');
      window.location.href = '/login';
    }
    return Promise.reject(error);
  },
);

/** Messaggio d'errore leggibile da una risposta del backend */
export function errorMessage(err: unknown, fallback = 'Operazione non riuscita'): string {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (!err.response) return 'Server non raggiungibile';
  }
  return fallback;
}

export type User = { email: string; name?: string };

export interface Rules {
  max_risk_pct: number;
  max_leverage: number;
  min_rr: number;
  daily_max_losses_in_row: number;
  daily_max_loss_r: number;
}

export interface Settings {
  capital: number;
  risk_pct: number;
  leverage: number;
  instruments: string[];
  rules: Rules;
  ai_configured: boolean;
  ai_model: string;
}

export type Timeframe = '1W' | '1D' | '4H' | '1H' | '15m';

export interface Candle {
  t: number; // ms, apertura della candela
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
  confirmed: boolean;
}

export interface Ticker {
  inst: string;
  last: number;
  bid: number | null;
  ask: number | null;
  open24h: number;
  high24h: number;
  low24h: number;
  ts: number;
}

export interface InstrumentInfo {
  inst: string;
  type: string;
  ct_val: number | null; // quantità della valuta base per contratto
  ct_val_ccy: string | null;
  lot_sz: number | null;
  min_sz: number | null;
  tick_sz: number | null;
  settle_ccy: string | null;
}

export type Side = 'long' | 'short' | 'none';
export type SetupKey = 'retest_rottura' | 'pullback_trend' | 'sweep_liquidita' | 'rottura_range' | 'nessuno';

export const SETUP_LABELS: Record<SetupKey, string> = {
  retest_rottura: 'Retest della rottura',
  pullback_trend: 'Pullback nel trend',
  sweep_liquidita: 'Presa di liquidità (sweep)',
  rottura_range: 'Rottura del range',
  nessuno: 'Nessun trade',
};

export interface KeyLevel {
  price: number;
  kind: 'supporto' | 'resistenza' | 'altro';
  note: string;
}

export interface AnalysisResult {
  decision: 'trade' | 'no_trade';
  summary: string;
  weekly: string;
  daily: string;
  structure: string;
  timing: string;
  key_levels: KeyLevel[];
  setup: SetupKey;
  side: Side;
  entry: number | null;
  stop: number | null;
  tp1: number | null;
  target: number | null;
  cancel_above: number | null;
  cancel_below: number | null;
  expiry_hours: number | null;
  cancel_conditions: string[];
  invalidation: string;
  bias: string;
  risks: string[];
  confidence: 'bassa' | 'media' | 'alta';
}

export interface Analysis {
  id: string;
  instrument: string;
  created_at: string;
  price: number;
  note: string;
  result: AnalysisResult;
  expiry: string | null;
  rr: number | null;
  plan_issues: string[];
  usage: { model: string; input_tokens: number; output_tokens: number };
}

export type SessionStatus = 'attesa' | 'aperta' | 'chiusa' | 'annullata' | 'notrade';
export type Outcome = 'stop' | 'tp1_pareggio' | 'tp1_target' | 'manuale';

export interface PlanInput {
  instrument: string;
  side: Side;
  setup: SetupKey;
  entry: number | null;
  stop: number | null;
  tp1: number | null;
  target: number | null;
  cancel_above: number | null;
  cancel_below: number | null;
  expiry: string | null;
  bias: string;
  note: string;
  analysis_id: string | null;
}

export interface Session extends PlanInput {
  id: string;
  n: number;
  status: SessionStatus;
  created_at: string;
  filled_at?: string | null;
  closed_at?: string | null;
  outcome?: Outcome | null;
  exit?: number | null;
  r?: number | null;
  rr: number | null;
  cancel_reason?: string;
  sizing?: {
    capital: number;
    risk_pct: number;
    leverage: number;
    risk_usd: number;
    quantity: number;
    notional: number;
    margin: number;
  };
}

export interface DailyStatus {
  trades: number;
  r_today: number;
  losses_in_row: number;
  stopped: boolean;
  message: string;
}

export interface StatsGroup {
  key?: string;
  sessions: number;
  trades: number;
  no_trade: number;
  cancelled: number;
  win_rate: number | null;
  total_r: number;
  avg_r: number | null;
}

export interface Stats {
  overall: StatsGroup;
  by_instrument: StatsGroup[];
  by_setup: StatsGroup[];
}

export const authApi = {
  login: (email: string, password: string) =>
    api.post<{ access_token: string; user: User }>('/auth/login', { email, password }),
};

export const settingsApi = {
  get: () => api.get<Settings>('/settings'),
  update: (data: Pick<Settings, 'capital' | 'risk_pct' | 'leverage' | 'instruments'>) => api.put<Settings>('/settings', data),
};

export const marketApi = {
  ticker: (inst: string) => api.get<Ticker>('/market/ticker', { params: { inst } }),
  candles: (inst: string, tf: Timeframe) => api.get<Candle[]>('/market/candles', { params: { inst, tf, limit: 300 } }),
  instrument: (inst: string) => api.get<InstrumentInfo>('/market/instrument', { params: { inst } }),
};

export const analysisApi = {
  run: (instrument: string, note: string) => api.post<Analysis>('/analysis', { instrument, note }, { timeout: 600_000 }),
  list: () => api.get<Analysis[]>('/analysis'),
  get: (id: string) => api.get<Analysis>(`/analysis/${id}`),
};

export const sessionsApi = {
  list: () => api.get<Session[]>('/sessions'),
  today: () => api.get<DailyStatus>('/sessions/today'),
  create: (data: PlanInput) => api.post<Session>('/sessions', data),
  update: (id: string, data: PlanInput) => api.put<Session>(`/sessions/${id}`, data),
  setNote: (id: string, note: string) => api.put<Session>(`/sessions/${id}/note`, { note }),
  fill: (id: string) => api.post<Session>(`/sessions/${id}/fill`, {}),
  close: (id: string, data: { outcome: Outcome; exit_price?: number; tp1_taken?: boolean }) =>
    api.post<Session>(`/sessions/${id}/close`, data),
  cancel: (id: string, reason: string) => api.post<Session>(`/sessions/${id}/cancel`, { reason }),
  delete: (id: string) => api.delete(`/sessions/${id}`),
  stats: () => api.get<Stats>('/stats'),
  export: (format: 'csv' | 'json') => api.get<Blob>('/sessions/export', { params: { format }, responseType: 'blob' }),
  import: (items: unknown[]) => api.post<{ added: number; skipped: number }>('/sessions/import', items),
};

export default api;
