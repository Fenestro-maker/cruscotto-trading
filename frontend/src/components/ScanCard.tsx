import { useEffect, useState } from 'react';
import { ChevronDown, Loader2, RefreshCw } from 'lucide-react';
import { errorMessage, scansApi, SETUP_LABELS } from '@/api/client';
import type { Scan } from '@/api/client';
import { cn, fmtDateTime, fmtPrice, instLabel } from '@/lib/format';
import { Alert, Card } from './ui';
import ScoreBadge from './ScoreBadge';

const SHOWN = 10;

/** Classifica del mattino: i candidati migliori su tutti gli strumenti, da approfondire con «Ragiona ora» */
export default function ScanCard({ current, onSelect, aiConfigured, scanTime, scanEnabled }: {
  current: string;
  onSelect: (inst: string) => void;
  aiConfigured: boolean;
  scanTime: string;
  scanEnabled: boolean;
}) {
  const [scan, setScan] = useState<Scan | null>(null);
  const [running, setRunning] = useState(false);
  const [open, setOpen] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    scansApi.latest().then(({ data }) => { setScan(data.scan); setRunning(data.running); }).catch(() => setScan(null));
  }, []);

  const runNow = async () => {
    setRunning(true);
    setError('');
    try {
      const { data } = await scansApi.run();
      setScan(data);
    } catch (err) {
      setError(errorMessage(err, 'Classifica non riuscita'));
    } finally {
      setRunning(false);
    }
  };

  const items = (scan?.items ?? []).filter((i) => i.direction !== 'none').slice(0, SHOWN);

  return (
    <Card
      title="Classifica del mattino"
      actions={(
        <div className="flex items-center gap-1">
          <button onClick={runNow} disabled={running || !aiConfigured}
            className="flex min-h-9 items-center gap-1 rounded-md px-2 text-xs text-slate-300 hover:bg-slate-800 disabled:opacity-50">
            {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : <RefreshCw className="h-3.5 w-3.5" aria-hidden />}
            {running ? 'In preparazione…' : 'Aggiorna ora'}
          </button>
          <button onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-label={open ? 'Chiudi la classifica' : 'Apri la classifica'}
            className="flex min-h-9 items-center rounded-md px-2 text-slate-400 hover:bg-slate-800">
            <ChevronDown className={cn('h-4 w-4 transition-transform', open && 'rotate-180')} aria-hidden />
          </button>
        </div>
      )}
    >
      {open && (
        <div className="space-y-3">
          <p className="text-xs text-slate-500">
            {scanEnabled ? `Ogni giorno alle ${scanTime}, con i primi 5 su Telegram` : 'Classifica automatica spenta (si attiva in Impostazioni)'}
            {scan && ` · ultima ${fmtDateTime(scan.created_at)}${scan.weekend ? ', weekend: solo crypto' : ''}`}
            {running && ' · ci vogliono uno o due minuti'}
          </p>
          {error && <Alert tone="error">{error}</Alert>}
          {scan?.status === 'errore' && <Alert tone="error">Ultima classifica non riuscita: {scan.error}</Alert>}
          {!scan && !running && <p className="text-sm text-slate-500">Ancora nessuna classifica.</p>}
          {scan?.market_note && <p className="text-sm text-slate-300">{scan.market_note}</p>}
          {scan?.status === 'ok' && items.length === 0 && (
            <p className="text-sm text-slate-400">Nessun candidato interessante: «nessun trade» è una scelta valida.</p>
          )}
          <ol className="space-y-1">
            {items.map((it, n) => (
              <li key={it.instrument}>
                <button
                  onClick={() => onSelect(it.instrument)}
                  className={cn('w-full rounded-lg px-2 py-2 text-left hover:bg-slate-800', it.instrument === current && 'bg-slate-800')}
                >
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="num w-5 text-slate-500">{n + 1}.</span>
                    <span className="font-medium text-slate-100">{instLabel(it.instrument)}</span>
                    <ScoreBadge score={it.score} />
                    <span className={cn('rounded px-1.5 text-xs font-semibold', it.direction === 'long' ? 'bg-emerald-900 text-emerald-200' : 'bg-rose-900 text-rose-200')}>
                      {it.direction.toUpperCase()}
                    </span>
                    <span className="text-xs text-slate-400">{SETUP_LABELS[it.setup]}</span>
                    {it.level != null && <span className="num text-xs text-slate-400">livello {fmtPrice(it.level)}</span>}
                  </div>
                  <p className="mt-0.5 pl-7 text-xs text-slate-400">{it.note}</p>
                </button>
              </li>
            ))}
          </ol>
          {scan?.status === 'ok' && (
            <p className="text-xs text-slate-600">
              Filtrati {scan.stats.analyzed ?? 0} strumenti, letti da Claude {scan.stats.to_ai ?? 0}. Tocca uno strumento per aprirlo, poi «Ragiona ora» per il piano completo.
            </p>
          )}
        </div>
      )}
    </Card>
  );
}
