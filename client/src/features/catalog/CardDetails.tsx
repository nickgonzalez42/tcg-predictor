import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useFetchCardDetailsQuery, useFetchCardForecastQuery, useFetchCardReasoningQuery } from "./catalogApi";
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
import { PRICE_TIER_OPTIONS } from "../watchlist/grades";
import { confidence } from "./confidence";
import type { Forecast } from "../../app/models/card";
import { fallbackToCardBack } from "../../lib/cardImages";
import { sanitizeHtml } from "../../lib/sanitizeHtml";
import CardLoader from "../../app/shared/components/CardLoader";
import Modal from "../../app/shared/components/Modal";

const TARGETS = ['ungraded', 'grade7', 'grade8', 'grade9', 'grade95', 'psa10', 'bgs10', 'cgc10', 'sgc10'];
const HORIZONS = ['1w', '1m', '6m', '12m'];
const TARGET_LABEL: Record<string, string> = {
    ungraded: 'Ungraded', grade7: 'Grade 7', grade8: 'Grade 8', grade9: 'Grade 9',
    grade95: 'Grade 9.5', psa10: 'PSA 10', bgs10: 'BGS 10', cgc10: 'CGC 10', sgc10: 'SGC 10',
};
const HORIZON_LABEL: Record<string, string> = {
    '1w': '1 week', '1m': '1 month', '6m': '6 months', '12m': '1 year',
};
import { GAME_LABEL } from "../../lib/games";
import { usePageMeta } from "../../lib/usePageMeta";
import AdSlot from "../../app/shared/components/AdSlot";
import CommentSection from "../social/CommentSection";

// The stored reason leads with "Projects +X% over 12m. <Signals|Behind it|Key
// drivers|What moves it>: ..." — the projection is already shown per cell, so
// strip that lead-in and keep just the signals, capitalized.
function reasonBody(reason: string): string {
    const body = reason.replace(/^Projects\s+[+-]?\d+%\s+over\s+\w+\.\s*[^:]*:\s*/i, '');
    return body.charAt(0).toUpperCase() + body.slice(1);
}

// "Order ticket": condition + quantity + add-to-portfolio / wishlist, in a
// highlighted panel. Wraps the same watchlist mutations as TrackButton.
function OrderTicket({ game, productId }: { game: string; productId: number; psa10?: number }) {
    const { data: user } = useUserInfoQuery();
    const { data: watchlist } = useFetchWatchlistQuery(undefined, { skip: !user });
    const [add, { isLoading: adding }] = useAddToWatchlistMutation();
    const [remove] = useRemoveFromWatchlistMutation();
    const [setQty, { isLoading: settingQty }] = useSetOwnedQuantityMutation();
    const [grade, setGrade] = useState('');   // '' = Ungraded (raw copy)
    const [qty, setQty_] = useState('1');

    const parsed = Number(qty);
    const valid = qty.trim() !== '' && Number.isInteger(parsed) && parsed >= 1 && parsed <= 999;

    const wishlisted = !!watchlist?.some(
        w => w.game === game && w.productId === productId && w.kind === 'wishlist');
    const ownedAtGrade = watchlist?.filter(
        w => w.game === game && w.productId === productId && w.kind === 'owned' && (w.grade ?? '') === grade).length ?? 0;
    const ownedTotal = watchlist?.filter(
        w => w.game === game && w.productId === productId && w.kind === 'owned').length ?? 0;

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
                        ＋ Add to Portfolio{ownedTotal > 0 ? ` (${ownedTotal} owned)` : ''}
                    </button>
                    <button className={`btn btn--outline btn--block${wishlisted ? ' btn--active' : ''}`}
                        style={{ marginTop: 'var(--space-15)' }} disabled={adding}
                        onClick={() => wishlisted
                            ? remove({ game, productId, kind: 'wishlist' })
                            : add({ game, productId, kind: 'wishlist' })}>
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

function ForecastSection({ forecasts, game, id }: {
    forecasts: Forecast[]; game: string; id: number;
}) {
    const { data: reasoning, isFetching: reasoningLoading } = useFetchCardReasoningQuery({ game, id });
    // One "why" button per grade row opens a modal with every horizon's detail
    // (prices, change, range, reason) for that tier, each horizon its own block.
    const [why, setWhy] = useState<{ tier: string; forecasts: Forecast[] } | null>(null);
    if (forecasts.length === 0) return null;

    return (
        <section className="panel detail-panel">
            <h4 className="mono detail-panel__title">Price forecast · model</h4>
            {reasoning?.prose ? (
                <div className="model-take">
                    <span className="model-take__label">Model's take</span>
                    <p>{reasoning.prose}</p>
                </div>
            ) : reasoningLoading && (
                <div className="model-take model-take--loading">
                    <span className="model-take__label">Model's take</span>
                    <p>Summarizing the outlook…</p>
                </div>
            )}
            <div className="forecast-wrap">
            <table className="forecast-table">
                <thead>
                    <tr>
                        <th>Tier</th>
                        <th>Current</th>
                        {HORIZONS.map(h => <th key={h}>{HORIZON_LABEL[h]}</th>)}
                    </tr>
                </thead>
                <tbody>
                    {TARGETS.filter(t => forecasts.some(f => f.target === t)).map(t => {
                        const tierForecasts = forecasts.filter(f => f.target === t);
                        const lead = tierForecasts.find(f => f.horizon === '12m') ?? tierForecasts[0];
                        const conf = confidence(lead?.confidence, lead?.months);
                        return (
                            <tr key={t}>
                                <td>
                                    <div className="forecast-tier">
                                        <strong>{TARGET_LABEL[t] ?? t}</strong>
                                        {tierForecasts.some(f => f.reason) && (
                                            <button className="why-btn"
                                                aria-label={`Forecast reasoning for ${TARGET_LABEL[t] ?? t}`}
                                                title="Forecast reasoning"
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
                                <td>
                                    <strong>{currencyFormat(tierForecasts[0]?.basePrice)}</strong>
                                    {tierForecasts[0]?.asOf && (
                                        <div className="mono price-asof">{shortDate(tierForecasts[0].asOf)}</div>
                                    )}
                                </td>
                                {HORIZONS.map(h => {
                                    const f = tierForecasts.find(x => x.horizon === h);
                                    if (!f) return <td key={h}>—</td>;
                                    const chg = f.basePrice ? (f.forecastPrice / f.basePrice - 1) * 100 : 0;
                                    return (
                                        <td key={h}>
                                            <strong>{currencyFormat(f.forecastPrice)}</strong>
                                            <div className="forecast-chg">
                                                <ChangePill value={chg} digits={h === '1w' || h === '1m' ? 1 : 0} />
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
            {why && (
                <Modal title={`${TARGET_LABEL[why.tier] ?? why.tier} · forecast reasoning`}
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
    const gameId = game ?? 'onepiece';
    const cardId = id ? +id : 0;
    const { data: card, isLoading } = useFetchCardDetailsQuery({ game: gameId, id: cardId });
    const { data: forecastData } = useFetchCardForecastQuery({ game: gameId, id: cardId });

    usePageMeta(card ? `${card.name} — ${card.setName ?? card.game}` : undefined,
        card ? `Price, graded history, and AI forecast for ${card.name} (${[card.setName, card.rarity].filter(Boolean).join(", ")}).` : undefined);

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

    const subline = [card.setName, card.rarity, card.cardNumber ? `#${card.cardNumber}` : null]
        .filter(Boolean).join(' · ');

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
        { label: 'Ungraded', value: g.ungraded },
        { label: 'Grade 7', value: g.grade7 },
        { label: 'Grade 8', value: g.grade8 },
        { label: 'Grade 9', value: g.grade9 },
        { label: 'Grade 9.5', value: g.grade95 },
        { label: 'PSA 10', value: g.psa10 },
        { label: 'BGS 10', value: g.bgs10 },
        { label: 'CGC 10', value: g.cgc10 },
        { label: 'SGC 10', value: g.sgc10 },
    ].filter(r => r.value != null) : [];

    return (
        <>
            <nav className="breadcrumb mono full-span">
                <Link to="/catalog">Catalog</Link> / <Link to="/catalog">{GAME_LABEL[gameId] ?? card.game}</Link> / <span>{card.name}</span>
            </nav>

            {/* Left: card art + order ticket + prices + details */}
            <div className="detail-left">
                <img
                    className="detail-left__img"
                    src={card.pictureUrl}
                    alt={card.name}
                    onError={e => fallbackToCardBack(e, card.game, card.cardType)}
                />
                <OrderTicket game={gameId} productId={cardId} psa10={g?.psa10} />
                {gradeRows.length > 0 && (
                    <div className="panel detail-panel">
                        <h4 className="mono detail-panel__title">All prices</h4>
                        <div className="mono detail-panel__sub">
                            PriceCharting{g?.updatedAt ? ` · ${shortDate(g.updatedAt)}` : ''}
                        </div>
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
                {cardDetails.length > 0 && (
                    <div className="panel detail-panel">
                        <h4 className="mono detail-panel__title">Details</h4>
                        <table className="detail-table">
                            <tbody>
                                {cardDetails.map((detail, index) => (
                                    <tr key={index}>
                                        <td className="mono">{detail.label}</td>
                                        <td>
                                            {detail.label === 'Description' && typeof detail.value === 'string'
                                                ? <span className="detail-desc"
                                                    dangerouslySetInnerHTML={{ __html: sanitizeHtml(detail.value) }} />
                                                : detail.value}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            {/* Center: header + chart + forecast */}
            <div className="detail-center">
                <h1 className="detail-center__name">{card.name}</h1>
                {subline && <div className="mono detail-center__subline">{subline}</div>}
                {card.price != null && (
                    <div className="detail-center__pricerow">
                        <span className="detail-center__price">{currencyFormat(card.price)}</span>
                        {pct12 != null && <ChangePill value={pct12} title="1 year model forecast" />}
                        {pct12 != null && <span className="mono">1Y</span>}
                        <span className="price-caption">
                            latest PriceCharting ungraded{card.priceAsOf ? ` · as of ${shortDate(card.priceAsOf)}` : ''}
                        </span>
                    </div>
                )}
                <section className="panel detail-panel">
                    <h4 className="mono detail-panel__title">Price history + forecast</h4>
                    <PriceHistoryChart game={gameId} id={cardId} forecasts={forecasts} />
                </section>
                <ForecastSection forecasts={forecasts} game={gameId} id={cardId} />
                <CommentSection game={gameId} productId={cardId} />
                <AdSlot slot="" />
            </div>
        </>
    )
}
