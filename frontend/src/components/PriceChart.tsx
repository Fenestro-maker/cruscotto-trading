import { useEffect, useRef, useState } from 'react';
import { CandlestickSeries, ColorType, LineStyle, createChart } from 'lightweight-charts';
import type { CandlestickData, IChartApi, IPriceLine, ISeriesApi, UTCTimestamp } from 'lightweight-charts';
import { errorMessage, marketApi } from '@/api/client';
import type { Candle, Timeframe } from '@/api/client';
import { cn } from '@/lib/format';

export interface ChartLevel {
  price: number;
  label: string;
  color: string;
  dashed?: boolean;
}

const TIMEFRAMES: Timeframe[] = ['1W', '1D', '4H', '1H', '15m'];

const toBar = (c: Candle): CandlestickData<UTCTimestamp> => ({
  time: (c.t / 1000) as UTCTimestamp,
  open: c.o, high: c.h, low: c.l, close: c.c,
});

// Orari del grafico in ora italiana (la libreria di suo usa UTC)
const rome = (opts: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat('it-IT', { timeZone: 'Europe/Rome', ...opts });
const FMT_FULL = rome({ day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' });
const FMT_TICK = [
  rome({ year: 'numeric' }),             // anno
  rome({ month: 'short' }),              // mese
  rome({ day: 'numeric', month: 'short' }), // giorno
  rome({ hour: '2-digit', minute: '2-digit' }), // ora
  rome({ hour: '2-digit', minute: '2-digit', second: '2-digit' }),
];
const asDate = (t: unknown) => new Date((t as number) * 1000);

export default function PriceChart({ inst, lastPrice, levels }: { inst: string; lastPrice: number | null; levels: ChartLevel[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const linesRef = useRef<IPriceLine[]>([]);
  const lastBarRef = useRef<CandlestickData<UTCTimestamp> | null>(null);
  const [tf, setTf] = useState<Timeframe>('1H');
  const [error, setError] = useState('');

  useEffect(() => {
    const chart = createChart(containerRef.current!, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#94a3b8', attributionLogo: false },
      grid: { vertLines: { color: '#1e293b' }, horzLines: { color: '#1e293b' } },
      rightPriceScale: { borderColor: '#334155' },
      timeScale: {
        borderColor: '#334155', timeVisible: true, secondsVisible: false,
        tickMarkFormatter: (t: unknown, type: number) => FMT_TICK[type]?.format(asDate(t)) ?? '',
      },
      localization: { locale: 'it-IT', timeFormatter: (t: unknown) => FMT_FULL.format(asDate(t)) },
      crosshair: { mode: 0 },
    });
    seriesRef.current = chart.addSeries(CandlestickSeries, {
      upColor: '#10b981', downColor: '#f43f5e', borderVisible: false, wickUpColor: '#10b981', wickDownColor: '#f43f5e',
    });
    chartRef.current = chart;
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      linesRef.current = [];
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    lastBarRef.current = null;
    marketApi.candles(inst, tf)
      .then(({ data }) => {
        if (cancelled || !seriesRef.current) return;
        const bars = data.map(toBar);
        seriesRef.current.setData(bars);
        lastBarRef.current = bars[bars.length - 1] ?? null;
        chartRef.current?.timeScale().fitContent();
        setError('');
      })
      .catch((err) => !cancelled && setError(errorMessage(err, 'Candele non disponibili')));
    return () => { cancelled = true; };
  }, [inst, tf]);

  // Aggiorna l'ultima candela con il prezzo in tempo reale
  useEffect(() => {
    const bar = lastBarRef.current;
    if (!bar || lastPrice == null || !seriesRef.current) return;
    const updated = { ...bar, close: lastPrice, high: Math.max(bar.high, lastPrice), low: Math.min(bar.low, lastPrice) };
    lastBarRef.current = updated;
    seriesRef.current.update(updated);
  }, [lastPrice]);

  const levelsKey = JSON.stringify(levels);
  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    linesRef.current.forEach((l) => series.removePriceLine(l));
    linesRef.current = (JSON.parse(levelsKey) as ChartLevel[]).map((lv) => series.createPriceLine({
      price: lv.price, color: lv.color, title: lv.label, lineWidth: 1,
      lineStyle: lv.dashed ? LineStyle.Dashed : LineStyle.Solid, axisLabelVisible: true,
    }));
  }, [levelsKey]);

  return (
    <div>
      <div className="mb-2 flex gap-1" role="tablist" aria-label="Timeframe">
        {TIMEFRAMES.map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={t === tf}
            onClick={() => setTf(t)}
            className={cn('min-h-9 rounded-md px-3 text-xs font-medium', t === tf ? 'bg-slate-700 text-white' : 'text-slate-400 hover:bg-slate-800')}
          >
            {t}
          </button>
        ))}
      </div>
      {error && <p className="mb-2 text-sm text-rose-300">{error}</p>}
      <div ref={containerRef} className="h-[320px] w-full sm:h-[420px]" />
    </div>
  );
}
