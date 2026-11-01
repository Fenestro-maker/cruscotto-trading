import { useState } from 'react';
import { instLabel } from '@/lib/format';
import { Input, Select } from './ui';

// Oltre questo numero di strumenti compare la casella di ricerca
const SEARCH_FROM = 12;

const matches = (inst: string, query: string) =>
  inst.toLowerCase().includes(query) || instLabel(inst).toLowerCase().includes(query);

/** Scelta dello strumento: menu, con ricerca quando gli strumenti sono tanti */
export default function InstrumentPicker({ instruments, value, onChange }: {
  instruments: string[]; value: string; onChange: (inst: string) => void;
}) {
  const [query, setQuery] = useState('');
  const q = query.trim().toLowerCase();
  const filtered = q ? instruments.filter((i) => matches(i, q)) : instruments;
  const options = filtered.includes(value) || !value ? filtered : [value, ...filtered];

  const search = (text: string) => {
    setQuery(text);
    const found = instruments.filter((i) => matches(i, text.trim().toLowerCase()));
    // Appena la ricerca esclude lo strumento mostrato, passa al primo trovato
    if (text.trim() && found.length && !found.includes(value)) onChange(found[0]);
  };

  return (
    <div className="flex gap-2">
      {instruments.length > SEARCH_FROM && (
        <div className="w-28">
          <Input aria-label="Cerca strumento" placeholder="Cerca…" value={query} onChange={(e) => search(e.target.value)} />
        </div>
      )}
      <div className="w-52">
        <Select aria-label="Strumento" value={value} onChange={(e) => onChange(e.target.value)}>
          {options.map((i) => <option key={i} value={i}>{instLabel(i)}</option>)}
        </Select>
      </div>
      {q && filtered.length === 0 && <span className="self-center text-xs text-slate-500">nessuno</span>}
    </div>
  );
}
