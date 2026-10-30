import { cn } from '@/lib/format';

/** Voto di fattibilità 1-10: rosso fino a 5, ambra 6-7, verde da 8 */
export default function ScoreBadge({ score, size = 'sm' }: { score: number; size?: 'sm' | 'lg' }) {
  const tone = score >= 8 ? 'bg-emerald-900 text-emerald-100' : score >= 6 ? 'bg-amber-900 text-amber-100' : 'bg-rose-950 text-rose-200';
  return (
    <span
      className={cn('num inline-flex items-baseline gap-0.5 rounded-lg font-semibold', tone, size === 'lg' ? 'px-3 py-1.5 text-2xl' : 'px-2 py-0.5 text-xs')}
      title="Voto di fattibilità"
    >
      {score}<span className={cn('font-normal opacity-70', size === 'lg' ? 'text-sm' : 'text-[10px]')}>/10</span>
    </span>
  );
}
