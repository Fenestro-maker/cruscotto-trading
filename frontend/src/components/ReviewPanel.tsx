import { useEffect, useState } from 'react';
import { Check, Loader2, RefreshCw, X } from 'lucide-react';
import { errorMessage } from '@/api/client';
import type { ReviewResult, Session, SessionReview } from '@/api/client';
import { cn, fmtDateTime, fmtPrice } from '@/lib/format';
import { Alert, Button, Card } from './ui';
import ScoreBadge from './ScoreBadge';

const VERDICTS: Record<ReviewResult['verdict'], { label: string; tone: string }> = {
  mantieni: { label: 'Mantieni', tone: 'bg-emerald-900 text-emerald-100' },
  modifica: { label: 'Modifica i livelli', tone: 'bg-amber-900 text-amber-100' },
  annulla: { label: 'Annulla', tone: 'bg-rose-900 text-rose-100' },
};

function nextAutoReview(session: Session, hours: number): string | null {
  if (hours <= 0) return null;
  const from = new Date(session.last_review_at ?? session.created_at).getTime();
  return fmtDateTime(new Date(from + hours * 3600_000).toISOString());
}

function ReviewDetail({ review, onApply, busy }: { review: SessionReview; onApply: () => void; busy: boolean }) {
  const r = review.result;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className={cn('rounded-full px-2.5 py-1 text-xs font-semibold', VERDICTS[r.verdict].tone)}>{VERDICTS[r.verdict].label}</span>
        <ScoreBadge score={r.score} />
        <span className="num text-xs text-slate-500">
          {fmtDateTime(review.created_at)} · {review.trigger} · prezzo {fmtPrice(review.price)} · dopo {Math.round(review.hours)} ore
        </span>
      </div>
      <p className="text-sm text-slate-100">{r.summary}</p>
      {r.changes.length > 0 && (
        <div>
          <div className="text-xs font-medium uppercase tracking-wide text-slate-500">Cosa è cambiato</div>
          <ul className="list-disc pl-5 text-sm text-slate-300">{r.changes.map((c, i) => <li key={i}>{c}</li>)}</ul>
        </div>
      )}
      <ul className="space-y-1 text-sm">
        {r.score_for.map((t, i) => (
          <li key={`f${i}`} className="flex gap-2"><Check className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" aria-label="a favore" /><span>{t}</span></li>
        ))}
        {r.score_against.map((t, i) => (
          <li key={`c${i}`} className="flex gap-2"><X className="mt-0.5 h-4 w-4 shrink-0 text-rose-400" aria-label="contro" /><span className="text-slate-300">{t}</span></li>
        ))}
      </ul>
      {r.verdict === 'modifica' && r.entry != null && (
        <div className="rounded-lg border border-amber-900 p-3">
          <div className="num text-sm text-slate-200">
            Nuovi livelli: ingresso {fmtPrice(r.entry)} · stop {fmtPrice(r.stop)} · TP1 {fmtPrice(r.tp1)} · target {fmtPrice(r.target)}
          </div>
          {review.plan_issues.length > 0
            ? <p className="mt-2 text-xs text-amber-300">{review.plan_issues.join(' · ')}</p>
            : (
              <Button variant="secondary" className="mt-2" onClick={onApply} disabled={busy}>
                Applica i nuovi livelli
              </Button>
            )}
          <p className="mt-2 text-xs text-slate-500">Ricorda di spostare anche l'ordine su OKX.</p>
        </div>
      )}
      {r.verdict === 'annulla' && (
        <Alert tone="warn">Il ricontrollo consiglia di annullare: se sei d'accordo, cancella l'ordine su OKX e premi «Annulla l'ordine» qui sopra.</Alert>
      )}
    </div>
  );
}

export default function ReviewPanel({ session, reviewEveryHours, aiConfigured, busy, onReview, onApply }: {
  session: Session;
  reviewEveryHours: number;
  aiConfigured: boolean;
  busy: boolean;
  onReview: () => Promise<void>;
  onApply: (r: ReviewResult) => void;
}) {
  const [running, setRunning] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [showAll, setShowAll] = useState(false);
  const [error, setError] = useState('');
  const reviews = session.reviews ?? [];
  const last = reviews[reviews.length - 1];
  const next = nextAutoReview(session, reviewEveryHours);

  useEffect(() => {
    if (!running) return;
    const start = Date.now();
    const id = setInterval(() => setElapsed(Math.round((Date.now() - start) / 1000)), 1000);
    return () => { clearInterval(id); setElapsed(0); };
  }, [running]);

  const run = async () => {
    setRunning(true);
    setError('');
    try {
      await onReview();
    } catch (err) {
      setError(errorMessage(err, 'Ricontrollo non riuscito'));
    } finally {
      setRunning(false);
    }
  };

  return (
    <Card title="Ricontrollo dello scenario">
      <p className="mb-3 text-sm text-slate-400">
        Claude rilegge il mercato e lo confronta con il piano: vale ancora la pena aspettare l'ingresso, vanno cambiati i livelli o conviene annullare?
      </p>
      <Button className="w-full" onClick={run} disabled={running || busy || !aiConfigured}>
        {running ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <RefreshCw className="h-4 w-4" aria-hidden />}
        {running ? `Claude sta ricontrollando… ${elapsed}s` : 'Ricontrolla lo scenario'}
      </Button>
      {error && <div className="mt-3"><Alert tone="error">{error}</Alert></div>}
      <p className="mt-2 text-xs text-slate-500">
        {next
          ? `Ricontrollo automatico ogni ${reviewEveryHours} ore, con notifica su Telegram: il prossimo verso il ${next}.`
          : 'Ricontrollo automatico spento (si attiva in Impostazioni).'}
      </p>
      {last && (
        <div className="mt-4 border-t border-slate-800 pt-4">
          <ReviewDetail review={last} onApply={() => onApply(last.result)} busy={busy} />
        </div>
      )}
      {reviews.length > 1 && (
        <div className="mt-4">
          <button onClick={() => setShowAll((s) => !s)} className="text-xs text-sky-300 underline">
            {showAll ? 'Nascondi' : 'Mostra'} i ricontrolli precedenti ({reviews.length - 1})
          </button>
          {showAll && (
            <ul className="mt-2 space-y-1">
              {reviews.slice(0, -1).reverse().map((rv, i) => (
                <li key={i} className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
                  <span className="num">{fmtDateTime(rv.created_at)}</span>
                  <span className={cn('rounded-full px-2 py-0.5 font-semibold', VERDICTS[rv.result.verdict].tone)}>{VERDICTS[rv.result.verdict].label}</span>
                  <ScoreBadge score={rv.result.score} />
                  <span className="basis-full text-slate-300">{rv.result.summary}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Card>
  );
}
