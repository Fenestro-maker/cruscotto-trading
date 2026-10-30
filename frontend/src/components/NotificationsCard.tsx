import { useEffect, useState } from 'react';
import { errorMessage, notificationsApi } from '@/api/client';
import type { NotificationItem } from '@/api/client';
import { fmtDateTime } from '@/lib/format';
import { Alert, Button, Card } from './ui';

// Le notifiche usano <b> per Telegram: qui si mostra il testo semplice
const plain = (text: string) => text.replace(/<\/?b>/g, '');

export default function NotificationsCard({ configured }: { configured: boolean }) {
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    notificationsApi.list().then(({ data }) => setItems(data)).catch(() => setItems([]));
  }, []);

  const test = async () => {
    setBusy(true);
    setMessage(null);
    try {
      await notificationsApi.test();
      setMessage({ tone: 'ok', text: 'Notifica inviata: controlla Telegram.' });
    } catch (err) {
      setMessage({ tone: 'error', text: errorMessage(err) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Notifiche Telegram">
      <p className="text-sm text-slate-300">
        Ogni 20 secondi il server controlla il conto OKX e ti scrive quando un ordine viene eseguito, quando scattano
        stop o take profit (anche parziale) e quando una posizione supera la soglia di guadagno.
      </p>
      {!configured && (
        <div className="mt-3"><Alert tone="warn">Non attive: mancano TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID nel file backend/.env.</Alert></div>
      )}
      {message && <div className="mt-3"><Alert tone={message.tone}>{message.text}</Alert></div>}
      <Button variant="secondary" className="mt-3" onClick={test} disabled={busy || !configured}>Invia notifica di prova</Button>
      <h3 className="mb-1 mt-4 text-xs font-semibold uppercase tracking-wide text-slate-400">Ultime notifiche</h3>
      {items.length === 0 && <p className="text-sm text-slate-500">Nessuna</p>}
      <ul className="space-y-2">
        {items.map((n, i) => (
          <li key={i} className="rounded-lg bg-slate-950/60 p-2 text-xs">
            <div className="text-slate-500">{fmtDateTime(n.created_at)}{!n.sent && <span className="text-rose-300"> · non inviata: {n.error}</span>}</div>
            <div className="whitespace-pre-line text-slate-200">{plain(n.text)}</div>
          </li>
        ))}
      </ul>
    </Card>
  );
}
