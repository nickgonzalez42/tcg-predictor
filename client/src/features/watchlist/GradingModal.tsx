import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import Modal from "../../app/shared/components/Modal";
import CardLoader from "../../app/shared/components/CardLoader";
import { useFetchGradingCandidatesQuery, type GradingCandidate } from "./watchlistApi";
import { currencyFormat, gameKey } from "../../lib/util";
import { fallbackToCardBack } from "../../lib/cardImages";

// "Recommend grading" (2026-10-25, portfolio header): the user's Near Mint cards with the
// latest price at every PSA tier, ranked by what grading would add. Gains
// are per copy and net of the grading fee the user sets; the table sorts by
// the best tier or by any single grade.
const TIERS = [
    { key: 'grade7', label: 'Grade 7' },
    { key: 'grade8', label: 'Grade 8' },
    { key: 'grade9', label: 'Grade 9' },
    { key: 'grade95', label: 'Grade 9.5' },
    { key: 'psa10', label: 'PSA 10' },
] as const;
type TierKey = typeof TIERS[number]['key'];
type Metric = 'usd' | 'pct';
type SortKey = 'best' | 'nm' | 'name' | TierKey;
const DEFAULT_FEE = 25;

type Row = GradingCandidate & {
    gains: Record<TierKey, { usd: number; pct: number } | null>;
    best: { tier: TierKey; usd: number; pct: number } | null;
};

export default function GradingModal({ onClose }: { onClose: () => void }) {
    const { data, isLoading } = useFetchGradingCandidatesQuery();
    const [metric, setMetric] = useState<Metric>('usd');
    const [fee, setFee] = useState(String(DEFAULT_FEE));
    const [sort, setSort] = useState<{ key: SortKey; dir: 'asc' | 'desc' }>({ key: 'best', dir: 'desc' });

    const feeUsd = Math.max(0, Number(fee) || 0);
    const rows = useMemo<Row[]>(() => (data?.cards ?? []).map(c => {
        const gains = {} as Row['gains'];
        let best: Row['best'] = null;
        for (const t of TIERS) {
            const price = c.grades[t.key];
            if (price == null) { gains[t.key] = null; continue; }
            const usd = price - c.nm - feeUsd;
            const g = { usd, pct: c.nm > 0 ? (usd / c.nm) * 100 : 0 };
            gains[t.key] = g;
            if (!best || g[metric] > best[metric]) best = { tier: t.key, ...g };
        }
        return { ...c, gains, best };
    }), [data, feeUsd, metric]);

    const sorted = useMemo(() => {
        const dir = sort.dir === 'asc' ? 1 : -1;
        const val = (r: Row): number | string | null => {
            if (sort.key === 'name') return r.name;
            if (sort.key === 'nm') return r.nm;
            if (sort.key === 'best') return r.best ? r.best[metric] : null;
            return r.gains[sort.key] ? r.gains[sort.key]![metric] : null;
        };
        return [...rows].sort((a, b) => {
            const x = val(a), y = val(b);
            if (x == null && y == null) return 0;
            if (x == null) return 1;            // rows without that grade's price sink
            if (y == null) return -1;
            if (typeof x === 'string' || typeof y === 'string') return String(x).localeCompare(String(y)) * dir;
            return (x - y) * dir;
        });
    }, [rows, sort, metric]);

    const toggle = (key: SortKey, defaultDir: 'asc' | 'desc' = 'desc') =>
        setSort(s => s.key === key ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: defaultDir });
    const arrow = (key: SortKey) => sort.key === key ? (sort.dir === 'asc' ? ' ↑' : ' ↓') : '';
    const th = (label: string, key: SortKey, opts: { defaultDir?: 'asc' | 'desc'; num?: boolean; title?: string } = {}) => (
        <th className={`sortable${opts.num ?? true ? ' screener__num' : ''}`} scope="col"
            aria-sort={sort.key === key ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
            title={opts.title} onClick={() => toggle(key, opts.defaultDir)}>
            {label}{arrow(key)}
        </th>
    );
    const gainText = (g: { usd: number; pct: number } | null) =>
        g == null ? '' : metric === 'usd'
            ? `${g.usd >= 0 ? '+' : '−'}${currencyFormat(Math.abs(g.usd))}`
            : `${g.pct >= 0 ? '+' : '−'}${Math.abs(g.pct).toFixed(0)}%`;
    const tierLabel = (k: TierKey) => TIERS.find(t => t.key === k)!.label;

    return (
        <Modal title="Recommend grading" onClose={onClose} className="modal--wide">
            <div className="grading">
                <p className="est-note grading__intro">
                    Your Near Mint cards and what each PSA grade sells for. Gains are per copy
                    against the raw price, net of the grading fee. Sort by the best grade or by any
                    single grade.
                </p>
                <div className="grading__controls">
                    <div className="range-tabs" role="group" aria-label="Rank by">
                        <button className={`btn btn--outline range-tab${metric === 'usd' ? ' btn--active' : ''}`}
                            aria-pressed={metric === 'usd'} onClick={() => setMetric('usd')}>$ gain</button>
                        <button className={`btn btn--outline range-tab${metric === 'pct' ? ' btn--active' : ''}`}
                            aria-pressed={metric === 'pct'} onClick={() => setMetric('pct')}>% gain</button>
                    </div>
                    <label className="grading__fee">
                        <span className="mono">Grading fee</span>
                        <input className="input" type="number" min={0} step={1} inputMode="decimal" value={fee}
                            onChange={e => setFee(e.target.value)} aria-label="Grading fee per card" />
                    </label>
                    <span className="mono grading__count">
                        {data ? `${rows.length} card${rows.length === 1 ? '' : 's'} with graded prices` : ''}
                    </span>
                </div>

                {isLoading ? (
                    <CardLoader />
                ) : rows.length === 0 ? (
                    <p className="est-note">
                        {data && data.nmCopies > 0
                            ? 'None of your Near Mint cards has graded sales on record yet.'
                            : <>No Near Mint cards in your collection yet. Add some from the catalog or the <Link to="/scan" onClick={onClose}>scanner</Link>.</>}
                    </p>
                ) : (
                    <div className="grading__wrap">
                        <table className="screener screener--positions grading__table">
                            <thead>
                                <tr>
                                    <th aria-label="Card image" />
                                    {th('Card', 'name', { defaultDir: 'asc', num: false })}
                                    {th('NM', 'nm', { title: 'Raw Near Mint price' })}
                                    {TIERS.map(t => th(t.label, t.key, { title: `Sort by the gain at ${t.label}` }))}
                                    {th('Best', 'best', { title: 'The grade that adds the most' })}
                                </tr>
                            </thead>
                            <tbody>
                                {sorted.map(r => (
                                    <tr key={`${r.game}:${r.productId}:${r.printing}`} className="screener__row">
                                        <td className="screener__thumbcell">
                                            <img className="screener__thumb" src={r.pictureUrl} alt="" loading="lazy"
                                                onError={e => fallbackToCardBack(e, r.game)} />
                                        </td>
                                        <td>
                                            <Link className="screener__name" to={`/catalog/${gameKey(r.game)}/${r.productId}`}
                                                onClick={onClose}>{r.name}</Link>
                                            <div className="mono">
                                                {[r.setName, r.cardNumber ? `#${r.cardNumber}` : null, r.printing || null,
                                                  r.copies > 1 ? `×${r.copies}` : null].filter(Boolean).join(' · ')}
                                            </div>
                                            {r.asOf && (
                                                <div className="mono grading__asof" title="Date of the newest graded sale on record">
                                                    graded as of {r.asOf.slice(0, 7)}
                                                </div>
                                            )}
                                        </td>
                                        <td className="screener__num grading__nm">{currencyFormat(r.nm)}</td>
                                        {TIERS.map(t => {
                                            const price = r.grades[t.key], g = r.gains[t.key];
                                            const isBest = r.best?.tier === t.key;
                                            return (
                                                <td key={t.key} className={`screener__num grading__tier${isBest ? ' grading__tier--best' : ''}`}>
                                                    {price == null ? <span className="mono grading__na">—</span> : (
                                                        <>
                                                            <span className="grading__price">{currencyFormat(price)}</span>
                                                            <span className={`mono grading__gain${g && g.usd < 0 ? ' grading__gain--down' : ''}`}>
                                                                {gainText(g)}
                                                            </span>
                                                        </>
                                                    )}
                                                </td>
                                            );
                                        })}
                                        <td className="screener__num grading__best">
                                            {r.best ? (
                                                <>
                                                    <span className="grading__price">{tierLabel(r.best.tier)}</span>
                                                    <span className={`mono grading__gain${r.best.usd < 0 ? ' grading__gain--down' : ''}`}>
                                                        {gainText(r.best)}
                                                    </span>
                                                </>
                                            ) : <span className="mono grading__na">—</span>}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
                <p className="est-note grading__note">
                    Graded prices are the latest recorded sales at each grade; a card only grades
                    as high as its condition allows, so treat the higher tiers as a ceiling.
                </p>
            </div>
        </Modal>
    );
}
