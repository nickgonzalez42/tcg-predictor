import { useState } from "react";
import { Link } from "react-router-dom";
import { useAddToWatchlistMutation, useRemoveOwnedCopyMutation } from "./watchlistApi";
import { OwnedCopyRow } from "./OwnedConditionItem";
import { tierLabel } from "./grades";
import CardThumbCell from "../../app/shared/components/CardThumbCell";
import ChangePill from "../../app/shared/components/ChangePill";
import Sparkline from "../../app/shared/components/Sparkline";
import Modal from "../../app/shared/components/Modal";
import { currencyFormat, gameKey, shortDate } from "../../lib/util";
import type { Card } from "../../app/models/card";

// One position row (a card + condition unit). Clicking the row expands the
// per-copy editor inline; −/＋ remove or add a copy at this condition.
export default function PositionRow({ card, hasYear }: { card: Card; hasYear: boolean }) {
    const [expanded, setExpanded] = useState(false);
    const [confirming, setConfirming] = useState(false);
    const [addCopy, { isLoading: adding }] = useAddToWatchlistMutation();
    const [removeCopy, { isLoading: removing }] = useRemoveOwnedCopyMutation();

    const copies = card.ownedCopies ?? [];
    const qty = card.ownedQuantity ?? copies.length;
    const grade = card.ownedGrade ?? '';
    const mktValue = card.price != null ? card.price * qty : null;

    const paidCopies = copies.filter(c => c.purchasePrice != null);
    const paid = paidCopies.length ? paidCopies.reduce((s, c) => s + (c.purchasePrice ?? 0), 0) : null;
    // P/L compares only the copies that have a recorded cost.
    const pl = paid != null && card.price != null ? card.price * paidCopies.length - paid : null;

    const addOne = () => addCopy({ game: gameKey(card.game), productId: card.id, kind: 'owned', grade });
    const removeOne = () => {
        const blank = [...copies].reverse().find(c => c.purchasePrice == null && !c.acquiredAt && !c.note);
        const target = blank ?? copies[copies.length - 1];
        if (target) removeCopy({ id: target.id });
    };
    // Removing the LAST copy deletes the whole position — confirm that one.
    const onMinus = () => (qty <= 1 ? setConfirming(true) : removeOne());

    return (
        <>
            {confirming && (
                <Modal title="Remove from portfolio" onClose={() => setConfirming(false)}>
                    <p>
                        This is the last copy of <strong>{card.name}</strong> ({tierLabel(card.ownedGrade)}).
                        Removing it deletes the position from your portfolio.
                    </p>
                    <div className="modal__actions">
                        <button className="btn btn--outline" onClick={() => setConfirming(false)}>
                            Cancel
                        </button>
                        <button className="btn btn--danger" disabled={removing}
                            onClick={() => { removeOne(); setConfirming(false); }}>
                            Remove
                        </button>
                    </div>
                </Modal>
            )}
            <tr className="screener__row" onClick={() => setExpanded(v => !v)}>
                <CardThumbCell card={card} />
                <td>
                    <Link className="screener__name" to={`/catalog/${gameKey(card.game)}/${card.id}`}
                        onClick={e => e.stopPropagation()}>
                        {card.name}
                    </Link>
                    <div className="mono">{[card.setName, card.rarity].filter(Boolean).join(' · ')}</div>
                </td>
                <td><span className="owned-condition">{tierLabel(card.ownedGrade)}</span></td>
                <td className="screener__num">{qty}</td>
                <td className="screener__num">{paid != null ? currencyFormat(paid) : '—'}</td>
                <td className="screener__num screener__price">
                    {mktValue != null ? currencyFormat(mktValue) : '—'}
                    {card.priceAsOf && <div className="mono price-asof">{shortDate(card.priceAsOf)}</div>}
                </td>
                <td className="screener__num">
                    {pl != null ? <ChangePill value={pl} unit="usd" title="vs recorded cost" /> : <span className="mono">—</span>}
                </td>
                <td className="screener__num">
                    <ChangePill value={hasYear ? card.fcst12Pct : card.fcst6Pct}
                        title={`${hasYear ? '1 year' : '6 month'} model forecast`} />
                </td>
                <td><Sparkline points={card.sparkline} /></td>
                <td className="screener__actions" onClick={e => e.stopPropagation()}>
                    {/* Row click still expands the copy editor (paid/date/note). */}
                    <button className="btn btn--outline btn--circle" disabled={removing || qty === 0}
                        onClick={onMinus} title="Remove one copy">−</button>
                    <button className="btn btn--outline btn--circle" disabled={adding}
                        onClick={addOne} title="Add one copy">＋</button>
                </td>
            </tr>
            {expanded && (
                <tr className="position-editor">
                    <td colSpan={10}>
                        <div className="owned-copies" style={{ borderTop: 'none', marginTop: 0 }}>
                            <div className="owned-copies__head">
                                Copies at {tierLabel(card.ownedGrade)}. A copy with a paid price,
                                date or note becomes its own position row.
                            </div>
                            {copies.map(copy => (
                                <OwnedCopyRow key={copy.id} copy={copy}
                                    onClose={() => setExpanded(false)} />
                            ))}
                        </div>
                    </td>
                </tr>
            )}
        </>
    );
}
