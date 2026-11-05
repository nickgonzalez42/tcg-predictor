import { Link } from "react-router-dom";
import Modal from "../../app/shared/components/Modal";
import CardLoader from "../../app/shared/components/CardLoader";
import ChangePill from "../../app/shared/components/ChangePill";
import { useFetchCollectionOutlookQuery, type CollectionOutlook, type OutlookLongest } from "./watchlistApi";
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

function Outlook({ o, onClose }: { o: CollectionOutlook; onClose: () => void }) {
    const covered = o.coverage.filter(c => c.copies > 0);
    const held = o.coverage.find(c => c.key === 'none');
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

            {/* ----- what the figures rest on ----- */}
            <section className="an-section">
                <h4 className="an-h">What's behind it</h4>
                <div className="ol-cov" role="img"
                    aria-label={covered.map(c => `${LONGEST[c.key]}: ${c.pct}% of today's value`).join(', ')}>
                    {covered.map(c => (
                        <span key={c.key} className={`ol-cov__seg ol-cov__seg--${c.key}`} style={{ flex: c.value }}
                            title={`${LONGEST[c.key]}: ${copies(c.copies)}, ${currencyFormat(c.value)} today`} />
                    ))}
                </div>
                <div className="an-legend mono ol-legend">
                    {covered.map(c => (
                        <span key={c.key}><i className={`an-dot ol-dot--${c.key}`} />{c.copies} · {LONGEST[c.key]}</span>
                    ))}
                </div>
                <p className="est-note an-note">
                    Each copy moves by the model's call for its card, condition and printing. Past its longest call
                    it holds that value, and a card with no call at all stays at today's price
                    {held && held.copies > 0 ? ` (${copies(held.copies)}, ${currencyFormat(held.value)})` : ''}.
                    A direction signal, not a promise.
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
                                    <td className="mono">{currencyFormat(g.v1m)}</td>
                                    <td className="mono">{currencyFormat(g.v6m)}</td>
                                    <td className="mono">
                                        {currencyFormat(g.v12m)}
                                        <span className="ol-table__pill"><ChangePill value={pct(g.v12m, g.now)} /></span>
                                    </td>
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
                                        <ChangePill value={c.changeUsd} unit="usd" title={`${currencyFormat(c.now)} today`} />
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
