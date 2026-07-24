import { useState } from "react";
import type { OwnedCopy } from "../../app/models/card";
import {
    useUpdateOwnedCopyMutation,
    useRemoveOwnedCopyMutation,
} from "./watchlistApi";
import { PRICE_TIER_OPTIONS } from "./grades";

// Per-copy grade select: the PriceCharting tiers ('' = Ungraded, i.e. raw).
const copyGradeOptions = PRICE_TIER_OPTIONS;

// Editable row for one owned copy. Changing the grade moves the copy to another
// condition's card; adding/clearing detail moves it between stack and standalone.
// onDone fires after a successful save; onClose (when given) shows a ✕ that
// closes the editor without touching the copy. Deleting is its own button.
export function OwnedCopyRow({ copy, onDone, onClose }: {
    copy: OwnedCopy; onDone?: () => void; onClose?: () => void;
}) {
    const [update, { isLoading: saving }] = useUpdateOwnedCopyMutation();
    const [remove, { isLoading: removing }] = useRemoveOwnedCopyMutation();

    // Acquired is never empty: it defaults to the copy's added date, and a
    // cleared field saves as null which the server resets to the added date.
    const initialAcquired = (copy.acquiredAt || copy.addedAt).slice(0, 10);

    const [grade, setGrade] = useState(copy.grade ?? '');
    const [auto, setAuto] = useState(copy.autoPrice ?? true);
    const [price, setPrice] = useState(String(copy.purchasePrice ?? 0));
    const [acquired, setAcquired] = useState(initialAcquired);
    const [note, setNote] = useState(copy.note ?? '');

    const dirty =
        grade !== (copy.grade ?? '') ||
        auto !== (copy.autoPrice ?? true) ||
        (!auto && price !== String(copy.purchasePrice ?? 0)) ||
        acquired !== initialAcquired ||
        note !== (copy.note ?? '');

    const save = async () => {
        try {
            await update({
                id: copy.id,
                grade: grade || null,
                autoPrice: auto,
                purchasePrice: auto ? null : (price.trim() === '' ? 0 : Number(price)),
                acquiredAt: acquired || null,   // null -> server resets to added date
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
                        value={auto ? String(copy.purchasePrice ?? 0) : price} disabled={auto}
                        title={auto ? "Auto price: the market price on the acquired date" : undefined}
                        onChange={e => setPrice(e.target.value)} />
                </label>
                <label className="owned-copy__auto" title="Set the paid price automatically from the market price on the acquired date ($0 if no data goes back that far)">
                    Auto price
                    <input type="checkbox" checked={auto} onChange={e => setAuto(e.target.checked)} />
                </label>
                <label>Acquired
                    <input className="input" type="date" value={acquired} max={new Date().toISOString().slice(0, 10)}
                        onChange={e => setAcquired(e.target.value)} />
                </label>
                <label className="owned-copy__note">Note
                    <input className="input" type="text" maxLength={200}
                        placeholder="—" value={note} onChange={e => setNote(e.target.value)} />
                </label>
            </div>
            <div className="owned-copy__actions">
                <button className="btn btn--outline" disabled={!dirty || saving} onClick={save}>Save</button>
                <button className="btn btn--outline btn--danger" disabled={removing}
                    title="Delete this copy from your portfolio"
                    onClick={() => remove({ id: copy.id })}>
                    Delete
                </button>
                {onClose && (
                    <button className="btn btn--outline" title="Close the editor" onClick={onClose}>✕</button>
                )}
            </div>
        </div>
    );
}
