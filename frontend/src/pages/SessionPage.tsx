import { useCallback, useEffect, useState } from 'react';
import { analysisApi, errorMessage, marketApi, sessionsApi, settingsApi } from '@/api/client';
import type { Analysis, DailyStatus, InstrumentInfo, Outcome, PlanInput, Session, Settings, Ticker } from '@/api/client';
import PriceChart from '@/components/PriceChart';
import type { ChartLevel } from '@/components/PriceChart';
import AnalysisCard, { ReasonPanel } from '@/components/AnalysisCard';
import PlanForm from '@/components/PlanForm';
import { EMPTY_FORM, formFromAnalysis, formFromPlan, planFromForm } from '@/lib/planForm';
import type { PlanFormState } from '@/lib/planForm';
import RiskPanel from '@/components/RiskPanel';
import AccountPanel from '@/components/AccountPanel';
import ActiveSession from '@/components/ActiveSession';
import { Alert, Button, Card, Select } from '@/components/ui';
import { planProblems } from '@/lib/risk';
import { cn, fmtPct, fmtPrice, instLabel } from '@/lib/format';

const PRICE_REFRESH_MS = 5000;

function levelsOf(plan: Pick<PlanInput, 'entry' | 'stop' | 'tp1' | 'target' | 'cancel_above' | 'cancel_below'>): ChartLevel[] {
  const out: ChartLevel[] = [];
  const add = (price: number | null, label: string, color: string, dashed = false) => {
    if (price != null) out.push({ price, label, color, dashed });
  };
  add(plan.cancel_above, 'annulla sopra', '#64748b', true);
  add(plan.target, 'target', '#10b981');
  add(plan.tp1, 'TP1', '#6ee7b7');
  add(plan.entry, 'ingresso', '#38bdf8');
  add(plan.stop, 'stop', '#f43f5e');
  add(plan.cancel_below, 'annulla sotto', '#64748b', true);
  return out;
}

async function fetchAll() {
  const [s, d, a] = await Promise.all([sessionsApi.list(), sessionsApi.today(), analysisApi.list()]);
  return { sessions: s.data, daily: d.data, analyses: a.data };
}

export default function SessionPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [inst, setInst] = useState(() => localStorage.getItem('inst') ?? '');
  const [ticker, setTicker] = useState<Ticker | null>(null);
  const [priceError, setPriceError] = useState('');
  const [info, setInfo] = useState<InstrumentInfo | null>(null);
  const [daily, setDaily] = useState<DailyStatus | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [analyses, setAnalyses] = useState<Analysis[]>([]);
  const [form, setForm] = useState<PlanFormState | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [reasoning, setReasoning] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const apply = useCallback((data: Awaited<ReturnType<typeof fetchAll>>) => {
    setSessions(data.sessions);
    setDaily(data.daily);
    setAnalyses(data.analyses);
  }, []);
  const reload = useCallback(() => fetchAll().then(apply), [apply]);

  useEffect(() => {
    settingsApi.get().then(({ data }) => {
      setSettings(data);
      setInst((cur) => (data.instruments.includes(cur) ? cur : data.instruments[0]));
    }).catch((err) => setError(errorMessage(err)));
    fetchAll().then(apply).catch((err) => setError(errorMessage(err)));
  }, [apply]);

  const active = sessions.find((s) => s.status === 'attesa' || s.status === 'aperta') ?? null;
  // La sessione in corso decide lo strumento mostrato
  const shownInst = active?.instrument ?? inst;

  useEffect(() => {
    if (!shownInst) return;
    localStorage.setItem('inst', shownInst);
    let stopped = false;
    const load = () => {
      if (document.hidden) return;
      marketApi.ticker(shownInst)
        .then(({ data }) => { if (!stopped) { setTicker(data); setPriceError(''); } })
        .catch((err) => !stopped && setPriceError(errorMessage(err, 'Prezzo non disponibile')));
    };
    load();
    const id = setInterval(load, PRICE_REFRESH_MS);
    marketApi.instrument(shownInst).then(({ data }) => !stopped && setInfo(data)).catch(() => !stopped && setInfo(null));
    return () => { stopped = true; clearInterval(id); setTicker(null); };
  }, [shownInst]);

  const latest = analyses.find((a) => a.instrument === shownInst) ?? null;
  const draft = form ? planFromForm(form, shownInst) : null;
  const problems = draft && settings ? planProblems(draft, settings.rules.min_rr) : [];

  const levels = draft ? levelsOf(draft)
    : active ? levelsOf(active)
      : latest?.result.decision === 'trade' ? levelsOf(latest.result) : [];

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError('');
    try {
      await fn();
      await reload();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const runAnalysis = async (note: string) => {
    setReasoning(true);
    setError('');
    try {
      const { data } = await analysisApi.run(shownInst, note);
      setAnalyses((list) => [data, ...list]);
      setForm(null);
    } catch (err) {
      setError(errorMessage(err, 'Analisi non riuscita'));
    } finally {
      setReasoning(false);
    }
  };

  const submitPlan = () => act(async () => {
    if (!draft) return;
    if (editingId) await sessionsApi.update(editingId, draft);
    else await sessionsApi.create(draft);
    setForm(null);
    setEditingId(null);
  });

  const recordNoTrade = (analysis: Analysis | null) => act(async () => {
    await sessionsApi.create({
      ...planFromForm(EMPTY_FORM, shownInst),
      side: 'none', setup: 'nessuno',
      bias: analysis?.result.bias ?? '',
      note: analysis?.result.summary ?? '',
      analysis_id: analysis?.id ?? null,
    });
  });

  if (!settings) {
    return error ? <Alert tone="error">{error}</Alert> : <p className="text-sm text-slate-500">Caricamento…</p>;
  }

  const change = ticker ? ((ticker.last - ticker.open24h) / ticker.open24h) * 100 : null;
  const stoppedToday = daily?.stopped ?? false;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="w-56">
          <Select
            aria-label="Strumento"
            value={shownInst}
            disabled={!!active}
            onChange={(e) => { setInst(e.target.value); setForm(null); }}
          >
            {settings.instruments.map((i) => <option key={i} value={i}>{instLabel(i)}</option>)}
          </Select>
        </div>
        <div className="flex items-baseline gap-2">
          <span className="num text-2xl font-semibold text-slate-50">{fmtPrice(ticker?.last)}</span>
          {change != null && <span className={cn('num text-sm', change >= 0 ? 'text-emerald-400' : 'text-rose-400')}>{fmtPct(change)} 24h</span>}
        </div>
        {daily && (
          <span className={cn('rounded-full px-3 py-1 text-xs', stoppedToday ? 'bg-rose-900 text-rose-100' : 'bg-slate-800 text-slate-300')}>
            {daily.message}
          </span>
        )}
      </div>
      {priceError && <Alert tone="warn">{priceError}</Alert>}
      {error && <Alert tone="error">{error}</Alert>}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
        <div className="space-y-4">
          <Card><PriceChart inst={shownInst} lastPrice={ticker?.last ?? null} levels={levels} /></Card>
          {!active && latest && (
            <AnalysisCard
              analysis={latest}
              onUsePlan={() => { setEditingId(null); setForm(formFromAnalysis(latest)); }}
              onNoTrade={() => recordNoTrade(latest)}
            />
          )}
        </div>

        <div className="space-y-4">
          {active && !form && (
            <ActiveSession
              session={active}
              price={ticker?.last ?? null}
              busy={busy}
              onFill={() => act(() => sessionsApi.fill(active.id))}
              onCancel={(reason) => act(() => sessionsApi.cancel(active.id, reason))}
              onClose={(outcome: Outcome, exitPrice?: number, tp1Taken?: boolean) =>
                act(() => sessionsApi.close(active.id, { outcome, exit_price: exitPrice, tp1_taken: tp1Taken }))}
              onEdit={() => { setEditingId(active.id); setForm(formFromPlan(active)); }}
            />
          )}
          {!active && !form && (
            <>
              <ReasonPanel
                busy={reasoning}
                onRun={runAnalysis}
                disabled={stoppedToday || !settings.ai_configured}
                disabledReason={stoppedToday ? daily?.message : 'Manca ANTHROPIC_API_KEY nel file .env del server.'}
              />
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" onClick={() => { setEditingId(null); setForm(EMPTY_FORM); }} disabled={stoppedToday}>Piano manuale</Button>
                <Button variant="ghost" onClick={() => recordNoTrade(null)} disabled={busy}>Nessun trade oggi</Button>
              </div>
            </>
          )}
          {form && (
            <PlanForm
              value={form}
              onChange={setForm}
              problems={problems}
              busy={busy}
              editing={!!editingId}
              onSubmit={submitPlan}
              onCancel={() => { setForm(null); setEditingId(null); }}
            />
          )}
          <RiskPanel plan={draft ?? active} settings={settings} daily={daily} info={info} />
          <AccountPanel inst={shownInst} session={active} />
        </div>
      </div>
    </div>
  );
}
