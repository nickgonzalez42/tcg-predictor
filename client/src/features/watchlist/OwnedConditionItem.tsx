import { useState } from "react";
import { Link } from "react-router-dom";
import type { Card, OwnedCopy } from "../../app/models/card";
import { currencyFormat, gameKey } from "../../lib/util";
import {
    useAddToWatchlistMutation,
    useUpdateOwnedCopyMutation,
    useRemoveOwnedCopyMutation,
} from "./watchlistApi";
import { PRICE_TIER_OPTIONS, tierLabel } from "./grades";
import ExpectedChange from "../catalog/ExpectedChange";
import { fallbackToCardBack } from "../../lib/cardImages";

// Per-copy grade select: the PriceCharting tiers ('' = Ungraded, i.e. raw).
const copyGradeOptions = PRICE_TIER_OPTIONS;

// Mirrors the backend's HasDetail: a copy with any purchase detail is its own
// tile; only fully blank copies stack into a quantity.
const hasDetail = (c: OwnedCopy) => c.purchasePrice != null || !!c.acquiredAt || !!c.note;

// One owned display unit. Either a STACK of blank copies at a (card + condition)
// with a quantity control, or a single DETAILED copy shown as its own card with
// its purchase info editable inline.
export default function OwnedConditionItem({ card }: { card: Card }) {
    const [addCopy, { isLoading: adding }] = useAddToWatchlistMutation();
    const [removeCopy, { isLoading: removing }] = useRemoveOwnedCopyMutation();
    const [expanded, setExpanded] = useState(false);
    const [editing, setEditing] = useState(false);

    const copies = card.ownedCopies ?? [];
    const detailedUnit = copies.length === 1 && hasDetail(copies[0]);
    const qty = card.ownedQuantity ?? copies.length;
    const grade = card.ownedGrade ?? '';   // '' = ungraded (raw) bucket
    const total = card.price != null ? card.price * qty : null;

    // card.game is the display name ("One Piece"); the API wants the key ("onepiece").
    const addOne = () => addCopy({ game: gameKey(card.game), productId: card.id, kind: 'owned', grade });
    const removeOne = () => {
        const target = copies[copies.length - 1];
        if (target) removeCopy({ id: target.id });
    };

    return (
        <div className="card">
            <img className="card__media" style={{ width: '100%', objectFit: 'contain' }}
                src={card.pictureUrl} alt={card.name}
                onError={e => fallbackToCardBack(e, card.game, card.cardType)} />
            <div className="card__body">
                <div className="card__title">{card.name}</div>
                <div className="owned-condition">
                    {tierLabel(card.ownedGrade)}
                    {detailedUnit && <span className="owned-condition__tag"> · noted</span>}
                </div>
                <div className="card__price">
                    {card.expectedChange != null ? (
                        <ExpectedChange card={card} />
                    ) : (
                        <>
                            {card.price != null ? currencyFormat(card.price) : '—'}
                            {total != null && qty > 1 && (
                                <span className="est-note"> · {currencyFormat(total)} total</span>
                            )}
                        </>
                    )}
                </div>

                {detailedUnit ? (
                    // A copy with purchase detail stands alone. Values show read-only;
                    // Edit opens the form, Save/Cancel close it again.
                    <div className="owned-copies">
                        {editing ? (
                            <OwnedCopyRow copy={copies[0]}
                                onDone={() => setEditing(false)}
                                onCancel={() => setEditing(false)} />
                        ) : (
                            <CopySummary copy={copies[0]} onEdit={() => setEditing(true)} />
                        )}
                    </div>
                ) : (
                    <>
                        <div className="qty-control">
                            <span className="field-label">Quantity</span>
                            <div className="qty-control__buttons">
                                <button className="btn btn--outline" disabled={removing || qty === 0}
                                    onClick={removeOne} title="Remove one copy">−</button>
                                <strong className="qty-control__count">{qty}</strong>
                                <button className="btn btn--outline" disabled={adding}
                                    onClick={addOne} title="Add one copy">＋</button>
                            </div>
                        </div>

                        <button className="btn btn--outline owned-expand" onClick={() => setExpanded(v => !v)}>
                            {expanded ? 'Hide copy details' : 'Add copy details'}
                        </button>
                        {expanded && (
                            <div className="owned-copies">
                                <div className="owned-copies__head">
                                    Adding a note, date or price moves that copy to its own card.
                                </div>
                                {copies.map(copy => <OwnedCopyRow key={copy.id} copy={copy} />)}
                            </div>
                        )}
                    </>
                )}
            </div>
            <div className="card__actions">
                <Link className="btn btn--outline" to={`/catalog/${gameKey(card.game)}/${card.id}`}>View</Link>
            </div>
        </div>
    );
}

// Read-only view of a detailed copy's values. A copy with no explicit acquired
// date defaults to the day it was added to the portfolio.
function CopySummary({ copy, onEdit }: { copy: OwnedCopy; onEdit: () => void }) {
    return (
        <div className="owned-summary">
            <div className="owned-summary__values">
                {copy.purchasePrice != null && (
                    <div><span className="owned-summary__label">Paid</span>{currencyFormat(copy.purchasePrice)}</div>
                )}
                <div>
                    <span className="owned-summary__label">Acquired</span>
                    {copy.acquiredAt
                        ? copy.acquiredAt.slice(0, 10)
                        : `${copy.addedAt.slice(0, 10)} (added)`}
                </div>
                {copy.note && (
                    <div><span className="owned-summary__label">Note</span>{copy.note}</div>
                )}
            </div>
            <button className="btn btn--outline" onClick={onEdit}>Edit</button>
        </div>
    );
}

// Editable row for one owned copy. Changing the grade moves the copy to another
// condition's card; adding/clearing detail moves it between stack and standalone.
// onDone fires after a successful save; onCancel (when given) shows a Cancel
// button that closes the form without touching the copy.
export function OwnedCopyRow({ copy, onDone, onCancel }: {
    copy: OwnedCopy; onDone?: () => void; onCancel?: () => void;
}) {
    const [update, { isLoading: saving }] = useUpdateOwnedCopyMutation();
    const [remove, { isLoading: removing }] = useRemoveOwnedCopyMutation();

    // A detailed copy with no explicit acquired date defaults to its added date
    // (matching the summary display). Blank stacked copies stay empty so a save
    // doesn't turn them into standalone detailed copies.
    const initialAcquired = copy.acquiredAt
        ? copy.acquiredAt.slice(0, 10)
        : hasDetail(copy) ? copy.addedAt.slice(0, 10) : '';

    const [grade, setGrade] = useState(copy.grade ?? '');
    const [price, setPrice] = useState(copy.purchasePrice != null ? String(copy.purchasePrice) : '');
    const [acquired, setAcquired] = useState(initialAcquired);
    const [note, setNote] = useState(copy.note ?? '');

    const dirty =
        grade !== (copy.grade ?? '') ||
        price !== (copy.purchasePrice != null ? String(copy.purchasePrice) : '') ||
        acquired !== initialAcquired ||
        note !== (copy.note ?? '');

    const save = async () => {
        try {
            await update({
                id: copy.id,
                grade: grade || null,
                purchasePrice: price.trim() === '' ? null : Number(price),
                acquiredAt: acquired || null,
                note: note.trim() === '' ? null : note.trim(),
            }).unwrap();
            onDone?.();   // close the form only if the save succeeded
        } catch {
            // save failed — keep the form open with the user's input intact
        }
    };

    return (
        <div className="owned-copy">
            <div className="owned-copy__fields">
                <label>Grade
                    <select className="input" value={grade} onChange={e => setGrade(e.target.value)}>
                        {copyGradeOptions.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                </label>
                <label>Paid
                    <input className="input" type="number" min="0" step="0.01" inputMode="decimal"
                        placeholder="—" value={price} onChange={e => setPrice(e.target.value)} />
                </label>
                <label>Acquired
                    <input className="input" type="date" value={acquired} onChange={e => setAcquired(e.target.value)} />
                </label>
                <label className="owned-copy__note">Note
                    <input className="input" type="text" maxLength={200}
                        placeholder="—" value={note} onChange={e => setNote(e.target.value)} />
                </label>
            </div>
            <div className="owned-copy__actions">
                <button className="btn btn--outline" disabled={!dirty || saving} onClick={save}>Save</button>
                {onCancel && (
                    <button className="btn btn--outline" onClick={onCancel}>Cancel</button>
                )}
                <button className="btn btn--outline" disabled={removing}
                    title="Remove this copy" onClick={() => remove({ id: copy.id })}>✕</button>
            </div>
        </div>
    );
}
