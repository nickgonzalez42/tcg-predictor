import { Link } from "react-router-dom";
import Modal from "../../app/shared/components/Modal";
import CardLoader from "../../app/shared/components/CardLoader";
import ChangePill from "../../app/shared/components/ChangePill";
import AllocationDonut from "./AllocationDonut";
import { useFetchCollectionAnalysisQuery, type CollectionAnalysis, type Slice } from "./watchlistApi";
import { useCountUp } from "../home/Glance";
import { currencyFormat, gameKey } from "../../lib/util";
import { fallbackToCardBack } from "../../lib/cardImages";

// Tools > Collection analysis (2026-10-25): infographics over everything the
// user holds — where the value sits (game, condition, set, rarity, price
// band, concentration), how it got there (acquisitions by month, holding
// time), what it did in the last 30 days, what the model expects at 6 and
// 12 months, and the cards that matter most. All figures come from
// portfolio/analysis in one call.
export default function CollectionAnalysisModal({ onClose }: { onClose: () => void }) {
    const { data, isLoading } = useFetchCollectionAnalysisQuery();
    return (
        <Modal title="Collection analysis" onClose={onClose} className="modal--wide">
            {isLoading || !data ? <CardLoader /> : data.copies === 0 ? (
                <p className="est-note">
                    Nothing to analyse yet. Add cards from the catalog or the <Link to="/scan" onClick={onClose}>scanner</Link>.
                </p>
            ) : <Analysis a={data} onClose={onClose} />}
        </Modal>
    );
}

const fmtDays = (d: number) => d >= 365 ? `${(d / 365).toFixed(1)} yr` : d >= 60 ? `${Math.round(d / 30)} mo` : `${Math.round(d)} d`;
const monthLabel = (m: string) => new Date(`${m}-15T00:00:00Z`).toLocaleString('en-US', { month: 'short', year: '2-digit', timeZone: 'UTC' });

function Analysis({ a, onClose }: { a: CollectionAnalysis; onClose: () => void }) {
    const total = useCountUp(a.totalValue, true);
    const h6 = a.outlook['6m'], h12 = a.outlook['12m'];
    return (
        <div className="an">
            {/* ----- headline ----- */}
            <div className="an-tiles">
                <div className="an-tile an-tile--hero">
                    <span className="an-tile__label">Collection value</span>
                    <span className="an-tile__value display">{currencyFormat(total)}</span>
                    <span className="an-tile__sub">
                        {a.cards.toLocaleString('en-US')} card{a.cards === 1 ? '' : 's'} · {a.copies.toLocaleString('en-US')} cop{a.copies === 1 ? 'y' : 'ies'} · {a.games} game{a.games === 1 ? '' : 's'}
                    </span>
                </div>
                <div className="an-tile">
                    <span className="an-tile__label">Invested</span>
                    <span className="an-tile__value">{currencyFormat(a.invested)}</span>
                    <span className="an-tile__sub">
                        {a.paidCopies} bought · {a.packCopies} pack pull{a.packCopies === 1 ? '' : 's'}
                    </span>
                </div>
                <div className="an-tile">
                    <span className="an-tile__label">Unrealized P/L</span>
                    <span className="an-tile__value">
                        {a.invested > 0 ? <ChangePill value={a.unrealizedUsd} unit="usd" title="Value of bought copies minus what they cost" /> : <span className="mono">n/a</span>}
                    </span>
                    <span className="an-tile__sub">
                        {a.unrealizedPct != null ? <ChangePill value={a.unrealizedPct} suffix="on cost" /> : 'no purchase prices yet'}
                    </span>
                </div>
                <div className="an-tile">
                    <span className="an-tile__label">Realized P/L</span>
                    <span className="an-tile__value">
                        {a.realizedUsd != null ? <ChangePill value={a.realizedUsd} unit="usd" title="Proceeds minus cost across every sale" /> : <span className="mono">n/a</span>}
                    </span>
                    <span className="an-tile__sub">{a.soldCopies ? `${a.soldCopies} sale${a.soldCopies === 1 ? '' : 's'}` : 'nothing sold yet'}</span>
                </div>
                <div className="an-tile">
                    <span className="an-tile__label">Typical card</span>
                    <span className="an-tile__value">{currencyFormat(a.medianValue)}</span>
                    <span className="an-tile__sub">median · average {currencyFormat(a.avgValue)}</span>
                </div>
            </div>

            {/* ----- concentration ----- */}
            <section className="an-section">
                <h4 className="an-h">Where the value sits</h4>
                <div className="an-split">
                    <Curve c={a.concentration} />
                    <div className="an-mini-tiles">
                        {([['Top card', a.concentration.top1], ['Top 5', a.concentration.top5],
                           ['Top 10', a.concentration.top10], ['Top 25', a.concentration.top25]] as const).map(([l, v]) => (
                            <div className="an-mini" key={l}>
                                <span className="an-mini__value">{v.toFixed(0)}%</span>
                                <span className="an-mini__label">{l}</span>
                            </div>
                        ))}
                        <p className="est-note an-mini__note">
                            Share of the collection's value held by the most valuable cards (copies combined).
                        </p>
                    </div>
                </div>
            </section>

            {/* ----- game + condition ----- */}
            <section className="an-section an-donuts">
                <AllocationDonut title="Value by game" slices={a.byGame} />
                <AllocationDonut title="Value by condition" slices={a.byTier} />
            </section>

            {/* ----- sets + rarity ----- */}
            <section className="an-section an-split">
                <Bars title="Top sets" slices={a.bySet} />
                <Bars title="By rarity" slices={a.byRarity} />
            </section>

            {/* ----- price bands + acquisitions ----- */}
            <section className="an-section an-split">
                <div>
                    <h4 className="an-h">Price bands</h4>
                    <div className="an-bars">
                        {(() => {
                            const max = Math.max(1, ...a.priceBands.map(b => b.copies));
                            return a.priceBands.map(b => (
                                <div className="an-bar" key={b.label}>
                                    <span className="an-bar__label">{b.label}</span>
                                    <span className="an-bar__track"><span className="an-bar__fill" style={{ width: `${(b.copies / max) * 100}%` }} /></span>
                                    <span className="an-bar__value mono">{b.copies} · {currencyFormat(b.value)}</span>
                                </div>
                            ));
                        })()}
                    </div>
                    <p className="est-note an-note">Copies in each price range, with their combined value.</p>
                </div>
                <Acquisitions rows={a.acquisitions} />
            </section>

            {/* ----- last 30 days + outlook ----- */}
            <section className="an-section an-split">
                <div>
                    <h4 className="an-h">Last 30 days</h4>
                    {a.momentum.copies === 0 ? <p className="est-note">No price history a month back yet.</p> : (
                        <>
                            <div className="an-mom" role="img"
                                aria-label={`${a.momentum.up} up, ${a.momentum.flat} flat, ${a.momentum.down} down`}>
                                <span className="an-mom__seg an-mom__seg--up" style={{ flex: a.momentum.up }} />
                                <span className="an-mom__seg an-mom__seg--flat" style={{ flex: a.momentum.flat }} />
                                <span className="an-mom__seg an-mom__seg--down" style={{ flex: a.momentum.down }} />
                            </div>
                            <div className="an-legend mono">
                                <span><i className="an-dot an-dot--up" />{a.momentum.up} up</span>
                                <span><i className="an-dot an-dot--flat" />{a.momentum.flat} flat</span>
                                <span><i className="an-dot an-dot--down" />{a.momentum.down} down</span>
                            </div>
                            <div className="an-mom__net">
                                <ChangePill value={a.momentum.netUsd} unit="usd" title="Change in value of the copies that were priced a month ago" />
                                <ChangePill value={a.momentum.netPct} />
                            </div>
                        </>
                    )}
                </div>
                <div>
                    <h4 className="an-h">Model outlook</h4>
                    <div className="an-outlook">
                        {([['6 months', h6], ['12 months', h12]] as const).map(([l, o]) => (
                            <div className="an-tile" key={l}>
                                <span className="an-tile__label">{l}</span>
                                {o && o.copies > 0 ? (
                                    <>
                                        <span className="an-tile__value">{currencyFormat(o.forecast)}</span>
                                        <span className="an-tile__sub an-tile__pills">
                                            <ChangePill value={o.changeUsd} unit="usd" />
                                            <ChangePill value={o.changePct} />
                                        </span>
                                        <span className="an-tile__sub">{o.up} up · {o.down} down · {o.copies} copies forecast</span>
                                    </>
                                ) : <span className="an-tile__sub">no forecasts for these cards yet</span>}
                            </div>
                        ))}
                    </div>
                    <p className="est-note an-note">
                        Today's prices moved by each card's model forecast; a direction signal, not a promise.
                    </p>
                </div>
            </section>

            {/* ----- holding + top cards ----- */}
            <section className="an-section an-split">
                <div>
                    <h4 className="an-h">Holding time</h4>
                    <div className="an-mini-tiles an-mini-tiles--row">
                        <div className="an-mini"><span className="an-mini__value">{fmtDays(a.holding.medianDays)}</span><span className="an-mini__label">median</span></div>
                        <div className="an-mini"><span className="an-mini__value">{fmtDays(a.holding.avgDays)}</span><span className="an-mini__label">average</span></div>
                        <div className="an-mini"><span className="an-mini__value">{fmtDays(a.holding.oldest.days)}</span><span className="an-mini__label">longest</span></div>
                    </div>
                    <p className="est-note an-note">
                        Longest held: <Link to={`/catalog/${gameKey(a.holding.oldest.game)}/${a.holding.oldest.id}`} onClick={onClose}>{a.holding.oldest.name ?? 'a card'}</Link> since {a.holding.oldest.since}.
                        Collecting since {a.holding.firstDate}.
                    </p>
                </div>
                <div>
                    <h4 className="an-h">Cards that matter most</h4>
                    <ul className="an-top">
                        {a.topCards.map(c => (
                            <li key={`${c.game}:${c.id}`} className="an-top__row">
                                <img src={c.pictureUrl} alt="" loading="lazy"
                                    onError={e => fallbackToCardBack(e, c.game)} />
                                <span className="an-top__text">
                                    <Link className="an-top__name" to={`/catalog/${gameKey(c.game)}/${c.id}`} onClick={onClose}>{c.name}</Link>
                                    <span className="mono an-top__sub">{[c.setName, c.copies > 1 ? `×${c.copies}` : null].filter(Boolean).join(' · ')}</span>
                                </span>
                                <span className="an-top__side mono">
                                    <span>{currencyFormat(c.value)}</span>
                                    <span className="an-top__pct">{c.pct.toFixed(1)}%</span>
                                </span>
                            </li>
                        ))}
                    </ul>
                </div>
            </section>
        </div>
    );
}

function Bars({ title, slices }: { title: string; slices: Slice[] }) {
    const max = Math.max(1, ...slices.map(s => s.value));
    return (
        <div>
            <h4 className="an-h">{title}</h4>
            <div className="an-bars">
                {slices.map(s => (
                    <div className="an-bar" key={s.label}>
                        <span className="an-bar__label" title={s.label}>{s.label}</span>
                        <span className="an-bar__track"><span className="an-bar__fill" style={{ width: `${(s.value / max) * 100}%` }} /></span>
                        <span className="an-bar__value mono">{currencyFormat(s.value)} · {s.pct.toFixed(0)}%</span>
                    </div>
                ))}
            </div>
        </div>
    );
}

// Share of value (y) against share of cards (x): the steeper the start,
// the more the collection rides a few cards. The diagonal is "every card
// worth the same".
function Curve({ c }: { c: CollectionAnalysis['concentration'] }) {
    const W = 320, H = 180, PL = 40, PR = 10, PT = 10, PB = 24;
    const x = (v: number) => PL + (v / 100) * (W - PL - PR);
    const y = (v: number) => H - PB - (v / 100) * (H - PT - PB);
    const pts = [{ cards: 0, value: 0 }, ...c.curve];
    const d = pts.map(p => `${x(p.cards).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ');
    return (
        <svg className="an-curve" viewBox={`0 0 ${W} ${H}`} role="img"
            aria-label={`Top 10% of cards hold about ${c.top10}% of the value`}>
            <line className="an-curve__diag" x1={x(0)} y1={y(0)} x2={x(100)} y2={y(100)} />
            <polygon className="an-curve__fill" points={`${d} ${x(100)},${y(0)}`} />
            <polyline className="an-curve__line" points={d} fill="none" />
            <text className="an-curve__axis" x={x(100)} y={H - 6} textAnchor="end">cards, most valuable first →</text>
            <text className="an-curve__axis" x={PL - 5} y={y(100) + 4} textAnchor="end">100%</text>
            <text className="an-curve__axis" x={PL - 5} y={y(50) + 4} textAnchor="end">50%</text>
            <text className="an-curve__axis" x={PL - 5} y={y(0) + 4} textAnchor="end">0%</text>
            <text className="an-curve__axis" x={9} y={H / 2} transform={`rotate(-90 9 ${H / 2})`} textAnchor="middle">share of value</text>
        </svg>
    );
}

function Acquisitions({ rows }: { rows: CollectionAnalysis['acquisitions'] }) {
    const max = Math.max(1, ...rows.map(r => r.copies));
    return (
        <div>
            <h4 className="an-h">Added by month</h4>
            {rows.length === 0 ? <p className="est-note">Nothing added in the last two years.</p> : (
                <>
                    <div className="an-cols" role="img" aria-label="Copies added per month">
                        {rows.map(r => (
                            <div className="an-col" key={r.month} title={`${monthLabel(r.month)}: ${r.copies} copies, worth ${currencyFormat(r.value)} today`}>
                                <span className="an-col__bar" style={{ height: `${(r.copies / max) * 100}%` }} />
                                <span className="an-col__label mono">{monthLabel(r.month)}</span>
                            </div>
                        ))}
                    </div>
                    <p className="est-note an-note">
                        Copies added each month (bar), by the date you acquired them. Hover a bar for what that month is worth today.
                    </p>
                </>
            )}
        </div>
    );
}
