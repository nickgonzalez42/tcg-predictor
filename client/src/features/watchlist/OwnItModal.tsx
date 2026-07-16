import { useState } from "react";
import Modal from "../../app/shared/components/Modal";
import type { Card } from "../../app/models/card";
import { useImportOwnedMutation, useRemoveFromWatchlistMutation } from "./watchlistApi";
import { PRICE_TIER_OPTIONS } from "./grades";

// "Own it" from the watchlist: collect the copy's details, add it to the
// portfolio (one bulk-import row — it handles grade/quantity/price/date), then
// take the card off the watchlist and close.
export default function OwnItModal({ card, game, defaultGrade, onClose }: {
    card: Card; game: string; defaultGrade: string; onClose: () => void;
}) {
    const [importOwned, { isLoading: adding }] = useImportOwnedMutation();
    const [removeWatch] = useRemoveFromWatchlistMutation();
    const [grade, setGrade] = useState(defaultGrade);
    const [qty, setQty] = useState('1');
    const [paid, setPaid] = useState('');
    const [acquired, setAcquired] = useState('');
    const [error, setError] = useState<string | null>(null);

    const quantity = Number(qty);
    const paidNum = Number(paid);
    const valid = Number.isInteger(quantity) && quantity >= 1 && quantity <= 999
        && (paid.trim() === '' || (isFinite(paidNum) && paidNum >= 0));

    const today = new Date().toISOString().slice(0, 10);

    const submit = async () => {
        if (!valid || adding) return;
        setError(null);
        try {
            const res = await importOwned({
                rows: [{
                    game,
                    productId: card.id,
                    grade: grade || undefined,
                    quantity,
                    purchasePrice: paid.trim() === '' ? undefined : paidNum,
                    acquiredAt: acquired || undefined,
                }],
            }).unwrap();
            const row = res.rows[0];
            if (row?.status !== 'imported') {
                setError(row?.message ?? "Couldn't add the card. Try again.");
                return;
            }
            // Added — it lives in the portfolio now, so stop watching it. If
            // this removal hiccups the add still stands; the star removes it.
            try { await removeWatch({ game, productId: card.id, kind: 'wishlist' }).unwrap(); }
            catch { /* non-fatal */ }
            onClose();
        } catch {
            setError("Couldn't add the card. Try again.");
        }
    };

    return (
        <Modal title={`Own it · ${card.name}`} onClose={onClose}>
            <p className="est-note">
                Adds the card to your portfolio and takes it off your watchlist.
            </p>

            <div className="own-form">
                <div className="field">
                    <label className="field-label" htmlFor="own-grade">Condition</label>
                    <select id="own-grade" className="input" value={grade}
                        onChange={e => setGrade(e.target.value)}>
                        {PRICE_TIER_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                </div>
                <div className="field">
                    <label className="field-label" htmlFor="own-qty">Copies</label>
                    <input id="own-qty" className="input" type="number" min="1" max="999" step="1"
                        inputMode="numeric" value={qty} onChange={e => setQty(e.target.value)} />
                </div>
                <div className="field">
                    <label className="field-label" htmlFor="own-paid">Price paid per copy (optional)</label>
                    <input id="own-paid" className="input" type="number" min="0" step="any"
                        inputMode="decimal" placeholder="Blank = market price on the acquired date"
                        value={paid} onChange={e => setPaid(e.target.value)} />
                </div>
                <div className="field">
                    <label className="field-label" htmlFor="own-date">Date acquired (optional)</label>
                    <input id="own-date" className="input" type="date" max={today}
                        value={acquired} onChange={e => setAcquired(e.target.value)} />
                </div>
            </div>

            {error && <p className="comment-error">{error}</p>}

            <div className="modal__actions">
                <button className="btn" disabled={!valid || adding} onClick={submit}>
                    {adding ? 'Adding…' : 'Add to portfolio'}
                </button>
                <button className="btn btn--outline" onClick={onClose}>Cancel</button>
            </div>
        </Modal>
    );
}
