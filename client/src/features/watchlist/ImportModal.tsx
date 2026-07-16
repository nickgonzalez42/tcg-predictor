import { useRef, useState } from "react";
import Modal from "../../app/shared/components/Modal";
import { useImportOwnedMutation, type ImportRow, type ImportCandidate, type ImportResult } from "./watchlistApi";
import { GAMES } from "../../lib/games";

const isGame = (s: string) =>
    GAMES.some(g => g.value === (s ?? '').trim().toLowerCase());

// One unresolved name that matched several cards, awaiting the user's pick.
type Ambig = { key: number; row: ImportRow; label: string; candidates: ImportCandidate[] };

// Parse a CSV of (game, card, condition, quantity, pricePaid, acquiredDate).
// `card` is either the numeric product id from a card's URL or the card's name;
// names are resolved server-side, with a picker for any that match several cards.
function parseRows(text: string): ImportRow[] {
    const lines = text.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
    if (lines.length && !isGame(lines[0].split(',')[0])) lines.shift();   // drop a header row

    return lines.map(line => {
        const [gameRaw = '', cardRaw = '', condRaw = '', qtyRaw = '', priceRaw = '', dateRaw = ''] =
            line.split(',').map(c => c.trim());
        const cond = condRaw.toLowerCase();
        const grade = cond === '' || cond === 'ungraded' ? undefined : cond;   // server validates
        const qty = Number(qtyRaw);
        const priceNum = Number(priceRaw.replace(/[$,]/g, ''));
        const isId = /^\d+$/.test(cardRaw);

        return {
            game: gameRaw.toLowerCase(),
            productId: isId ? Number(cardRaw) : undefined,
            name: isId ? undefined : (cardRaw || undefined),
            grade,
            quantity: Number.isInteger(qty) && qty > 0 ? qty : 1,
            purchasePrice: priceRaw !== '' && isFinite(priceNum) && priceNum >= 0 ? priceNum : undefined,
            acquiredAt: dateRaw || undefined,
        };
    });
}

export default function ImportModal({ onClose }: { onClose: () => void }) {
    const [importOwned] = useImportOwnedMutation();
    const [busy, setBusy] = useState(false);
    const [started, setStarted] = useState(false);
    const [fatal, setFatal] = useState<string | null>(null);
    const [added, setAdded] = useState(0);
    const [errors, setErrors] = useState<string[]>([]);
    const [ambiguous, setAmbiguous] = useState<Ambig[]>([]);
    const [picks, setPicks] = useState<Record<number, number>>({});
    const keyRef = useRef(0);

    const onFile = async (file: File) => {
        const rows = parseRows(await file.text());
        setStarted(true);
        setFatal(null);
        setBusy(true);
        try {
            const res = await importOwned({ rows }).unwrap();
            applyFirst(rows, res);
        } catch {
            setFatal("Import failed. Check the file format and try again.");
        }
        setBusy(false);
    };

    // First pass: tally imports, collect errors, and surface ambiguous names.
    const applyFirst = (rows: ImportRow[], res: ImportResult) => {
        setAdded(a => a + res.added);
        const errs: string[] = [];
        const ambig: Ambig[] = [];
        for (const r of res.rows) {
            const row = rows[r.index];
            if (r.status === 'error') errs.push(r.message ?? 'Row could not be imported.');
            else if (r.status === 'ambiguous' && r.candidates?.length) {
                ambig.push({ key: keyRef.current++, row, label: row?.name ?? '(unnamed)', candidates: r.candidates });
            }
        }
        if (errs.length) setErrors(e => [...e, ...errs]);
        setAmbiguous(ambig);
    };

    // Resubmit the ambiguous rows the user has picked a card for; keep the rest.
    const importResolved = async () => {
        const resolved = ambiguous.filter(a => picks[a.key] != null);
        if (!resolved.length) return;
        const unresolved = ambiguous.filter(a => picks[a.key] == null);
        const rows: ImportRow[] = resolved.map(a => ({ ...a.row, productId: picks[a.key], name: undefined }));

        setBusy(true);
        try {
            const res = await importOwned({ rows }).unwrap();
            setAdded(x => x + res.added);
            const errs = res.rows.filter(r => r.status === 'error').map(r => r.message ?? 'Row could not be imported.');
            if (errs.length) setErrors(e => [...e, ...errs]);
        } catch {
            setFatal("Import failed. Try again.");
        }
        setAmbiguous(unresolved);
        setPicks({});
        setBusy(false);
    };

    const selectedCount = ambiguous.filter(a => picks[a.key] != null).length;

    return (
        <Modal title="Import a CSV" onClose={onClose}>
            {!started && (
                <>
                    <p>
                        Add owned cards in bulk from a spreadsheet. Make a CSV with one row per
                        card and these columns:
                    </p>
                    <table className="detail-table">
                        <tbody>
                            <tr><td>game</td><td>{GAMES.map(g => g.value).join(', ')}</td></tr>
                            <tr><td>card</td><td>the card's <strong>name</strong>, or the number from its URL (<span className="mono">/catalog/pokemon/89163</span> is <span className="mono">89163</span>). A name that matches several cards lets you pick.</td></tr>
                            <tr><td>condition</td><td><span className="mono">ungraded</span> (or blank), grade7, grade8, grade9, grade95, psa10, bgs10, cgc10, sgc10</td></tr>
                            <tr><td>quantity</td><td>how many you own at that condition</td></tr>
                            <tr><td>pricePaid</td><td>optional; what you paid per copy. Blank fills in from the market on the acquired date.</td></tr>
                            <tr><td>acquiredDate</td><td>optional; <span className="mono">YYYY-MM-DD</span>. Blank uses today.</td></tr>
                        </tbody>
                    </table>
                    <p style={{ marginTop: "10px" }}>Example (a header row is optional):</p>
                    <pre className="import-example">{`game,card,condition,quantity,pricePaid,acquiredDate
pokemon,89163,psa10,1,850,2024-03-15
onepiece,629181,ungraded,3,,
yugioh,Dark Magician,grade9,2,120.50,2023-11-02`}</pre>
                    <div className="modal__actions">
                        <label className={`btn${busy ? ' btn--disabled' : ''}`}>
                            {busy ? 'Importing…' : 'Choose CSV file'}
                            <input type="file" accept=".csv,text/csv" hidden disabled={busy}
                                onChange={e => { const f = e.target.files?.[0]; if (f) onFile(f); }} />
                        </label>
                    </div>
                </>
            )}

            {started && (
                <>
                    <p><strong>Imported {added} cop{added === 1 ? 'y' : 'ies'}.</strong>
                        {ambiguous.length > 0 && ` ${ambiguous.length} name${ambiguous.length === 1 ? '' : 's'} need a pick below.`}
                        {errors.length > 0 && ` ${errors.length} row${errors.length === 1 ? '' : 's'} skipped.`}</p>

                    {fatal && <p className="import-errors">{fatal}</p>}

                    {ambiguous.map(a => (
                        <div key={a.key} className="import-ambig">
                            <p className="import-ambig__q">
                                "<strong>{a.label}</strong>" matches several {a.row.game} cards. Pick one:
                            </p>
                            <div className="import-cands">
                                {a.candidates.slice(0, 12).map(c => (
                                    <label key={c.productId}
                                        className={`import-cand${picks[a.key] === c.productId ? ' import-cand--on' : ''}`}>
                                        <input type="radio" name={`ambig-${a.key}`}
                                            checked={picks[a.key] === c.productId}
                                            onChange={() => setPicks(p => ({ ...p, [a.key]: c.productId }))} />
                                        {c.imageUrl && (
                                            <img className="import-cand__img" src={c.imageUrl} alt=""
                                                onError={e => { e.currentTarget.style.visibility = 'hidden'; }} />
                                        )}
                                        <span className="import-cand__body">
                                            <span className="import-cand__name">{c.name}</span>
                                            <span className="import-cand__meta">
                                                {[c.setName, c.rarity].filter(Boolean).join(' · ')}
                                                {c.price != null && ` · $${c.price.toFixed(2)}`}
                                            </span>
                                        </span>
                                    </label>
                                ))}
                            </div>
                            {a.candidates.length > 12 && (
                                <p className="est-note">Showing the first 12. If yours isn't here, use its product id instead.</p>
                            )}
                        </div>
                    ))}

                    {errors.length > 0 && (
                        <pre className="import-example import-errors">{errors.join('\n')}</pre>
                    )}

                    <div className="modal__actions">
                        {ambiguous.length > 0 && (
                            <button className={`btn${busy || selectedCount === 0 ? ' btn--disabled' : ''}`}
                                disabled={busy || selectedCount === 0} onClick={importResolved}>
                                {busy ? 'Importing…' : `Import ${selectedCount} selected`}
                            </button>
                        )}
                        <button className="btn btn--outline" onClick={onClose}>Done</button>
                    </div>
                </>
            )}
        </Modal>
    );
}
