import type { InstrumentInfo, PlanInput, Rules, Side } from '@/api/client';

export interface Sizing {
  riskUsd: number;
  quantity: number;
  notional: number;
  margin: number;
  liquidation: number;
  gainTp1Half: number; // metà posizione chiusa a TP1
  gainTotal: number;   // metà a TP1 + metà al target
  rr: number;
}

/** Dimensione dal rischio: quantità = rischio in $ / distanza dello stop. La leva decide solo il margine. */
export function computeSizing(
  side: Side, entry: number, stop: number, tp1: number, target: number,
  capital: number, riskPct: number, leverage: number,
): Sizing | null {
  if (side === 'none' || !entry || !stop || entry === stop || !leverage) return null;
  const riskUsd = (capital * riskPct) / 100;
  const quantity = riskUsd / Math.abs(entry - stop);
  const notional = quantity * entry;
  const dir = side === 'long' ? 1 : -1;
  const gainTp1Half = (quantity / 2) * (tp1 - entry) * dir;
  const gainTarget = (quantity / 2) * (target - entry) * dir;
  return {
    riskUsd,
    quantity,
    notional,
    margin: notional / leverage,
    // stima senza margine di mantenimento
    liquidation: side === 'long' ? entry * (1 - 1 / leverage) : entry * (1 + 1 / leverage),
    gainTp1Half,
    gainTotal: gainTp1Half + gainTarget,
    rr: Math.abs(target - entry) / Math.abs(entry - stop),
  };
}

/** Quantità in contratti OKX, arrotondata per difetto al lotto minimo */
export function toContracts(quantity: number, info: InstrumentInfo | null): number | null {
  if (!info?.ct_val || !info.lot_sz) return null;
  const raw = quantity / info.ct_val;
  const lots = Math.floor(raw / info.lot_sz + 1e-9);
  const decimals = Math.max(0, -Math.floor(Math.log10(info.lot_sz)));
  return Number((lots * info.lot_sz).toFixed(decimals));
}

/** Stessi controlli del backend sull'ordine dei livelli e sul rapporto minimo */
export function planProblems(plan: Pick<PlanInput, 'side' | 'entry' | 'stop' | 'tp1' | 'target'>, minRr: number): string[] {
  const { side, entry, stop, tp1, target } = plan;
  if (side === 'none') return [];
  if (entry == null || stop == null || tp1 == null || target == null) return ['Servono ingresso, stop, TP1 e target'];
  if (side === 'long' && !(stop < entry && entry < tp1 && tp1 <= target)) return ['Per un long serve stop < ingresso < TP1 ≤ target'];
  if (side === 'short' && !(stop > entry && entry > tp1 && tp1 >= target)) return ['Per uno short serve stop > ingresso > TP1 ≥ target'];
  const rr = Math.abs(target - entry) / Math.abs(entry - stop);
  if (rr < minRr) return [`Rapporto ${rr.toFixed(2)}:1, sotto il minimo di ${minRr}:1: non si entra`];
  return [];
}

export interface RuleCheck {
  label: string;
  ok: boolean | null; // null = non ancora verificabile
  detail: string;
}

export function ruleChecks(
  rules: Rules, riskPct: number, leverage: number, rr: number | null,
  daily: { losses_in_row: number; r_today: number } | null,
): RuleCheck[] {
  return [
    { label: `Rischio ≤ ${rules.max_risk_pct}%`, ok: riskPct <= rules.max_risk_pct, detail: `${riskPct}%` },
    { label: `Leva ≤ ${rules.max_leverage}x, margine isolato`, ok: leverage <= rules.max_leverage, detail: `${leverage}x` },
    { label: `R:R ≥ ${rules.min_rr}:1`, ok: rr == null ? null : rr >= rules.min_rr, detail: rr != null ? `${rr.toFixed(2)}:1` : '—' },
    {
      label: `Perdite di fila oggi < ${rules.daily_max_losses_in_row}`,
      ok: !daily || daily.losses_in_row < rules.daily_max_losses_in_row,
      detail: daily ? String(daily.losses_in_row) : '—',
    },
    {
      label: `Risultato di oggi > ${rules.daily_max_loss_r} R`,
      ok: !daily || daily.r_today > rules.daily_max_loss_r,
      detail: daily ? `${daily.r_today > 0 ? '+' : ''}${daily.r_today} R` : '—',
    },
  ];
}
