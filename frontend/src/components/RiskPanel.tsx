import { Check, Copy, Minus, X } from 'lucide-react';
import { useState } from 'react';
import type { DailyStatus, InstrumentInfo, PlanInput, Settings } from '@/api/client';
import { computeSizing, ruleChecks, toContracts } from '@/lib/risk';
import { cn, fmtNum, fmtPrice, fmtUsd } from '@/lib/format';
import { Card, Stat } from './ui';

export default function RiskPanel({ plan, settings, daily, info }: {
  plan: PlanInput | null; settings: Settings; daily: DailyStatus | null; info: InstrumentInfo | null;
}) {
  const [copied, setCopied] = useState(false);
  const sizing = plan && plan.side !== 'none' && plan.entry != null && plan.stop != null && plan.tp1 != null && plan.target != null
    ? computeSizing(plan.side, plan.entry, plan.stop, plan.tp1, plan.target, settings.capital, settings.risk_pct, settings.leverage)
    : null;
  const checks = ruleChecks(settings.rules, settings.risk_pct, settings.leverage, sizing?.rr ?? null, daily);
  const contracts = sizing ? toContracts(sizing.quantity, info) : null;
  const base = info?.ct_val_ccy || (plan?.instrument.split('-')[0] ?? '');

  const copy = async () => {
    if (!plan || !sizing) return;
    const text = [
      `${plan.instrument} ${plan.side.toUpperCase()}`,
      `Ingresso limit ${fmtPrice(plan.entry)} · Stop ${fmtPrice(plan.stop)} · TP1 ${fmtPrice(plan.tp1)} · Target ${fmtPrice(plan.target)}`,
      `Quantità ${fmtNum(sizing.quantity, 4)} ${base}${contracts != null ? ` (${contracts} contratti)` : ''} · leva ${settings.leverage}x isolato`,
      `Rischio ${fmtUsd(sizing.riskUsd)} · R:R ${sizing.rr.toFixed(2)}:1`,
    ].join('\n');
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  return (
    <Card
      title="Rischio e dimensione"
      actions={sizing && (
        <button onClick={copy} className="flex min-h-9 items-center gap-1 rounded-md px-2 text-xs text-slate-300 hover:bg-slate-800">
          <Copy className="h-3.5 w-3.5" aria-hidden />{copied ? 'Copiato' : 'Copia per OKX'}
        </button>
      )}
    >
      <p className="mb-3 text-xs text-slate-500">
        Capitale {fmtUsd(settings.capital)} · rischio {settings.risk_pct}% · leva {settings.leverage}x (da Impostazioni)
      </p>
      {sizing ? (
        <div className="grid grid-cols-2 gap-2">
          <Stat label="Quantità" value={`${fmtNum(sizing.quantity, 4)} ${base}`} />
          <Stat label="Contratti OKX" value={contracts != null ? fmtNum(contracts, Math.max(0, -Math.floor(Math.log10(info?.lot_sz ?? 1)))) : '—'} />
          <Stat label="Nozionale" value={fmtUsd(sizing.notional)} />
          <Stat label="Margine impegnato" value={fmtUsd(sizing.margin)} />
          <Stat label="Perdita allo stop" value={fmtUsd(-sizing.riskUsd)} tone="down" />
          <Stat label="R:R al target" value={`${sizing.rr.toFixed(2)}:1`} />
          <Stat label="Metà a TP1" value={fmtUsd(sizing.gainTp1Half)} tone="up" />
          <Stat label="TP1 + target" value={fmtUsd(sizing.gainTotal)} tone="up" />
          <Stat label="Liquidazione stimata" value={fmtPrice(sizing.liquidation)} />
          {info?.min_sz != null && contracts != null && (
            <Stat label="Minimo OKX" value={contracts >= info.min_sz ? 'rispettato' : `sotto ${info.min_sz}`} tone={contracts >= info.min_sz ? undefined : 'down'} />
          )}
        </div>
      ) : (
        <p className="text-sm text-slate-500">Inserisci ingresso, stop, TP1 e target per calcolare la dimensione.</p>
      )}
      <h3 className="mb-2 mt-4 text-xs font-semibold uppercase tracking-wide text-slate-400">Regole fisse</h3>
      <ul className="space-y-1.5">
        {checks.map((c) => (
          <li key={c.label} className="flex items-center gap-2 text-sm">
            {c.ok === null
              ? <Minus className="h-4 w-4 shrink-0 text-slate-500" aria-label="da verificare" />
              : c.ok
                ? <Check className="h-4 w-4 shrink-0 text-emerald-400" aria-label="rispettata" />
                : <X className="h-4 w-4 shrink-0 text-rose-400" aria-label="non rispettata" />}
            <span className={cn('flex-1', c.ok === false && 'text-rose-300')}>{c.label}</span>
            <span className="num text-xs text-slate-400">{c.detail}</span>
          </li>
        ))}
        <li className="flex items-center gap-2 text-sm text-slate-400">
          <Check className="h-4 w-4 shrink-0 text-slate-500" aria-hidden />
          Stop inserito insieme all'ordine · ordine limit (maker)
        </li>
      </ul>
    </Card>
  );
}
