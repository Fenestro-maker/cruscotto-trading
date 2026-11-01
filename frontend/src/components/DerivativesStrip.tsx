import { useEffect, useState } from 'react';
import { marketApi } from '@/api/client';
import type { Derivatives } from '@/api/client';
import { cn, fmtDateTime, fmtNum } from '@/lib/format';

const REFRESH_MS = 60_000;

const pct = (x: number, digits = 2) => `${x > 0 ? '+' : ''}${x.toLocaleString('it-IT', { minimumFractionDigits: digits, maximumFractionDigits: digits })}%`;

function compactUsd(x: number): string {
  if (x >= 1e9) return `${fmtNum(x / 1e9, 2)} Mld $`;
  if (x >= 1e6) return `${fmtNum(x / 1e6, 1)} Mln $`;
  return `${fmtNum(x, 0)} $`;
}

/** Funding rate e open interest dello strumento (contesto sul posizionamento) */
export default function DerivativesStrip({ inst }: { inst: string }) {
  const [data, setData] = useState<Derivatives | null>(null);

  useEffect(() => {
    let stopped = false;
    const load = () => {
      if (document.hidden) return;
      marketApi.derivatives(inst).then(({ data }) => !stopped && setData(data)).catch(() => !stopped && setData(null));
    };
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => { stopped = true; clearInterval(id); setData(null); };
  }, [inst]);

  const f = data?.funding;
  const o = data?.open_interest;
  if (!f && !o) return null;

  // Funding oltre il doppio della sua media recente: posizionamento sbilanciato
  const crowded = f?.rate != null && f.avg_30 != null && Math.abs(f.rate) > Math.max(Math.abs(f.avg_30) * 2, 0.0001);

  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-400">
      {f?.rate != null && (
        <span title={`Positivo: i long pagano gli short. Prossimo pagamento ${fmtDateTime(f.next_time)}`}>
          Funding{' '}
          <span className={cn('num font-medium', crowded ? 'text-amber-300' : 'text-slate-200')}>{pct(f.rate * 100, 4)}</span>
          {f.interval_h ? ` ogni ${fmtNum(f.interval_h, 0)}h` : ''}
          {f.annual_pct != null && <span className="num"> (≈ {pct(f.annual_pct, 1)} annuo)</span>}
          {f.avg_30 != null && <span className="num"> · media {f.count_30} periodi {pct(f.avg_30 * 100, 4)}</span>}
          {crowded && <span className="text-amber-300"> · sbilanciato</span>}
        </span>
      )}
      {o?.oi_ccy != null && (
        <span>
          Open interest <span className="num font-medium text-slate-200">{o.oi_usd ? compactUsd(o.oi_usd) : fmtNum(o.oi_ccy, 2)}</span>
          {data!.oi_changes.map((ch) => (
            <span key={ch.hours} className="num">
              {' · '}{ch.hours}h <span className={ch.oi_pct >= 0 ? 'text-emerald-400' : 'text-rose-400'}>{pct(ch.oi_pct)}</span>
            </span>
          ))}
          {data!.oi_changes.length === 0 && <span> · variazioni disponibili dopo qualche ora</span>}
        </span>
      )}
    </div>
  );
}
