import { useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { accountApi, errorMessage } from '@/api/client';
import type { Account, OkxPosition, Session } from '@/api/client';
import { cn, fmtDateTime, fmtNum, fmtPrice, fmtUsd } from '@/lib/format';
import { Alert, Card } from './ui';

const REFRESH_MS = 30_000;

// Campo "type" delle posizioni chiuse di OKX
const CLOSE_TYPES: Record<string, string> = {
  '1': 'chiusa in parte', '2': 'chiusa', '3': 'liquidata', '4': 'liquidata in parte', '5': 'ADL',
};

function direction(p: OkxPosition): 'long' | 'short' {
  if (p.pos_side === 'long' || p.pos_side === 'short') return p.pos_side;
  return p.pos >= 0 ? 'long' : 'short';
}

export default function AccountPanel({ inst, session }: { inst: string; session: Session | null }) {
  const [account, setAccount] = useState<Account | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let stopped = false;
    const load = () => {
      if (document.hidden) return;
      setLoading(true);
      accountApi.get()
        .then(({ data }) => { if (!stopped) { setAccount(data); setError(''); } })
        .catch((err) => !stopped && setError(errorMessage(err, 'Conto OKX non disponibile')))
        .finally(() => !stopped && setLoading(false));
    };
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => { stopped = true; clearInterval(id); };
  }, [tick]);

  const refresh = (
    <button onClick={() => setTick((t) => t + 1)} disabled={loading} aria-label="Aggiorna il conto"
      className="flex min-h-9 items-center gap-1 rounded-md px-2 text-xs text-slate-300 hover:bg-slate-800 disabled:opacity-50">
      <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} aria-hidden />Aggiorna
    </button>
  );

  if (account && !account.configured) {
    return (
      <Card title="Conto OKX">
        <p className="text-sm text-slate-400">
          Non collegato. Per vedere posizioni e ordini serve una chiave API OKX con il solo permesso «Leggi»,
          da inserire nel file <span className="num">backend/.env</span> del server.
        </p>
      </Card>
    );
  }

  const hasStop = (target: string) =>
    !!account?.configured && account.algo_orders.some((a) => a.inst === target && a.sl != null);
  const position = account?.configured ? account.positions.find((p) => p.inst === inst) : undefined;

  return (
    <Card title="Conto OKX · sola lettura" actions={refresh}>
      {error && <Alert tone="error">{error}</Alert>}
      {!account && !error && <p className="text-sm text-slate-500">Caricamento…</p>}
      {account?.configured && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
            <span className="num text-lg font-semibold text-slate-100">{fmtUsd(account.total_eq)}</span>
            {account.currencies.map((c) => (
              <span key={c.ccy} className="num text-xs text-slate-400">{c.ccy} {fmtNum(c.eq)} · disponibili {fmtNum(c.avail)}</span>
            ))}
          </div>

          {session?.status === 'attesa' && session.instrument === inst && position && (
            <Alert tone="info">Su OKX c'è una posizione aperta su {inst}: se è questo piano, premi «Ordine eseguito».</Alert>
          )}
          {session?.status === 'aperta' && session.instrument === inst && !position && (
            <Alert tone="info">Su OKX non risulta più una posizione aperta su {inst}: controlla le posizioni chiuse qui sotto e registra l'esito.</Alert>
          )}

          <section>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">Posizioni aperte</h3>
            {account.positions.length === 0 && <p className="text-sm text-slate-500">Nessuna</p>}
            <ul className="space-y-2">
              {account.positions.map((p) => {
                const dir = direction(p);
                return (
                  <li key={p.inst + p.pos_side} className={cn('rounded-lg bg-slate-950/60 p-2 text-sm', p.inst === inst && 'ring-1 ring-sky-800')}>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={cn('rounded px-1.5 text-xs font-semibold', dir === 'long' ? 'bg-emerald-900 text-emerald-200' : 'bg-rose-900 text-rose-200')}>
                        {dir.toUpperCase()}
                      </span>
                      <span className="text-slate-100">{p.inst}</span>
                      <span className="num text-xs text-slate-400">{fmtNum(Math.abs(p.pos), 2)} contratti · {p.lever}x {p.mgn_mode === 'isolated' ? 'isolato' : 'cross'}</span>
                      <span className={cn('num ml-auto', (p.upl ?? 0) >= 0 ? 'text-emerald-400' : 'text-rose-400')}>{fmtUsd(p.upl)}</span>
                    </div>
                    <div className="num mt-1 text-xs text-slate-400">
                      ingresso {fmtPrice(p.avg_px)} · mark {fmtPrice(p.mark_px)} · liquidazione {fmtPrice(p.liq_px)}
                    </div>
                    {!hasStop(p.inst) && <p className="mt-1 text-xs font-medium text-rose-300">Nessuno stop trovato su OKX per questa posizione.</p>}
                    {p.mgn_mode !== 'isolated' && <p className="mt-1 text-xs font-medium text-amber-300">Margine cross: la regola dice isolato.</p>}
                  </li>
                );
              })}
            </ul>
          </section>

          <section>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">Ordini in attesa</h3>
            {account.orders.length === 0 && account.algo_orders.length === 0 && <p className="text-sm text-slate-500">Nessuno</p>}
            <ul className="space-y-1 text-sm">
              {account.orders.map((o, i) => (
                <li key={`o${i}`} className="num text-xs text-slate-300">
                  {o.inst} · {o.side === 'buy' ? 'compra' : 'vendi'} {o.ord_type} {fmtPrice(o.px)} × {fmtNum(o.sz, 2)}
                  {o.sl != null && <span className="text-rose-300"> · stop {fmtPrice(o.sl)}</span>}
                  {o.tp != null && <span className="text-emerald-300"> · TP {fmtPrice(o.tp)}</span>}
                  {o.sl == null && <span className="font-medium text-rose-300"> · senza stop</span>}
                </li>
              ))}
              {account.algo_orders.map((a, i) => (
                <li key={`a${i}`} className="num text-xs text-slate-300">
                  {a.inst} · {a.ord_type === 'oco' ? 'stop + TP' : 'condizionale'} {a.side === 'buy' ? 'compra' : 'vendi'} {a.close_position ? 'tutta la posizione' : fmtNum(a.sz, 2)}
                  {a.sl != null && <span className="text-rose-300"> · stop {fmtPrice(a.sl)}</span>}
                  {a.tp != null && <span className="text-emerald-300"> · TP {fmtPrice(a.tp)}</span>}
                </li>
              ))}
            </ul>
          </section>

          <section>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">Ultime posizioni chiuse</h3>
            {account.history.length === 0 && <p className="text-sm text-slate-500">Nessuna</p>}
            <ul className="space-y-1">
              {account.history.slice(0, 8).map((h, i) => (
                <li key={i} className="flex items-center gap-2 text-xs">
                  <span className="num w-28 shrink-0 text-slate-500">{fmtDateTime(h.closed_at)}</span>
                  <span className="flex-1 text-slate-300">
                    {h.inst} {h.direction} · {fmtPrice(h.open_px)} → {fmtPrice(h.close_px)}{CLOSE_TYPES[h.close_type] && ` · ${CLOSE_TYPES[h.close_type]}`}
                  </span>
                  <span className={cn('num', (h.pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-rose-400')}>{fmtUsd(h.pnl)}</span>
                </li>
              ))}
            </ul>
          </section>
          <p className="text-xs text-slate-600">Aggiornato {fmtDateTime(account.updated_at)} · ogni 30 secondi a pagina aperta</p>
        </div>
      )}
    </Card>
  );
}
