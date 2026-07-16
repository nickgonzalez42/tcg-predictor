import { useState } from "react";
import Modal from "../../app/shared/components/Modal";
import type { Card } from "../../app/models/card";
import {
    useFetchAlertsQuery, useAddAlertMutation, useDeleteAlertMutation,
    type AlertKind, type CardAlert,
} from "./watchlistApi";
import { PRICE_TIER_OPTIONS, tierLabel } from "./grades";
import { currencyFormat } from "../../lib/util";

const HORIZONS = [
    { value: '1m', label: '1 month' },
    { value: '6m', label: '6 months' },
    { value: '12m', label: '1 year' },
];
// 1W stays in the label map so any legacy 1w alert still renders correctly.
const HZ_SHORT: Record<string, string> = { '1w': '1W', '1m': '1M', '6m': '6M', '12m': '1Y' };

const pct = (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`;

// "PSA 10 · 1M forecast ≥ $500" — one line describing an alert.
function describe(a: CardAlert) {
    const dir = a.direction === 'above' ? '≥' : '≤';
    const cond = tierLabel(a.grade ?? '');
    if (a.kind === 'price') return `${cond} price ${dir} ${currencyFormat(a.target)}`;
    const hz = HZ_SHORT[a.horizon ?? ''] ?? a.horizon;
    return a.kind === 'fcst_price'
        ? `${cond} · ${hz} forecast ${dir} ${currencyFormat(a.target)}`
        : `${cond} · ${hz} forecast ${dir} ${pct(a.target)}`;
}

function currentLabel(a: CardAlert) {
    if (a.current == null) return 'no data';
    return a.kind === 'fcst_pct' ? `now ${pct(a.current)}` : `now ${currencyFormat(a.current)}`;
}

// Alert manager for one card: every existing alert (live value + hit badge +
// delete), and a form to add more — on the current price, a forecast price,
// or a forecast % change, per condition tier and horizon.
export default function AlertModal({ card, game, defaultGrade, onClose }: {
    card: Card; game: string; defaultGrade: string; onClose: () => void;
}) {
    const { data: alerts } = useFetchAlertsQuery();
    const [addAlert, { isLoading: adding }] = useAddAlertMutation();
    const [deleteAlert] = useDeleteAlertMutation();
    const mine = (alerts ?? []).filter(a => a.game === game && a.productId === card.id);

    const [kind, setKind] = useState<AlertKind>('price');
    const [horizon, setHorizon] = useState('1m');
    const [grade, setGrade] = useState(defaultGrade);
    const [direction, setDirection] = useState<'above' | 'below'>('below');
    const [value, setValue] = useState('');
    const [error, setError] = useState<string | null>(null);

    const num = Number(value);
    const valid = value.trim() !== '' && isFinite(num)
        && (kind === 'fcst_pct' ? Math.abs(num) <= 1000 : num > 0);

    const submit = async () => {
        if (!valid || adding) return;
        setError(null);
        try {
            await addAlert({
                game, productId: card.id, grade: grade || undefined, kind,
                horizon: kind === 'price' ? undefined : horizon,
                direction, target: num,
            }).unwrap();
            setValue('');
        } catch (e) {
            const msg = (e as { data?: string })?.data;
            setError(typeof msg === 'string' ? msg : "Couldn't add the alert. Try again.");
        }
    };

    return (
        <Modal title={`Alerts · ${card.name}`} onClose={onClose}>
            {mine.length > 0 ? (
                <ul className="alert-list">
                    {mine.map(a => (
                        <li key={a.id} className={`alert-list__row${a.hit ? ' alert-list__row--hit' : ''}`}>
                            <span className="alert-list__desc">
                                {describe(a)}
                                <span className="alert-list__now mono"> · {currentLabel(a)}</span>
                            </span>
                            {a.hit && <span className="alert-list__hit mono">HIT</span>}
                            <button className="btn btn--outline btn--circle" title="Delete alert"
                                onClick={() => deleteAlert({ id: a.id })}>✕</button>
                        </li>
                    ))}
                </ul>
            ) : (
                <p className="est-note">No alerts on this card yet.</p>
            )}

            <div className="own-form alert-form">
                <div className="field">
                    <label className="field-label" htmlFor="al-kind">Alert on</label>
                    <select id="al-kind" className="input" value={kind}
                        onChange={e => setKind(e.target.value as AlertKind)}>
                        <option value="price">Actual price</option>
                        <option value="fcst_price">Forecast price</option>
                        <option value="fcst_pct">Forecast % growth</option>
                    </select>
                </div>
                {kind !== 'price' && (
                    <div className="field">
                        <label className="field-label" htmlFor="al-horizon">Timeframe</label>
                        <select id="al-horizon" className="input" value={horizon}
                            onChange={e => setHorizon(e.target.value)}>
                            {HORIZONS.map(h => <option key={h.value} value={h.value}>{h.label}</option>)}
                        </select>
                    </div>
                )}
                <div className="field">
                    <label className="field-label" htmlFor="al-grade">Condition</label>
                    <select id="al-grade" className="input" value={grade}
                        onChange={e => setGrade(e.target.value)}>
                        {PRICE_TIER_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                </div>
                <div className="field">
                    <label className="field-label" htmlFor="al-dir">Notify when</label>
                    <select id="al-dir" className="input" value={direction}
                        onChange={e => setDirection(e.target.value as 'above' | 'below')}>
                        <option value="below">At or below</option>
                        <option value="above">At or above</option>
                    </select>
                </div>
                <div className="field">
                    <label className="field-label" htmlFor="al-value">
                        {kind === 'fcst_pct' ? 'Percent (e.g. 25 or -20)' : 'Price ($)'}
                    </label>
                    <input id="al-value" className="input" type="number" step="any"
                        inputMode="decimal" value={value}
                        onChange={e => setValue(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter') submit(); }} />
                </div>
            </div>

            {error && <p className="comment-error">{error}</p>}

            <div className="modal__actions">
                <button className="btn" disabled={!valid || adding} onClick={submit}>
                    {adding ? 'Adding…' : 'Add alert'}
                </button>
                <button className="btn btn--outline" onClick={onClose}>Done</button>
            </div>
        </Modal>
    );
}
