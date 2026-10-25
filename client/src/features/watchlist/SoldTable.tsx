import { useState } from "react";
import { Link } from "react-router-dom";
import Modal from "../../app/shared/components/Modal";
import ChangePill from "../../app/shared/components/ChangePill";
import { currencyFormat, shortDate } from "../../lib/util";
import { tierLabel } from "./grades";
import {
    useFetchSoldQuery, useUpdateSoldMutation, useRestoreSoldMutation, useRemoveSoldMutation,
    type SoldCopy, type Realized,
} from "./watchlistApi";

// The Sold track (2026-10-24): every copy the user marked sold, newest first,
// with what it cost, what it sold for, how long it was held and the realized
// P/L — plus edit, undo (restore to the portfolio) and delete.
export default function SoldTable({ realized }: { realized?: Realized | null }) {
    const { data, isLoading } = useFetchSoldQuery();
    const [editing, setEditing] = useState<SoldCopy | null>(null);
    const [restore, { isLoading: restoring }] = useRestoreSoldMutation();
    const [remove, { isLoading: removing }] = useRemoveSoldMutation();
    const [confirmDelete, setConfirmDelete] = useState<SoldCopy | null>(null);

    if (isLoading) return <p className="est-note">Loading your sales…</p>;
    if (!data || data.length === 0) {
        return (
            <p className="est-note">
                Nothing sold yet. On any position, use the <strong>$</strong> button to record a sale;
                it moves here with its realized gain or loss.
            </p>
        );
    }

    return (
        <>
            {realized && (
                <div className="sold-totals">
                    <div className="sold-totals__cell">
                        <span className="mono">Sales</span>
                        <strong>{realized.count}</strong>
                    </div>
                    <div className="sold-totals__cell">
                        <span className="mono">Proceeds</span>
                        <strong>{currencyFormat(realized.proceeds)}</strong>
                    </div>
                    <div className="sold-totals__cell">
                        <span className="mono">What you paid</span>
                        <strong>{realized.basis > 0 ? currencyFormat(realized.basis) : 'none recorded'}</strong>
                    </div>
                    <div className="sold-totals__cell">
                        <span className="mono">Profit from sales</span>
                        <span className="sold-totals__pills">
                            <ChangePill value={realized.plUsd} unit="usd" />
                            {realized.plPct != null && <ChangePill value={realized.plPct} suffix="on what you paid" />}
                        </span>
                    </div>
                    {realized.packCopies > 0 && (
                        <div className="est-note sold-totals__note">
                            {realized.packCopies} {realized.packCopies === 1 ? 'copy was a pack pull' : 'copies were pack pulls'} with
                            no recorded cost; their proceeds count as gain.
                        </div>
                    )}
                </div>
            )}

            <div className="screener-wrap">
                <table className="screener screener--positions screener--sold">
                    <thead>
                        <tr>
                            <th aria-label="Card image" />
                            <th>Card</th>
                            <th className="screener__mid">Condition</th>
                            <th className="screener__mid">Acquired</th>
                            <th className="screener__mid">Sold</th>
                            <th className="screener__mid">Held</th>
                            <th className="screener__mid">Profit</th>
                            <th aria-label="Actions" />
                        </tr>
                    </thead>
                    <tbody>
                        {data.map(s => (
                            <tr className="screener__row" key={s.id}>
                                <td className="screener__thumb-cell">
                                    <img className="screener__thumb" src={s.pictureUrl} alt="" loading="lazy" />
                                </td>
                                <td>
                                    <Link className="screener__name" to={`/catalog/${s.game}/${s.productId}`}>{s.name}</Link>
                                    <div className="mono">{[s.setName, s.rarity].filter(Boolean).join(' · ')}</div>
                                    {s.note && <div className="sold-note">{s.note}</div>}
                                </td>
                                <td>
                                    <span className="owned-condition">{tierLabel(s.grade)}</span>
                                    {s.printing && <div className="mono">{s.printing}</div>}
                                </td>
                                <td className="screener__num">
                                    <div className="mono">{shortDate(s.acquiredAt)}</div>
                                    <div>{s.source === 'paid' ? currencyFormat(s.purchasePrice) : <span className="pack-paid mono">pack pull</span>}</div>
                                </td>
                                <td className="screener__num">
                                    <div className="mono">{shortDate(s.soldAt)}</div>
                                    <div className="screener__price">{currencyFormat(s.soldPrice)}</div>
                                </td>
                                <td className="screener__num mono">{s.heldDays} d</td>
                                <td className="screener__num">
                                    <ChangePill value={s.plUsd} unit="usd" title={s.source === 'paid' ? 'Sale price minus what you paid' : 'Pack pull: no recorded cost'} />
                                    {s.plPct != null && <div><ChangePill value={s.plPct} /></div>}
                                </td>
                                <td className="screener__actions">
                                    <button className="btn btn--outline btn--sm" onClick={() => setEditing(s)} title="Edit this sale">Edit</button>
                                    <button className="btn btn--outline btn--sm" disabled={restoring}
                                        onClick={() => restore({ id: s.id })} title="Undo: put the copy back in your portfolio">Undo</button>
                                    <button className="btn btn--outline btn--sm btn--danger" disabled={removing}
                                        onClick={() => setConfirmDelete(s)} title="Delete this sale record">✕</button>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            {editing && <EditSaleModal sale={editing} onClose={() => setEditing(null)} />}
            {confirmDelete && (
                <Modal title="Delete sale" onClose={() => setConfirmDelete(null)}>
                    <p>
                        Delete the record of selling <strong>{confirmDelete.name}</strong> ({tierLabel(confirmDelete.grade)})?
                        The copy will be gone from both tracks. To put it back in your portfolio instead, use Undo.
                    </p>
                    <div className="modal__actions">
                        <button className="btn btn--outline" onClick={() => setConfirmDelete(null)}>Cancel</button>
                        <button className="btn btn--danger" disabled={removing}
                            onClick={async () => { await remove({ id: confirmDelete.id }); setConfirmDelete(null); }}>
                            Delete
                        </button>
                    </div>
                </Modal>
            )}
        </>
    );
}

function EditSaleModal({ sale, onClose }: { sale: SoldCopy; onClose: () => void }) {
    const [update, { isLoading }] = useUpdateSoldMutation();
    const [price, setPrice] = useState(sale.soldPrice.toFixed(2));
    const [soldAt, setSoldAt] = useState(sale.soldAt.slice(0, 10));
    const [note, setNote] = useState(sale.note ?? '');
    const p = Number(price);
    const valid = price.trim() !== '' && isFinite(p) && p >= 0;
    const today = new Date().toISOString().slice(0, 10);
    const save = async () => {
        if (!valid || isLoading) return;
        try {
            await update({ id: sale.id, price: p, soldAt: soldAt || null, note: note.trim() || null }).unwrap();
            onClose();
        } catch { /* keep the form open */ }
    };
    return (
        <Modal title={`Edit sale · ${sale.name}`} onClose={onClose}>
            <div className="own-form">
                <div className="field">
                    <label className="field-label" htmlFor="edit-sale-price">Sale price</label>
                    <input id="edit-sale-price" className="input" type="number" min="0" step="0.01" inputMode="decimal"
                        value={price} onChange={e => setPrice(e.target.value)} />
                </div>
                <div className="field">
                    <label className="field-label" htmlFor="edit-sale-date">Sold on</label>
                    <input id="edit-sale-date" className="input" type="date" max={today}
                        value={soldAt} onChange={e => setSoldAt(e.target.value)} />
                </div>
                <div className="field">
                    <label className="field-label" htmlFor="edit-sale-note">Note</label>
                    <input id="edit-sale-note" className="input" type="text" maxLength={200}
                        value={note} onChange={e => setNote(e.target.value)} />
                </div>
            </div>
            <div className="modal__actions">
                <button className="btn" disabled={!valid || isLoading} onClick={save}>{isLoading ? 'Saving…' : 'Save'}</button>
                <button className="btn btn--outline" onClick={onClose}>Cancel</button>
            </div>
        </Modal>
    );
}
