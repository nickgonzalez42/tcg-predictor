import { useRef, useState } from "react";
import Modal from "../../app/shared/components/Modal";
import { useImportOwnedMutation, type ImportRow, type ImportCandidate, type ImportResult } from "./watchlistApi";
import { GAMES } from "../../lib/games";

const isGame = (s: string) =>
    GAMES.some(g => g.value === (s ?? '').trim().toLowerCase());

// One unresolved name that matched several cards, awaiting the user's pick.
type Ambig = { key: number; row: ImportRow; label: string; candidates: ImportCandidate[] };

// One CSV line -> fields, honoring double-quoted fields (the exporter quotes
// card names, which may hold commas).
function splitCsv(line: string): string[] {
    const out: string[] = [];
    let cur = '', quoted = false;
    for (let i = 0; i < line.length; i++) {
        const ch = line[i];
        if (quoted) {
            if (ch === '"') {
                if (line[i + 1] === '"') { cur += '"'; i++; }
                else quoted = false;
            } else cur += ch;
        }
        else if (ch === '"') quoted = true;
        else if (ch === ',') { out.push(cur); cur = ''; }
        else cur += ch;
    }
    out.push(cur);
    return out;
}

// Parse the import CSV. Headerless files use the documented column order
// (game, card, condition, quantity, pricePaid, acquiredDate). A header row
// switches to name-based mapping — any column order, unknown columns ignored —
// which is also how the optional tcgplayerId column rides in (product ids ARE
// TCGplayer product ids, so it's an alternative to `card`). `card` takes a
// numeric id or a card name; names resolve server-side with a picker when
// several cards match.
function parseRows(text: string): ImportRow[] {
    const lines = text.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
    if (!lines.length) return [];

    let idx = { game: 0, card: 1, condition: 2, quantity: 3, pricePaid: 4, acquiredDate: 5, tcgplayerId: -1 };
    const first = splitCsv(lines[0]);
    if (!isGame(first[0])) {
        const norm = (s: string) => s.toLowerCase().replace(/[^a-z]/g, '');
        const heads = first.map(norm);
        const find = (...names: string[]) => heads.findIndex(h => names.includes(h));
        idx = {
            game: find('game'),
            card: find('card', 'productid', 'id'),
            condition: find('condition', 'grade'),
            quantity: find('quantity', 'qty', 'count'),
            pricePaid: find('pricepaid', 'price', 'paid'),
            acquiredDate: find('acquireddate', 'acquired', 'date'),
            tcgplayerId: find('tcgplayerid', 'tcgplayer'),
        };
        lines.shift();
    }

    return lines.map(line => {
        const cells = splitCsv(line);
        const get = (i: number) => (i >= 0 ? (cells[i] ?? '').trim() : '');

        const cardRaw = get(idx.card);
        const tcgRaw = get(idx.tcgplayerId);
        const cond = get(idx.condition).toLowerCase();
        const qty = Number(get(idx.quantity));
        const priceRaw = get(idx.pricePaid);
        const priceNum = Number(priceRaw.replace(/[$,]/g, ''));

        // Identify the card: numeric card cell, else the tcgplayerId column
        // (same id space), else treat the card cell as a name.
        const isId = /^\d+$/.test(cardRaw);
        const tcgId = /^\d+$/.test(tcgRaw) ? Number(tcgRaw) : undefined;

        return {
            game: get(idx.game).toLowerCase(),
            productId: isId ? Number(cardRaw) : tcgId,
            name: isId || (tcgId != null && !cardRaw) ? undefined : (cardRaw || undefined),
            grade: cond === '' || cond === 'ungraded' ? undefined : cond,   // server validates
            quantity: Number.isInteger(qty) && qty > 0 ? qty : 1,
            purchasePrice: priceRaw !== '' && isFinite(priceNum) && priceNum >= 0 ? priceNum : undefined,
            acquiredAt: get(idx.acquiredDate) || undefined,
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
                            <tr><td>tcgplayerId</td><td>optional; the TCGplayer product id, as an alternative to <span className="mono">card</span> (they share the same ids). Needs a header row.</td></tr>
                        </tbody>
                    </table>
                    <p className="est-note">
                        With a header row the columns can come in any order and extra columns
                        are ignored, so a file exported from here re-imports as-is.
                    </p>
                    <p style={{ marginTop: "10px" }}>
                        Example. The header row is optional for the first six columns in this
                        order; include one to reorder columns or to use tcgplayerId:
                    </p>
                    <pre className="import-example">{`game,card,condition,quantity,pricePaid,acquiredDate,tcgplayerId
pokemon,89163,psa10,1,850,2024-03-15,
onepiece,629181,ungraded,3,,,
yugioh,Dark Magician,grade9,2,120.50,2023-11-02,
pokemon,,ungraded,1,,,106999`}</pre>
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
