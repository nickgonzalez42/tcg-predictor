import { useState } from "react";
import { Link } from "react-router-dom";
import { useAddToWatchlistMutation, useRemoveOwnedCopyMutation } from "./watchlistApi";
import { OwnedCopyRow } from "./OwnedCopyRow";
import { packPrice, tierLabel } from "./grades";
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

    // P/L compares only PAID copies — pack pulls have no recorded cost, so a
    // $0 basis would read as pure profit.
    const paidCopies = copies.filter(c => (c.source ?? 'paid') === 'paid');
    const paid = paidCopies.length ? paidCopies.reduce((s, c) => s + (c.purchasePrice ?? 0), 0) : null;
    const pl = paid != null && card.price != null ? card.price * paidCopies.length - paid : null;
    // Pack pulls LIST at booster MSRP (2026-10-02, user request) in the pack
    // accent color — a visual cost anchor only, never counted in P/L.
    const packPulls = copies.length - paidCopies.length;
    const packCost = packPulls * packPrice(gameKey(card.game));

    // Rows are stacks of IDENTICAL copies (2026-10-10): "+" clones the stack's
    // signature so the new copy lands in this row, "−" drops the newest copy.
    const proto = copies[0];
    const addOne = () => addCopy({
        game: gameKey(card.game), productId: card.id, kind: 'owned', grade,
        printing: proto?.printing ?? '',
        source: proto?.source ?? 'pack',
        autoPrice: proto?.autoPrice,
        purchasePrice: proto && proto.source === 'paid' && !proto.autoPrice ? proto.purchasePrice : undefined,
        acquiredAt: proto?.acquiredAt,
        note: proto?.note,
    });
    const removeOne = () => {
        const target = copies[copies.length - 1];
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
                    {/* Phones hide the Condition column; the tier moves here. */}
                    <div className="screener__cond-inline">
                        <span className="owned-condition">{tierLabel(card.ownedGrade)}</span>
                    </div>
                </td>
                <td><span className="owned-condition">{tierLabel(card.ownedGrade)}</span></td>
                <td className="screener__num">{qty}</td>
                <td className="screener__num">
                    {paid != null ? currencyFormat(paid) : packCost <= 0 ? '—' : null}
                    {packCost > 0 && (
                        <div className="pack-paid mono"
                            title={`${packPulls} pack pull${packPulls === 1 ? '' : 's'} listed at booster price — not counted in P/L`}>
                            {paid != null ? '+' : ''}{currencyFormat(packCost)} pack
                        </div>
                    )}
                </td>
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
                <Modal title={`${card.name} · ${tierLabel(card.ownedGrade)}`}
                    onClose={() => setExpanded(false)}>
                    <p className="est-note" style={{ marginTop: 0 }}>
                        Identical copies stack into one row. Edit any copy here — a copy that
                        differs in any way (condition, paid price, date, note) gets its own row.
                    </p>
                    <div className="owned-copies owned-copies--modal">
                        {copies.map(copy => (
                            <OwnedCopyRow key={copy.id} copy={copy}
                                onDone={() => setExpanded(false)} />
                        ))}
                    </div>
                </Modal>
            )}
        </>
    );
}
