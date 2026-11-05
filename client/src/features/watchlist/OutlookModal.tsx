import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { createChart, AreaSeries, LineSeries, LineStyle, ColorType } from "lightweight-charts";
import Modal from "../../app/shared/components/Modal";
import CardLoader from "../../app/shared/components/CardLoader";
import ChangePill from "../../app/shared/components/ChangePill";
import { useFetchCollectionOutlookQuery, type CollectionOutlook, type OutlookLongest } from "./watchlistApi";
import { fade } from "./ValueChart";
import { currencyFormat, gameKey } from "../../lib/util";
import { fallbackToCardBack } from "../../lib/cardImages";

// Tools > Value outlook (2026-11-05, user: "a tool on the portfolio page
// that shows the predicted value of your collection in 1M, 6M and 1Y; if a
// card doesn't carry a prediction, its value stagnates at the longest
// forecast"). Every copy starts at today's price at its own condition and
// moves by the model's call for that card at each window; past its longest
// call it holds that value, and a card with no call at all stays at today's
// price. All figures come from portfolio/outlook in one call; includePacks
// follows the page toggle so "Today" matches the headline number.
// Same-day feedback: the value path is drawn as a chart, every window and
// mover carries its percentage, and the coverage bar became a sentence (a
// solid single-colour bar said nothing when every copy had a 1-year call).
export default function OutlookModal({ includePacks, onClose }: { includePacks: boolean; onClose: () => void }) {
    const { data, isLoading } = useFetchCollectionOutlookQuery({ includePacks });
    return (
        <Modal title="Value outlook" onClose={onClose} className="modal--wide">
            {isLoading || !data ? <CardLoader /> : data.copies === 0 ? (
                <p className="est-note">
                    Nothing to look ahead at yet. Add cards from the catalog or the <Link to="/scan" onClick={onClose}>scanner</Link>.
                </p>
            ) : data.priced === 0 ? (
                <p className="est-note">None of your cards has a price yet, so there is nothing to project.</p>
            ) : <Outlook o={data} onClose={onClose} />}
        </Modal>
    );
}

const WINDOWS = [['1m', 'In 1 month'], ['6m', 'In 6 months'], ['12m', 'In 1 year']] as const;
const LONGEST: Record<OutlookLongest, string> = {
    '12m': '1-year call', '6m': '6-month call', '1m': '1-month call', early: 'early estimate', none: 'no call yet',
};
const copies = (n: number) => `${n.toLocaleString('en-US')} cop${n === 1 ? 'y' : 'ies'}`;
const pct = (v: number, base: number) => base > 0 ? (v / base - 1) * 100 : null;

// What the figures rest on, as one sentence: "All 114 copies carry a 1-year
// call." or "98 copies carry a 1-year call, 12 a 6-month call only, 2 no
// call yet (held at today's price, $340.00)."
function coverageSentence(o: CollectionOutlook): string {
    const phrase: Record<OutlookLongest, (value: number) => string> = {
        '12m': () => 'a 1-year call',
        '6m': () => 'a 6-month call only',
        '1m': () => 'a 1-month call only',
        early: () => 'an early estimate only',
        none: value => `no call yet (held at today's price, ${currencyFormat(value)})`,
    };
    const buckets = o.coverage.filter(c => c.copies > 0);
    if (buckets.length === 1) return `All ${copies(buckets[0].copies)} carry ${phrase[buckets[0].key](buckets[0].value)}.`;
    return buckets
        .map((c, i) => i === 0 ? `${copies(c.copies)} carry ${phrase[c.key](c.value)}` : `${c.copies} ${phrase[c.key](c.value)}`)
        .join(', ') + '.';
}

function Outlook({ o, onClose }: { o: CollectionOutlook; onClose: () => void }) {
    return (
        <div className="an">
            {/* ----- today and the three windows ----- */}
            <div className="ol-tiles">
                <div className="an-tile ol-tile--today">
                    <span className="an-tile__label">Today</span>
                    <span className="an-tile__value">{currencyFormat(o.now)}</span>
                    <span className="an-tile__sub">
                        {o.priced === o.copies ? copies(o.copies) : `${o.priced} of ${copies(o.copies)} priced`}
                    </span>
                </div>
                {WINDOWS.map(([k, label]) => {
                    const w = o.windows[k];
                    return (
                        <div className="an-tile" key={k}>
                            <span className="an-tile__label">{label}</span>
                            <span className="an-tile__value">{currencyFormat(w.value)}</span>
                            <span className="an-tile__sub an-tile__pills">
                                <ChangePill value={w.changeUsd} unit="usd" />
                                <ChangePill value={w.changePct} />
                            </span>
                            <span className="an-tile__sub" title={`${w.up} up, ${w.down} down`}>
                                {w.called} with a call · {w.held} held
                            </span>
                        </div>
                    );
                })}
            </div>

            {/* ----- the value path ----- */}
            <section className="an-section">
                <h4 className="an-h">Where it's headed</h4>
                <OutlookChart o={o} />
                <p className="est-note an-note">
                    {coverageSentence(o)} Each copy moves by the model's call for its card, condition and printing.
                    Past its longest call it holds that value, and a card with no call at all stays at today's price.
                    The dashed line is today's value. A direction signal, not a promise.
                </p>
            </section>

            {/* ----- by game + the cards doing the most ----- */}
            <section className="an-section an-split">
                <div>
                    <h4 className="an-h">By game</h4>
                    <table className="ol-table">
                        <thead>
                            <tr><th>Game</th><th>Today</th><th>1 month</th><th>6 months</th><th>1 year</th></tr>
                        </thead>
                        <tbody>
                            {o.byGame.map(g => (
                                <tr key={g.game}>
                                    <td>{g.label} <span className="ol-muted">×{g.copies}</span></td>
                                    <td className="mono">{currencyFormat(g.now)}</td>
                                    {([g.v1m, g.v6m, g.v12m] as const).map((v, i) => (
                                        <td className="mono" key={i}>
                                            {currencyFormat(v)}
                                            <span className="ol-table__pill"><ChangePill value={pct(v, g.now)} /></span>
                                        </td>
                                    ))}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
                <div>
                    <h4 className="an-h">Cards doing the most</h4>
                    {o.movers.length === 0 ? (
                        <p className="est-note">No card in your collection has a call yet.</p>
                    ) : (
                        <ul className="an-top">
                            {o.movers.map(c => (
                                <li key={`${c.game}:${c.id}`} className="an-top__row">
                                    <img src={c.pictureUrl} alt="" loading="lazy"
                                        onError={e => fallbackToCardBack(e, c.game)} />
                                    <span className="an-top__text">
                                        <Link className="an-top__name" to={`/catalog/${gameKey(c.game)}/${c.id}`} onClick={onClose}>{c.name}</Link>
                                        <span className="mono an-top__sub">
                                            {[c.setName, c.copies > 1 ? `×${c.copies}` : null, LONGEST[c.longest]].filter(Boolean).join(' · ')}
                                        </span>
                                    </span>
                                    <span className="an-top__side mono">
                                        <span>{currencyFormat(c.value)} <span className="an-top__pct">in 1 year</span></span>
                                        <span className="an-tile__pills">
                                            <ChangePill value={c.changeUsd} unit="usd" title={`${currencyFormat(c.now)} today`} />
                                            <ChangePill value={c.changePct} />
                                        </span>
                                    </span>
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            </section>
        </div>
    );
}

// The collection's value from today to a year out, on a real date axis so
// the month between today and the first call is a month wide and the six
// after it are six. One series (the total), markers on the four points the
// tiles already name, a dashed reference at today's value so a dip or a
// climb reads at a glance, and the library's crosshair as the hover layer.
// Colours come from the theme variables, like the portfolio value chart.
function OutlookChart({ o }: { o: CollectionOutlook }) {
    const ref = useRef<HTMLDivElement>(null);
    useEffect(() => {
        const el = ref.current;
        if (!el) return;
        const css = getComputedStyle(el);
        const v = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
        const line = v('--link', '#7fb0ea');
        const textMuted = v('--text-muted', '#8b96ad');
        const border = v('--border', '#2e3a52');

        const today = new Date();
        const at = (months: number) => {
            const d = new Date(today);
            d.setMonth(d.getMonth() + months);
            return d.toISOString().slice(0, 10);
        };
        const points = [
            { time: at(0), value: o.now },
            { time: at(1), value: o.windows['1m'].value },
            { time: at(6), value: o.windows['6m'].value },
            { time: at(12), value: o.windows['12m'].value },
        ];

        const chart = createChart(el, {
            height: 220,
            autoSize: true,
            layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: textMuted },
            grid: { vertLines: { color: border }, horzLines: { color: border } },
            rightPriceScale: { borderColor: border },
            timeScale: { borderColor: border, fixLeftEdge: true, fixRightEdge: true },
            handleScroll: false,
            handleScale: false,
        });
        const path = chart.addSeries(AreaSeries, {
            lineColor: line,
            topColor: fade(line, 0.22),
            bottomColor: fade(line, 0.02),
            lineWidth: 2,
            pointMarkersVisible: true,
            pointMarkersRadius: 4,
            priceLineVisible: false,
            lastValueVisible: false,
            priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
        });
        path.setData(points);
        const base = chart.addSeries(LineSeries, {
            color: textMuted,
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
        });
        base.setData(points.map(p => ({ time: p.time, value: o.now })));
        chart.timeScale().fitContent();
        return () => chart.remove();
    }, [o]);
    return <div ref={ref} className="ol-chart" role="img"
        aria-label={`Value today ${currencyFormat(o.now)}, in 1 month ${currencyFormat(o.windows['1m'].value)}, in 6 months ${currencyFormat(o.windows['6m'].value)}, in 1 year ${currencyFormat(o.windows['12m'].value)}`} />;
}
