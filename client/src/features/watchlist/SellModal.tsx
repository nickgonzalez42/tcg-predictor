import { useState } from "react";
import Modal from "../../app/shared/components/Modal";
import ChangePill from "../../app/shared/components/ChangePill";
import type { OwnedCopy } from "../../app/models/card";
import { useSellCopiesMutation } from "./watchlistApi";
import { currencyFormat } from "../../lib/util";

// "Mark as sold" for a position (2026-10-24): how many of the stack's copies,
// the sale price per copy (prefilled with today's market price), the sale
// date and an optional note. The copies move to the Sold track with their
// cost basis intact, so the realized P/L is known the moment they go.
export default function SellModal({ title, copies, marketPrice, onClose }: {
    title: string; copies: OwnedCopy[]; marketPrice?: number | null; onClose: () => void;
}) {
    const [sell, { isLoading }] = useSellCopiesMutation();
    const [qty, setQty] = useState(String(copies.length === 1 ? 1 : 1));
    const [price, setPrice] = useState(marketPrice != null ? marketPrice.toFixed(2) : '');
    const [soldAt, setSoldAt] = useState(new Date().toISOString().slice(0, 10));
    const [note, setNote] = useState('');
    const [error, setError] = useState<string | null>(null);

    const n = Number(qty);
    const p = Number(price);
    const valid = Number.isInteger(n) && n >= 1 && n <= copies.length
        && price.trim() !== '' && isFinite(p) && p >= 0;
    const today = new Date().toISOString().slice(0, 10);

    // Sell the newest copies first (the same ones "−" would remove); copies in
    // a stack are identical, so the choice changes nothing else.
    const chosen = copies.slice(Math.max(0, copies.length - (valid ? n : 1)));
    const paid = chosen.filter(c => c.source === 'paid');
    const basis = paid.reduce((s, c) => s + (c.purchasePrice ?? 0), 0);
    const proceeds = valid ? p * n : 0;

    const submit = async () => {
        if (!valid || isLoading) return;
        setError(null);
        try {
            await sell({ ids: chosen.map(c => c.id), price: p, soldAt: soldAt || undefined,
                         note: note.trim() || undefined }).unwrap();
            onClose();
        } catch {
            setError("Couldn't record the sale. Try again.");
        }
    };

    return (
        <Modal title={`Mark as sold · ${title}`} onClose={onClose}>
            <p className="est-note" style={{ marginTop: 0 }}>
                Sold copies leave your positions and move to the Sold track with their cost basis,
                so you keep the realized gain or loss. You can undo from there.
            </p>
            <div className="own-form">
                {copies.length > 1 && (
                    <div className="field">
                        <label className="field-label" htmlFor="sell-qty">Copies sold (of {copies.length})</label>
                        <input id="sell-qty" className="input" type="number" min="1" max={copies.length} step="1"
                            inputMode="numeric" value={qty} onChange={e => setQty(e.target.value)} />
                    </div>
                )}
                <div className="field">
                    <label className="field-label" htmlFor="sell-price">Sale price per copy</label>
                    <input id="sell-price" className="input" type="number" min="0" step="0.01" inputMode="decimal"
                        value={price} autoFocus onChange={e => setPrice(e.target.value)}
                        placeholder={marketPrice != null ? marketPrice.toFixed(2) : '0.00'} />
                    {marketPrice != null && (
                        <span className="field-hint">Market today: {currencyFormat(marketPrice)}</span>
                    )}
                </div>
                <div className="field">
                    <label className="field-label" htmlFor="sell-date">Sold on</label>
                    <input id="sell-date" className="input" type="date" max={today}
                        value={soldAt} onChange={e => setSoldAt(e.target.value)} />
                </div>
                <div className="field">
                    <label className="field-label" htmlFor="sell-note">Note (optional)</label>
                    <input id="sell-note" className="input" type="text" maxLength={200}
                        placeholder="eBay, local shop, trade…" value={note} onChange={e => setNote(e.target.value)} />
                </div>
            </div>

            {valid && (
                <div className="sell-preview">
                    <div><span className="mono">Proceeds</span><strong>{currencyFormat(proceeds)}</strong></div>
                    <div>
                        <span className="mono">Cost basis</span>
                        <strong>{paid.length ? currencyFormat(basis) : 'none recorded'}</strong>
                    </div>
                    <div>
                        <span className="mono">Realized</span>
                        <ChangePill value={proceeds - basis} unit="usd" />
                    </div>
                    {paid.length < chosen.length && (
                        <div className="est-note sell-preview__note">
                            {chosen.length - paid.length} of these {chosen.length === 1 ? 'is a pack pull' : 'are pack pulls'} with
                            no recorded cost, so the whole sale price counts as gain.
                        </div>
                    )}
                </div>
            )}
            {error && <p className="comment-error">{error}</p>}

            <div className="modal__actions">
                <button className="btn" disabled={!valid || isLoading} onClick={submit}>
                    {isLoading ? 'Saving…' : `Mark ${valid ? n : ''} sold`}
                </button>
                <button className="btn btn--outline" onClick={onClose}>Cancel</button>
            </div>
        </Modal>
    );
}
