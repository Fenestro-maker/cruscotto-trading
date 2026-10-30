import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronDown, Download, Trash2, Upload } from 'lucide-react';
import { errorMessage, SETUP_LABELS, sessionsApi } from '@/api/client';
import type { SessionStatus, Session, SetupKey, Stats, StatsGroup } from '@/api/client';
import { Alert, Button, Card, Stat, Textarea } from '@/components/ui';
import ScoreBadge from '@/components/ScoreBadge';
import { cn, downloadBlob, fmtDateTime, fmtNum, fmtPrice, fmtR, instLabel } from '@/lib/format';

const STATUS_LABELS: Record<SessionStatus, string> = {
  attesa: 'In attesa', aperta: 'Aperta', chiusa: 'Chiusa', annullata: 'Annullata', notrade: 'Nessun trade',
};

const OUTCOME_LABELS: Record<string, string> = {
  stop: 'Stop', tp1_pareggio: 'TP1 + pareggio', tp1_target: 'TP1 + target', manuale: 'Uscita manuale',
};

function GroupTable({ title, rows, label }: { title: string; rows: StatsGroup[]; label: (k: string) => string }) {
  return (
    <Card title={title}>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-slate-500">
            <tr><th className="py-1 pr-3 font-medium"></th><th className="pr-3 font-medium">Sessioni</th><th className="pr-3 font-medium">Trade</th><th className="pr-3 font-medium">Win rate</th><th className="font-medium">Totale</th></tr>
          </thead>
          <tbody>
            {rows.map((g) => (
              <tr key={g.key} className="border-t border-slate-800">
                <td className="py-1.5 pr-3">{label(g.key ?? '-')}</td>
                <td className="num pr-3">{g.sessions}</td>
                <td className="num pr-3">{g.trades}</td>
                <td className="num pr-3">{g.win_rate != null ? `${fmtNum(g.win_rate, 0)}%` : '—'}</td>
                <td className={cn('num', g.total_r > 0 ? 'text-emerald-400' : g.total_r < 0 ? 'text-rose-400' : '')}>{fmtR(g.total_r)}</td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={5} className="py-2 text-slate-500">Ancora nessuna sessione</td></tr>}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function SessionRow({ s, onSaveNote, onDelete }: { s: Session; onSaveNote: (note: string) => Promise<void>; onDelete: () => void }) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState(s.note);
  const [saving, setSaving] = useState(false);
  return (
    <li className="border-t border-slate-800">
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open} className="flex min-h-12 w-full items-center gap-3 py-2 text-left text-sm">
        <span className="num w-8 text-slate-500">#{s.n}</span>
        <span className="flex-1">
          <span className="block text-slate-100">
            {instLabel(s.instrument)} · {s.side === 'none' ? 'Nessun trade' : `${s.side.toUpperCase()} · ${SETUP_LABELS[s.setup as SetupKey] ?? s.setup}`}
          </span>
          <span className="block text-xs text-slate-500">{fmtDateTime(s.created_at)} · {STATUS_LABELS[s.status]}{s.outcome ? ` · ${OUTCOME_LABELS[s.outcome]}` : ''}</span>
        </span>
        {s.score != null && <ScoreBadge score={s.score} />}
        <span className={cn('num text-sm', (s.r ?? 0) > 0 ? 'text-emerald-400' : (s.r ?? 0) < 0 ? 'text-rose-400' : 'text-slate-400')}>
          {s.status === 'chiusa' ? fmtR(s.r) : ''}
        </span>
        <ChevronDown className={cn('h-4 w-4 text-slate-500 transition-transform', open && 'rotate-180')} aria-hidden />
      </button>
      {open && (
        <div className="space-y-3 pb-4">
          {s.side !== 'none' && (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
              <Stat label="Ingresso" value={fmtPrice(s.entry)} />
              <Stat label="Stop" value={fmtPrice(s.stop)} />
              <Stat label="TP1" value={fmtPrice(s.tp1)} />
              <Stat label="Target" value={fmtPrice(s.target)} />
              <Stat label="Uscita" value={fmtPrice(s.exit)} />
            </div>
          )}
          {s.bias && <p className="text-sm text-slate-300"><span className="text-slate-500">Bias: </span>{s.bias}</p>}
          {s.cancel_reason && <p className="text-sm text-slate-300"><span className="text-slate-500">Annullata: </span>{s.cancel_reason}</p>}
          <Textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Nota della sessione: cosa ha funzionato, cosa no" />
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" disabled={saving || note === s.note} onClick={async () => { setSaving(true); await onSaveNote(note); setSaving(false); }}>
              Salva nota
            </Button>
            <Button variant="ghost" onClick={onDelete}><Trash2 className="h-4 w-4" aria-hidden />Elimina</Button>
          </div>
        </div>
      )}
    </li>
  );
}

export default function JournalPage() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(() => Promise.all([sessionsApi.list(), sessionsApi.stats()])
    .then(([s, st]) => { setSessions(s.data); setStats(st.data); })
    .catch((err) => setError(errorMessage(err))), []);

  useEffect(() => {
    Promise.all([sessionsApi.list(), sessionsApi.stats()])
      .then(([s, st]) => { setSessions(s.data); setStats(st.data); })
      .catch((err) => setError(errorMessage(err)));
  }, []);

  const exportAs = async (format: 'csv' | 'json') => {
    try {
      const { data } = await sessionsApi.export(format);
      downloadBlob(data, `diario-${new Date().toISOString().slice(0, 10)}.${format}`);
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  const importFile = async (file: File) => {
    setError('');
    try {
      const items = JSON.parse(await file.text());
      if (!Array.isArray(items)) throw new Error('formato');
      const { data } = await sessionsApi.import(items);
      setNotice(`Importate ${data.added} sessioni${data.skipped ? `, ${data.skipped} già presenti` : ''}.`);
      await load();
    } catch (err) {
      setError(err instanceof SyntaxError || (err instanceof Error && err.message === 'formato')
        ? 'File non valido: serve un JSON esportato dal diario'
        : errorMessage(err));
    }
  };

  const o = stats?.overall;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-semibold text-slate-100">Diario delle sessioni</h1>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={() => exportAs('csv')}><Download className="h-4 w-4" aria-hidden />CSV</Button>
          <Button variant="secondary" onClick={() => exportAs('json')}><Download className="h-4 w-4" aria-hidden />JSON</Button>
          <Button variant="secondary" onClick={() => fileRef.current?.click()}><Upload className="h-4 w-4" aria-hidden />Importa JSON</Button>
          <input ref={fileRef} type="file" accept="application/json,.json" className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) importFile(f); e.target.value = ''; }} />
        </div>
      </div>
      {error && <Alert tone="error">{error}</Alert>}
      {notice && <Alert tone="ok">{notice}</Alert>}

      {o && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
          <Stat label="Sessioni" value={o.sessions} />
          <Stat label="Trade eseguiti" value={o.trades} />
          <Stat label="Win rate" value={o.win_rate != null ? `${fmtNum(o.win_rate, 0)}%` : '—'} />
          <Stat label="Totale" value={fmtR(o.total_r)} tone={o.total_r > 0 ? 'up' : o.total_r < 0 ? 'down' : undefined} />
          <Stat label="Media per trade" value={fmtR(o.avg_r)} />
        </div>
      )}
      {o && o.sessions < 20 && (
        <p className="text-xs text-slate-500">Il metodo si valuta dopo 20–30 sessioni: per ora i numeri sono indicativi.</p>
      )}

      {stats && (
        <div className="grid gap-4 md:grid-cols-2">
          <GroupTable title="Per strumento" rows={stats.by_instrument} label={instLabel} />
          <GroupTable title="Per setup" rows={stats.by_setup} label={(k) => SETUP_LABELS[k as SetupKey] ?? k} />
          <GroupTable title="Per voto di fattibilità" rows={stats.by_score} label={(k) => (k === '-' ? 'Senza voto' : `Voto ${k}`)} />
        </div>
      )}

      <Card title="Sessioni">
        <ul>
          {sessions.map((s) => (
            <SessionRow
              key={s.id}
              s={s}
              onSaveNote={async (note) => {
                try { await sessionsApi.setNote(s.id, note); await load(); } catch (err) { setError(errorMessage(err)); }
              }}
              onDelete={async () => {
                if (!window.confirm(`Eliminare la sessione #${s.n}?`)) return;
                try { await sessionsApi.delete(s.id); await load(); } catch (err) { setError(errorMessage(err)); }
              }}
            />
          ))}
          {sessions.length === 0 && <li className="py-2 text-sm text-slate-500">Ancora nessuna sessione.</li>}
        </ul>
      </Card>
    </div>
  );
}
