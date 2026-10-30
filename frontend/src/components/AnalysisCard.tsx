import { useEffect, useState } from 'react';
import { Brain, Check, CheckCircle2, Loader2, X, XCircle } from 'lucide-react';
import type { Analysis } from '@/api/client';
import { SETUP_LABELS } from '@/api/client';
import { cn, fmtDateTime, fmtPrice, instLabel } from '@/lib/format';
import { Alert, Button, Card, Textarea } from './ui';
import ScoreBadge from './ScoreBadge';

export function ReasonPanel({ disabled, disabledReason, busy, onRun }: {
  disabled: boolean; disabledReason?: string; busy: boolean; onRun: (note: string) => void;
}) {
  const [note, setNote] = useState('');
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!busy) return;
    const start = Date.now();
    const id = setInterval(() => setElapsed(Math.round((Date.now() - start) / 1000)), 1000);
    return () => { clearInterval(id); setElapsed(0); };
  }, [busy]);

  return (
    <Card title="Ragiona ora">
      <p className="mb-3 text-sm text-slate-400">
        Claude legge le candele settimanali, giornaliere, 4h, 1h e 15m da OKX e propone il setup della sessione, oppure nessun trade.
      </p>
      <Textarea
        rows={2}
        placeholder="Nota facoltativa (es. «guardo solo long», «c'è il dato sull'inflazione alle 14:30»)"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        disabled={busy}
      />
      {disabled && disabledReason && <div className="mt-3"><Alert tone="warn">{disabledReason}</Alert></div>}
      <Button className="mt-3 w-full" onClick={() => onRun(note)} disabled={disabled || busy}>
        {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Brain className="h-4 w-4" aria-hidden />}
        {busy ? `Claude sta ragionando… ${elapsed}s` : 'Ragiona ora'}
      </Button>
      {busy && <p className="mt-2 text-xs text-slate-500">Di solito ci vogliono uno o due minuti.</p>}
    </Card>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <p className="text-sm text-slate-200">{children}</p>
    </div>
  );
}

export default function AnalysisCard({ analysis, onUsePlan, onNoTrade }: {
  analysis: Analysis; onUsePlan: () => void; onNoTrade: () => void;
}) {
  const r = analysis.result;
  const isTrade = r.decision === 'trade';
  const valid = isTrade && analysis.plan_issues.length === 0;
  return (
    <Card
      title={`Analisi · ${instLabel(analysis.instrument)}`}
      actions={<span className="text-xs text-slate-500">{fmtDateTime(analysis.created_at)}</span>}
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className={cn('rounded-full px-2.5 py-1 text-xs font-semibold',
            isTrade ? (r.side === 'long' ? 'bg-emerald-900 text-emerald-200' : 'bg-rose-900 text-rose-200') : 'bg-slate-800 text-slate-300')}>
            {isTrade ? `${r.side.toUpperCase()} · ${SETUP_LABELS[r.setup]}` : 'Nessun trade'}
          </span>
          {r.score == null && r.confidence && <span className="rounded-full bg-slate-800 px-2.5 py-1 text-xs text-slate-300">Fiducia {r.confidence}</span>}
          {analysis.rr != null && <span className="num rounded-full bg-slate-800 px-2.5 py-1 text-xs text-slate-300">R:R {analysis.rr}:1</span>}
          <span className="num text-xs text-slate-500">prezzo all'analisi {fmtPrice(analysis.price)}</span>
        </div>

        {r.score != null && (
          <div className="rounded-lg border border-slate-800 p-3">
            <div className="flex items-center gap-3">
              <ScoreBadge score={r.score} size="lg" />
              <div>
                <div className="text-sm font-medium text-slate-100">Fattibilità del trade</div>
                <div className="text-xs text-slate-500">più conferme convergono, più il voto è alto</div>
              </div>
            </div>
            {(r.score_for?.length || r.score_against?.length) ? (
              <ul className="mt-3 space-y-1 text-sm">
                {r.score_for?.map((t, i) => (
                  <li key={`f${i}`} className="flex gap-2"><Check className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" aria-label="a favore" /><span>{t}</span></li>
                ))}
                {r.score_against?.map((t, i) => (
                  <li key={`c${i}`} className="flex gap-2"><X className="mt-0.5 h-4 w-4 shrink-0 text-rose-400" aria-label="contro" /><span className="text-slate-300">{t}</span></li>
                ))}
              </ul>
            ) : null}
          </div>
        )}

        <p className="text-sm font-medium text-slate-100">{r.summary}</p>

        {isTrade && (
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {([['Ingresso', r.entry], ['Stop', r.stop], ['TP1', r.tp1], ['Target', r.target]] as const).map(([l, v]) => (
              <div key={l} className="rounded-lg bg-slate-950/60 px-3 py-2">
                <div className="text-xs text-slate-500">{l}</div>
                <div className="num text-sm font-medium">{fmtPrice(v)}</div>
              </div>
            ))}
          </div>
        )}
        {analysis.plan_issues.length > 0 && <Alert tone="warn">{analysis.plan_issues.join(' · ')}</Alert>}

        <div className="grid gap-3 sm:grid-cols-2">
          <Row label="Settimanale">{r.weekly}</Row>
          <Row label="Giornaliero">{r.daily}</Row>
          <Row label="Struttura 4h / 1h">{r.structure}</Row>
          <Row label="Timing 15m">{r.timing}</Row>
        </div>

        {r.key_levels.length > 0 && (
          <div>
            <div className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">Livelli chiave</div>
            <ul className="space-y-1 text-sm">
              {[...r.key_levels].sort((a, b) => b.price - a.price).map((lv, i) => (
                <li key={i} className="flex gap-2">
                  <span className={cn('num w-24 shrink-0', lv.kind === 'resistenza' ? 'text-rose-300' : lv.kind === 'supporto' ? 'text-emerald-300' : 'text-slate-300')}>
                    {fmtPrice(lv.price)}
                  </span>
                  <span className="text-slate-400">{lv.kind} · {lv.note}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        <Row label="Bias">{r.bias}</Row>
        <Row label="Invalidazione">{r.invalidation}</Row>
        {r.cancel_conditions.length > 0 && (
          <div>
            <div className="text-xs font-medium uppercase tracking-wide text-slate-500">Annulla se</div>
            <ul className="list-disc pl-5 text-sm text-slate-200">{r.cancel_conditions.map((c, i) => <li key={i}>{c}</li>)}</ul>
          </div>
        )}
        {r.risks.length > 0 && (
          <div>
            <div className="text-xs font-medium uppercase tracking-wide text-slate-500">Rischi</div>
            <ul className="list-disc pl-5 text-sm text-slate-300">{r.risks.map((c, i) => <li key={i}>{c}</li>)}</ul>
          </div>
        )}

        <div className="flex flex-wrap gap-2">
          {isTrade ? (
            <Button onClick={onUsePlan} variant={valid ? 'primary' : 'secondary'}>
              {valid ? <CheckCircle2 className="h-4 w-4" aria-hidden /> : <XCircle className="h-4 w-4" aria-hidden />}
              {valid ? 'Usa questo piano' : 'Correggi il piano'}
            </Button>
          ) : (
            <Button onClick={onNoTrade} variant="secondary">Registra «nessun trade»</Button>
          )}
        </div>
        <p className="text-xs text-slate-600">
          {analysis.usage.model} · {analysis.usage.input_tokens.toLocaleString('it-IT')} token in ingresso, {analysis.usage.output_tokens.toLocaleString('it-IT')} in uscita
        </p>
      </div>
    </Card>
  );
}
