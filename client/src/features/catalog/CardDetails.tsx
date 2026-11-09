import { Fragment, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useFetchCardDetailsQuery, useFetchCardForecastQuery, useFetchCardReasoningQuery, useFetchCardSalesQuery, useFetchFiltersQuery } from "./catalogApi";
import { WINDOW_LABEL, windowsFor, type Window } from "./windows";
import {
    useFetchWatchlistQuery,
    useAddToWatchlistMutation,
    useRemoveFromWatchlistMutation,
    useSetOwnedQuantityMutation,
} from "../watchlist/watchlistApi";
import { useUserInfoQuery } from "../account/accountApi";
import { currencyFormat, shortDate } from "../../lib/util";
import PriceHistoryChart from "./PriceHistoryChart";
import ChangePill from "../../app/shared/components/ChangePill";
import { PRICE_TIER_OPTIONS, GRADE_TIERS, GRADE_TIER_LABEL } from "../watchlist/grades";
import { confidence } from "./confidence";
import type { Forecast } from "../../app/models/card";
import { fallbackToCardBack } from "../../lib/cardImages";
import { sanitizeHtml } from "../../lib/sanitizeHtml";
import CardLoader from "../../app/shared/components/CardLoader";
import Modal from "../../app/shared/components/Modal";
import { isUpcoming, releaseLabel } from "../../app/shared/components/PredictedPrice";

const HORIZONS = ['1m', '6m', '12m'];
const HORIZON_LABEL: Record<string, string> = {
    '1m': '1 month', '6m': '6 months', '12m': '1 year',
};
import { GAME_LABEL } from "../../lib/games";
import { usePageMeta } from "../../lib/usePageMeta";
import { tcgBuyUrl } from "../../lib/affiliate";
import AdSlot from "../../app/shared/components/AdSlot";
import { useColumnParallax } from "../../lib/useColumnParallax";

// The stored reason leads with "Projects +X% over 12m. <Signals|Behind it|Key
// drivers|What moves it>: ..." — the projection is already shown per cell, so
// strip that lead-in and keep just the signals, capitalized.
function reasonBody(reason: string): string {
    const body = reason.replace(/^Projects\s+[+-]?\d+%\s+over\s+\w+\.\s*[^:]*:\s*/i, '');
    return body.charAt(0).toUpperCase() + body.slice(1);
}

// "Order ticket": condition + quantity + add-to-portfolio / wishlist, in a
// highlighted panel. Wraps the same watchlist mutations as TrackButton.
function OrderTicket({ game, productId, printing = '' }: { game: string; productId: number; printing?: string; psa10?: number }) {
    const { data: user } = useUserInfoQuery();
    const { data: watchlist } = useFetchWatchlistQuery(undefined, { skip: !user });
    const [add, { isLoading: adding }] = useAddToWatchlistMutation();
    const [remove, { isLoading: removing }] = useRemoveFromWatchlistMutation();
    const [setQty, { isLoading: settingQty }] = useSetOwnedQuantityMutation();
    const [grade, setGrade] = useState('');   // '' = Ungraded (raw copy)
    const [qty, setQty_] = useState('1');

    const parsed = Number(qty);
    const valid = qty.trim() !== '' && Number.isInteger(parsed) && parsed >= 1 && parsed <= 999;

    // Printing-aware: watching the 1st Edition is a separate watch.
    const wishlisted = !!watchlist?.some(
        w => w.game === game && w.productId === productId && w.kind === 'wishlist'
             && (w.printing ?? '') === printing);
    const ownedAtGrade = watchlist?.filter(
        w => w.game === game && w.productId === productId && w.kind === 'owned' && (w.grade ?? '') === grade).length ?? 0;
    const ownedTotal = watchlist?.filter(
        w => w.game === game && w.productId === productId && w.kind === 'owned').length ?? 0;

    // Adds are PACK PULLS by default (2026-10-02, user decision) — marking a
    // copy as paid happens in the portfolio's copy editor afterwards.
    const addToPortfolio = async () => {
        if (!valid || settingQty) return;
        try {
            await setQty({ game, productId, grade, quantity: Math.min(ownedAtGrade + parsed, 999) }).unwrap();
            setQty_('1');
        } catch { /* keep the form as-is so the user can retry */ }
    };

    return (
        <div className="ticket">
            <div className="ticket__title mono">Track this card</div>
            {user ? (
                <>
                    <label className="field-label" htmlFor="ticket-grade">Condition</label>
                    <select id="ticket-grade" className="input" value={grade}
                        onChange={e => setGrade(e.target.value)}>
                        {PRICE_TIER_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                    <label className="field-label" htmlFor="ticket-qty" style={{ marginTop: 'var(--space-15)' }}>Quantity</label>
                    <input id="ticket-qty" className="input" type="number" min="1" max="999" step="1"
                        inputMode="numeric" value={qty} onChange={e => setQty_(e.target.value)} />
                    <button className="btn btn--block" style={{ marginTop: 'var(--space-15)' }}
                        disabled={!valid || settingQty} onClick={addToPortfolio}>
                        {settingQty ? 'Adding…' : `＋ Add to Portfolio${ownedTotal > 0 ? ` (${ownedTotal} owned)` : ''}`}
                    </button>
                    <button className={`btn btn--outline btn--block${wishlisted ? ' btn--active' : ''}`}
                        style={{ marginTop: 'var(--space-15)' }} disabled={adding || removing}
                        onClick={() => wishlisted
                            ? remove({ game, productId, kind: 'wishlist' })
                            : add({ game, productId, kind: 'wishlist', printing })}>
                        {wishlisted ? '★ Watching' : '☆ Add to Watchlist'}
                    </button>
                </>
            ) : (
                <p className="est-note" style={{ margin: 0 }}>
                    <Link to="/login">Sign in</Link> to add this card to your portfolio or watchlist.
                </p>
            )}
        </div>
    );
}

// Weekly TCGplayer sales for the printing the page is on (2026-11-09, user:
// "add a volume table to the card detail page"): the last eight weeks of
// Near Mint copies and orders with the week's low, high and market price,
// headed by the total over the last full weeks. The newest week is marked
// as still running when the crawl fetched it mid-week. Nothing renders for a
// card the crawl has not reached yet.
const SALES_ROWS = 8;
function SalesSection({ game, id, printing }: { game: string; id: number; printing?: string }) {
    const { data } = useFetchCardSalesQuery({ game, id, printing });
    const weeks = data?.weeks ?? [];
    if (!data || weeks.length === 0) return null;
    const newest = weeks[weeks.length - 1];
    const running = !!data.asOf && Date.parse(data.asOf) < Date.parse(newest.weekStart) + 7 * 86400e3;
    const full = running ? weeks.slice(0, -1) : weeks;
    const recentFull = full.slice(-4);
    const soldRecent = recentFull.reduce((n, w) => n + w.sold, 0);
    const allRecent = recentFull.reduce((n, w) => n + w.soldAllConditions, 0);
    const rows = weeks.slice(-SALES_ROWS).reverse();
    const copies = (n: number) => `${n} ${n === 1 ? 'copy' : 'copies'}`;
    const weekLabel = (d: string) => new Date(d + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    return (
        <section className="panel detail-panel">
            <h4 className="mono detail-panel__title">Copies sold each week</h4>
            {recentFull.length > 0 && (
                <p className="price-caption">
                    {copies(soldRecent)} sold Near Mint on TCGplayer over the last {recentFull.length === 1 ? 'full week' : `${recentFull.length} full weeks`}
                    {allRecent > soldRecent ? `, ${allRecent} counting every condition` : ''}
                    {data.variant ? ` (${data.variant} printing)` : ''}.
                    {data.asOf ? ` Counted ${shortDate(data.asOf)}; we recount about every four weeks.` : ''}
                </p>
            )}
            <div className="forecast-wrap">
                <table className="sales-table">
                    <thead>
                        <tr>
                            <th>Week of</th>
                            <th>Copies</th>
                            <th>Orders</th>
                            <th className="sales-table__wide">Low</th>
                            <th className="sales-table__wide">High</th>
                            <th>Market</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map(w => (
                            <tr key={w.weekStart}>
                                <td>
                                    {weekLabel(w.weekStart)}
                                    {w === newest && running && <span className="sales-table__sofar"> so far</span>}
                                </td>
                                <td><strong>{w.sold}</strong></td>
                                <td>{w.orders}</td>
                                <td className="sales-table__wide">{w.low != null ? currencyFormat(w.low) : '–'}</td>
                                <td className="sales-table__wide">{w.high != null ? currencyFormat(w.high) : '–'}</td>
                                <td>{w.market != null ? currencyFormat(w.market) : '–'}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            <p className="est-note">
                Near Mint sales only. Low and high are the cheapest and dearest copies that sold that week; market is TCGplayer's market price at the time.
            </p>
        </section>
    );
}

function ForecastSection({ forecasts, game, id }: {
    forecasts: Forecast[]; game: string; id: number;
}) {
    const { data: reasoning, isFetching: reasoningLoading } = useFetchCardReasoningQuery({ game, id });
    // One "why" button per grade row opens a modal with every horizon's detail
    // (prices, change, range, reason) for that tier, each horizon its own block.
    const [why, setWhy] = useState<{ tier: string; forecasts: Forecast[] } | null>(null);
    // Which windows the game serves at all (2026-11-05): an empty slot reads
    // "Not yet" when the whole game lacks that horizon, "n/a" when only this
    // card or tier does.
    const { data: facets } = useFetchFiltersQuery({ game });
    const served = windowsFor(facets);
    const WINDOW: Record<string, Window> = { '1m': '1m', '6m': '6m', '12m': '1y' };
    // A call the model made at a noticeably different price (the 6-month and
    // 1-year rows re-anchor monthly, the price moves daily): the figures apply
    // the call to today's price, and the cell says when and from what it was
    // made (2026-11-06, after a week in which crashed cards read as rebounds).
    const madeElsewhere = (f: Forecast) =>
        f.anchorPrice != null && f.basePrice > 0 && Math.abs(f.anchorPrice / f.basePrice - 1) > 0.01;
    const madeNote = (f: Forecast) =>
        `call made ${f.asOf ? shortDate(f.asOf) + ' ' : ''}at ${currencyFormat(f.anchorPrice)}`;
    if (forecasts.length === 0) return null;

    // Those notes used to sit inside the cells, where one of them widened
    // its column and pushed the next window across the panel (2026-11-08).
    // They live under the table instead, one line per call date and price,
    // naming the windows (and the condition, when the table has several).
    const NOTE_WINDOW: Record<string, string> = { '1m': '1-month', '6m': '6-month', '12m': '1-year' };
    const tiers = GRADE_TIERS.filter(t => forecasts.some(f => f.target === t));
    const notes: string[] = [];
    for (const t of tiers) {
        const groups = new Map<string, Forecast[]>();
        for (const f of forecasts.filter(x => x.target === t && HORIZONS.includes(x.horizon) && madeElsewhere(x)))
            groups.set(`${f.asOf ?? ''}|${f.anchorPrice}`, [...(groups.get(`${f.asOf ?? ''}|${f.anchorPrice}`) ?? []), f]);
        for (const fs of groups.values()) {
            fs.sort((a, b) => HORIZONS.indexOf(a.horizon) - HORIZONS.indexOf(b.horizon));
            const windows = fs.map(f => NOTE_WINDOW[f.horizon] ?? f.horizon);
            const list = windows.length > 1
                ? `${windows.slice(0, -1).join(', ')} and ${windows[windows.length - 1]}`
                : windows[0];
            const f0 = fs[0];
            notes.push(`${tiers.length > 1 ? `${GRADE_TIER_LABEL[t] ?? t}: ` : ''}`
                + `${list} ${fs.length > 1 ? 'calls' : 'call'} made ${f0.asOf ? shortDate(f0.asOf) + ' ' : ''}`
                + `at ${currencyFormat(f0.anchorPrice)}; the figures apply ${fs.length > 1 ? 'them' : 'it'} to today's price.`);
        }
    }

    return (
        <section className="panel detail-panel">
            <h4 className="mono detail-panel__title">Our forecast</h4>
            {reasoning?.prose ? (
                <div className="model-take">
                    <span className="model-take__label">Model's take</span>
                    <p>{reasoning.prose}</p>
                </div>
            ) : reasoningLoading && (
                <div className="model-take model-take--loading">
                    <span className="model-take__label">Model's take</span>
                    <p>Putting the outlook into words…</p>
                </div>
            )}
            <div className="forecast-wrap">
            <table className="forecast-table">
                <thead>
                    <tr>
                        <th>Condition</th>
                        <th>Now</th>
                        {HORIZONS.map(h => <th key={h}>{HORIZON_LABEL[h]}</th>)}
                    </tr>
                </thead>
                <tbody>
                    {tiers.map(t => {
                        const tierForecasts = forecasts.filter(f => f.target === t);
                        const lead = tierForecasts.find(f => f.horizon === '12m') ?? tierForecasts[0];
                        const conf = confidence(lead?.confidence, lead?.months);
                        return (
                            <tr key={t}>
                                <td>
                                    <div className="forecast-tier">
                                        <strong>{GRADE_TIER_LABEL[t] ?? t}</strong>
                                        {tierForecasts.some(f => f.reason) && (
                                            <button className="why-btn"
                                                aria-label={`Why we think so, ${GRADE_TIER_LABEL[t] ?? t}`}
                                                title="Why we think so"
                                                onClick={() => setWhy({ tier: t, forecasts: tierForecasts })}>
                                                ⓘ
                                            </button>
                                        )}
                                    </div>
                                    <div>
                                        <span className={`conf ${conf.cls}`} title={conf.reason}>
                                            {conf.short}
                                        </span>
                                    </div>
                                </td>
                                <td data-label="Now">
                                    <strong>{currencyFormat(tierForecasts[0]?.basePrice)}</strong>
                                </td>
                                {HORIZONS.map(h => {
                                    // Launch-tier fallback (2026-10-09): a card too
                                    // new for the standard model may carry a
                                    // launch1m forecast — show it in the 1-month
                                    // slot, clearly badged as an early estimate.
                                    const f = tierForecasts.find(x => x.horizon === h)
                                        ?? (h === '1m'
                                            ? tierForecasts.find(x => x.horizon === 'launch1m')
                                            : undefined);
                                    if (!f) {
                                        return served[WINDOW[h]]
                                            ? <td key={h} data-label={HORIZON_LABEL[h]}>n/a</td>
                                            : <td key={h} data-label={HORIZON_LABEL[h]}
                                                title={`No ${WINDOW_LABEL[WINDOW[h]]} calls for this game yet`}>Not yet</td>;
                                    }
                                    const early = f.horizon === 'launch1m';
                                    const chg = f.basePrice ? (f.forecastPrice / f.basePrice - 1) * 100 : 0;
                                    return (
                                        <td key={h} data-label={HORIZON_LABEL[h]}
                                            title={madeElsewhere(f)
                                                ? `The price has moved since the model made this call (${madeNote(f)}). The figures apply its call to today's price.`
                                                : undefined}>
                                            <strong>{currencyFormat(f.forecastPrice)}</strong>
                                            {early && (
                                                <span className="early-badge"
                                                    title="Early estimate: this card is too new for our usual forecast, so this is a first guess from its set, rarity, art and the recent drift of cards like it. Expect it to be rough; it is best for comparing new cards with each other.">
                                                    EARLY
                                                </span>
                                            )}
                                            <div className="forecast-chg">
                                                <ChangePill value={chg} digits={h === '1m' ? 1 : 0} />
                                            </div>
                                        </td>
                                    );
                                })}
                            </tr>
                        );
                    })}
                </tbody>
            </table>
            </div>
            {notes.length > 0 && (
                <ul className="forecast-notes">
                    {notes.map(n => <li key={n}>{n}</li>)}
                </ul>
            )}
            {why && (
                <Modal title={`${GRADE_TIER_LABEL[why.tier] ?? why.tier} · why we think so`}
                    onClose={() => setWhy(null)}>
                    {HORIZONS.filter(h => why.forecasts.some(f => f.horizon === h)).map(h => {
                        const f = why.forecasts.find(x => x.horizon === h)!;
                        const pct = f.basePrice ? (f.forecastPrice / f.basePrice - 1) * 100 : undefined;
                        return (
                            <div key={h} className="why-section">
                                <h5 className="mono why-section__title">{HORIZON_LABEL[h]}</h5>
                                <div className="why-stats">
                                    <div className="why-stat">
                                        <span className="mono why-stat__label">Current</span>
                                        <strong>{currencyFormat(f.basePrice)}</strong>
                                    </div>
                                    <div className="why-stat">
                                        <span className="mono why-stat__label">Forecast</span>
                                        <strong className={f.forecastPrice >= f.basePrice
                                            ? 'why-stat__value--up' : 'why-stat__value--down'}>
                                            {currencyFormat(f.forecastPrice)}
                                        </strong>
                                    </div>
                                    <div className="why-stat">
                                        <span className="mono why-stat__label">Change</span>
                                        <span>
                                            <ChangePill value={f.forecastPrice - f.basePrice} unit="usd" />{' '}
                                            <ChangePill value={pct} />
                                        </span>
                                    </div>
                                    <div className="why-stat">
                                        <span className="mono why-stat__label">Range</span>
                                        <span className="est-note">
                                            {currencyFormat(f.low)}–{currencyFormat(f.high)}
                                        </span>
                                    </div>
                                </div>
                                {madeElsewhere(f) && (
                                    <p className="est-note">
                                        The model made this call {f.asOf ? `on ${shortDate(f.asOf)} ` : ''}when the card
                                        was {currencyFormat(f.anchorPrice)}. The figures above apply that call to today's price.
                                    </p>
                                )}
                                {f.reason && <p>{reasonBody(f.reason)}</p>}
                            </div>
                        );
                    })}
                    <p className="est-note why-note">
                        Each horizon is a separate model estimate, so figures can differ
                        between them. The range is the model's own 10th–90th percentile
                        scenario band. Not investment advice.
                    </p>
                </Modal>
            )}
        </section>
    );
}

export default function CardDetails() {
    const { game, id } = useParams();
    // Selected printing rides the URL (?printing=) so a specific printing is
    // shareable; empty = the card's base printing.
    const [searchParams, setSearchParams] = useSearchParams();
    const printing = searchParams.get('printing') ?? '';
    const gameId = game ?? 'onepiece';
    const cardId = id ? +id : 0;
    const { data: card, isLoading } = useFetchCardDetailsQuery(
        { game: gameId, id: cardId, printing: printing || undefined });
    // The art column and the chart column finish the page together (see the hook).
    const leftRef = useRef<HTMLDivElement>(null);
    const centerRef = useRef<HTMLDivElement>(null);
    useColumnParallax(leftRef, centerRef, [card?.id, isLoading]);

    const { data: forecastData } = useFetchCardForecastQuery(
        { game: gameId, id: cardId, printing: printing || undefined });

    const prerelease = !!card && card.price == null && !!card.isPrerelease && card.predictedPrice != null;
    usePageMeta(card ? `${card.name} ${prerelease ? 'Predicted Launch Price' : 'Price Prediction'} · ${card.setName ?? card.game}` : undefined,
        card ? (prerelease
            ? `Pre-release: a predicted launch price for ${card.name} (${[card.setName, card.rarity].filter(Boolean).join(", ")}) before it has a market price.`
            : `AI price prediction, market price, and graded history for ${card.name} (${[card.setName, card.rarity].filter(Boolean).join(", ")}).`) : undefined);

    // Stable identity: the chart effect keys off this array, so a fresh copy per
    // render would tear the chart down on unrelated re-renders. (Hooks must run
    // on every render, so this stays above the loading early-return.)
    const forecasts = useMemo(() =>
        [...(forecastData?.forecasts ?? [])].sort((a, b) =>
            a.target.localeCompare(b.target) || a.horizon.localeCompare(b.horizon)),
        [forecastData]);

    if (isLoading || !card) return <CardLoader game={gameId} />
    const fc12 = forecasts.find(f => f.target === 'ungraded' && f.horizon === '12m');
    const pct12 = fc12 && fc12.basePrice ? (fc12.forecastPrice / fc12.basePrice - 1) * 100 : undefined;
    const historyMonths = forecasts.find(f => f.target === 'ungraded')?.months;

    // Set and rarity jump to the catalog pre-filtered; the number stays text
    // (search matches names only, so a number link would land on no results).
    const catalogFilter = (key: 'sets' | 'rarities', value: string) =>
        `/catalog?game=${gameId}&${key}=${encodeURIComponent(value)}`;
    const sublineParts = [
        card.setName && <Link to={catalogFilter('sets', card.setName)}>{card.setName}</Link>,
        card.rarity && <Link to={catalogFilter('rarities', card.rarity)}>{card.rarity}</Link>,
        card.cardNumber && `#${card.cardNumber}`,
    ].filter(Boolean);

    const cardDetails = [
        { label: 'Game', value: GAME_LABEL[gameId] ?? card.game },
        { label: 'Set', value: card.setName },
        { label: 'Rarity', value: card.rarity },
        { label: 'Type', value: card.cardType },
        ...Object.entries(card.attributes).map(([label, value]) => ({ label, value })),
        { label: 'History', value: historyMonths ? `${historyMonths} months` : undefined },
        { label: 'Description', value: card.description },
    ].filter(detail => detail.value);

    const g = card.gradedPrices;
    const gradeRows = g ? [
        // Ungraded = the live Near Mint market (same number as the headline);
        // the graded snapshot's own ungraded column is the retired source and
        // only backstops cards without an NM price. BGS/CGC/SGC 10 were dropped
        // with that source (2026-08-01) — the API now always sends them null.
        { label: 'Ungraded', value: card.price ?? g.ungraded },
        { label: 'Grade 7', value: g.grade7 },
        { label: 'Grade 8', value: g.grade8 },
        { label: 'Grade 9', value: g.grade9 },
        { label: 'Grade 9.5', value: g.grade95 },
        { label: 'PSA 10', value: g.psa10 },
    ].filter(r => r.value != null) : [];

    // No sales data at all (unpriced + no graded snapshot). Such cards
    // are filtered out of the catalog but reachable by direct link, image
    // search, or a watchlist — so the detail page says so plainly instead of
    // rendering a blank price area over an empty chart.
    const noSales = card.price == null && gradeRows.length === 0 && !prerelease;

    return (
        <>
            <nav className="breadcrumb mono full-span">
                {/* Bare /catalog re-picks the user's default game; ?game= pins it. */}
                <Link to="/catalog">Catalog</Link> / <Link to={`/catalog?game=${gameId}`}>{GAME_LABEL[gameId] ?? card.game}</Link> / <span>{card.name}</span>
            </nav>

            {/* Left: card art + order ticket + prices */}
            <div className="detail-left" ref={leftRef}>
                <img
                    className="detail-left__img"
                    src={card.pictureUrl}
                    alt={card.name}
                    onError={e => fallbackToCardBack(e, card.game, card.cardType)}
                />
                {tcgBuyUrl(cardId) && (
                    <div className="buy-tcg">
                        {/* rel=sponsored: affiliate links must be marked for
                            crawlers; the visible disclosure covers the FTC. */}
                        <a className="btn btn--block" href={tcgBuyUrl(cardId)!}
                            target="_blank" rel="sponsored noopener">
                            Buy on TCGplayer ↗
                        </a>
                        <div className="buy-tcg__disclosure mono">
                            Affiliate link; CardStock may earn a commission.
                        </div>
                    </div>
                )}
                <OrderTicket game={gameId} productId={cardId} printing={printing} psa10={g?.psa10} />
                {gradeRows.length > 0 && (
                    <div className="panel detail-panel">
                        <h4 className="mono detail-panel__title">All prices</h4>
                        {g?.updatedAt && (
                            <div className="mono detail-panel__sub">
                                as of {shortDate(g.updatedAt)}
                            </div>
                        )}
                        <table className="detail-table">
                            <tbody>
                                {gradeRows.map((r, i) => (
                                    <tr key={i}>
                                        <td>{r.label}</td>
                                        <td className="detail-table__price">{currencyFormat(r.value)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            {/* Right: header + chart + forecast + details */}
            <div className="detail-center" ref={centerRef}>
                <h1 className="detail-center__name">{card.name}</h1>
                {sublineParts.length > 0 && (
                    <div className="mono detail-center__subline">
                        {sublineParts.map((part, i) => (
                            <Fragment key={i}>{i > 0 && ' · '}{part}</Fragment>
                        ))}
                    </div>
                )}
                {prerelease ? (
                    /* Pre-release (2026-10-10): the estimate stands where the price
                       row, chart and forecast table would be — framed in violet and
                       tagged, so it never reads as market data. */
                    <section className="panel detail-panel detail-prerelease">
                        <div className="detail-prerelease__head">
                            <span className="mono predicted__tag">Predicted</span>
                            <h4 className="mono detail-panel__title">
                                {isUpcoming(card.releaseDate)
                                    ? 'Pre-release · no market price yet'
                                    : 'Released · awaiting first sales'}
                            </h4>
                        </div>
                        <div className="detail-prerelease__pricerow">
                            <span className="detail-prerelease__price">{currencyFormat(card.predictedPrice)}</span>
                            {card.predictedLow != null && card.predictedHigh != null && (
                                <span className="mono detail-prerelease__range"
                                    title="Likely range: 8 in 10 of this game's past launches landed inside a range like this">
                                    likely {currencyFormat(card.predictedLow)}–{currencyFormat(card.predictedHigh)}
                                </span>
                            )}
                        </div>
                        <div className="mono detail-prerelease__release">
                            {releaseLabel(card.releaseDate) || 'Not trading yet'}
                            {card.predictedMissPct != null && ` · typical miss on past launches ~${Math.round(card.predictedMissPct)}%`}
                        </div>
                        {card.predictedReason && <p className="detail-prerelease__why">{card.predictedReason}</p>}
                        <p className="est-note">
                            This is a model estimate of where the card will trade over its first
                            couple of months, built only from what the card is: its rarity, set,
                            printed stats, the history of earlier cards with the same character, and
                            its artwork. There are no sales behind it yet. Once the card starts
                            trading, its real market price and the regular forecast take over here,
                            and this estimate is graded on the public track record. Not financial advice.
                        </p>
                    </section>
                ) : noSales ? (
                    <section className="panel detail-panel detail-nosales">
                        <h4 className="mono detail-panel__title">No sales info</h4>
                        <p className="est-note detail-nosales__body">
                            There are no recorded sales for this card yet, so there's no
                            market price, price history, or forecast to show. This is common for
                            brand-new promos and event cards. Check back once it starts trading.
                        </p>
                    </section>
                ) : (
                    <>
                        {card.printings && card.printings.length > 1 && (
                            <div className="printing-pills" role="group" aria-label="Printing">
                                {card.printings.map(p => (
                                    <button key={p} type="button"
                                        className={'printing-pill' + ((printing || card.basePrinting) === p ? ' printing-pill--active' : '')}
                                        onClick={() => {
                                            const sp = new URLSearchParams(searchParams);
                                            if (p === card.basePrinting) sp.delete('printing');
                                            else sp.set('printing', p);
                                            setSearchParams(sp, { replace: true });
                                        }}>
                                        {p}
                                    </button>
                                ))}
                            </div>
                        )}
                        {card.price != null && (
                            <div className="detail-center__pricerow">
                                <span className="detail-center__price">{currencyFormat(card.price)}</span>
                                {pct12 != null && <ChangePill value={pct12} title="1 year model forecast" />}
                                {pct12 != null && <span className="mono">1Y</span>}
                                <span className="price-caption">
                                    {printing ? `${printing} · market price` : 'Near Mint market price'}{card.priceAsOf ? ` · as of ${shortDate(card.priceAsOf)}` : ''}
                                </span>
                            </div>
                        )}
                        {/* Liquidity honesty (2026-09-13): a long-frozen price is
                            usually a listing, not sales — say so up front. */}
                        {!printing && card.priceMovedAt && (() => {
                            const days = Math.floor((Date.now() - Date.parse(card.priceMovedAt!)) / 86400e3);
                            if (days <= 13) return null;   // recently moved: sales-backed, no caveat needed
                            return (
                                <p className="est-note price-liquidity">
                                    {days > 45
                                        ? `This price hasn't moved in ${days} days; it may reflect asking prices rather than recent sales.`
                                        : `Price last moved ${days} days ago.`}
                                </p>
                            );
                        })()}
                        <section className="panel detail-panel">
                            <h4 className="mono detail-panel__title">Price history and forecast</h4>
                            <PriceHistoryChart game={gameId} id={cardId} printing={printing || undefined} forecasts={forecasts} />
                        </section>
                        {printing && forecasts.length === 0 ? (
                            <section className="panel detail-panel">
                                <h4 className="mono detail-panel__title">Forecast</h4>
                                <p className="price-caption">
                                    No forecast for the {printing} printing yet; forecasts
                                    currently cover the {card.basePrinting ?? 'base'} printing.
                                </p>
                            </section>
                        ) : (
                            <ForecastSection forecasts={forecasts} game={gameId} id={cardId} />
                        )}
                        <SalesSection game={gameId} id={cardId} printing={printing || undefined} />
                    </>
                )}
                {cardDetails.length > 0 && (
                    <div className="panel detail-panel">
                        <h4 className="mono detail-panel__title">Details</h4>
                        <table className="detail-table">
                            <tbody>
                                {cardDetails.map((detail, index) => (
                                    // Long text (the card's rules text) reads as a
                                    // block under its label, left-aligned and full
                                    // width, instead of ragged-right beside it.
                                    detail.label === 'Description' && typeof detail.value === 'string'
                                        ? (
                                            <tr key={index} className="detail-row--block">
                                                <td colSpan={2}>
                                                    <span className="mono detail-row__label">{detail.label}</span>
                                                    <div className="detail-desc"
                                                        dangerouslySetInnerHTML={{ __html: sanitizeHtml(detail.value) }} />
                                                </td>
                                            </tr>
                                        ) : (
                                            <tr key={index}>
                                                <td className="mono">{detail.label}</td>
                                                <td>{detail.value}</td>
                                            </tr>
                                        )
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
                <AdSlot slot="" />
            </div>
        </>
    )
}
