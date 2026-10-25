import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import Modal from "../../app/shared/components/Modal";
import { useScanCardMutation, type ScanHit } from "../catalog/catalogApi";
import {
    useAddToWatchlistMutation, useFetchWatchlistQuery, useRemoveOwnedCopyMutation,
} from "../watchlist/watchlistApi";
import { PRICE_TIER_OPTIONS, tierLabel } from "../watchlist/grades";
import { GAMES } from "../../lib/games";
import { currencyFormat, gameKey } from "../../lib/util";
import { usePageMeta } from "../../lib/usePageMeta";
import type { Card } from "../../app/models/card";

// Collection scanner (2026-10-24). The rear camera streams into a stage with
// a card-shaped guide; every SCAN_EVERY ms the pixels under the guide are
// sent to api/cards/scan, which embeds the frame with the same CLIP encoder
// the catalog art was embedded with and returns the closest cards. Because
// recognition is by ART, same-art reprints and variants score alike, so the
// result is always offered as a short list IN A MODAL over the live camera:
// the user picks the exact card, a quantity and a condition, the copies land
// in the collection, the modal closes and the camera is ready for the next
// card (the one just added is not re-offered until another card is shown).
// Frames are processed in memory server-side and never stored.
const CARD_RATIO = 88 / 63;          // portrait trading card
// Calibrated 2026-10-24 on the 256-d index: real cards score 0.71+ (90% of
// synthetic phone shots, 0.88 typical); a wooden desk 0.63, a hand 0.59, a
// card too far away 0.57, an out-of-focus card 0.62. A blank dark frame can
// reach 0.70 against dark cards, so flat frames are dropped BEFORE upload
// (MIN_SPREAD: luminance standard deviation of the crop, 0-255 scale).
const AUTO_MIN = 0.74;               // top score that counts as "a card is in the frame"
const MIN_SPREAD = 12;
// Stillness trigger (user 2026-10-25: "a good way to determine is if the
// camera holds still for a little"). Every tick the guide crop is sampled
// locally; the mean luminance change against the previous tick is the
// motion. A frame is uploaded only after STILL_TICKS consecutive ticks with
// motion <= MOTION_MAX (~a second of holding still) and only ONCE per still
// period — lining a card up or passing it through never uploads anything.
// Closing the modal pauses auto-open for DISMISS_COOLDOWN.
const MOTION_MAX = 6;                // mean |Δluminance| per sampled pixel (0-255); sensor noise ~2
const STILL_TICKS = 2;
const DISMISS_COOLDOWN = 3000;       // ms
const SCAN_EVERY = 500;              // ms between local motion checks
const CROP_W = 448;                  // upload width (the encoder sees 224 anyway)
const CROP_H = Math.round(CROP_W * CARD_RATIO);

type Guide = { x: number; y: number; w: number; h: number; W: number; H: number };
type Entry = { key: number; card: Card; qty: number; grade: string; printing: string };

export default function ScanPage() {
    usePageMeta("Scan cards");
    const videoRef = useRef<HTMLVideoElement>(null);
    const stageRef = useRef<HTMLDivElement>(null);
    const canvasRef = useRef<HTMLCanvasElement | null>(null);
    const streamRef = useRef<MediaStream | null>(null);
    const fileRef = useRef<HTMLInputElement>(null);
    const inFlight = useRef(false);
    // "game:id" of the card just added or dismissed: not re-offered until a
    // different card tops a frame (so the same card doesn't reopen the sheet).
    const suppressRef = useRef<string | null>(null);
    const cooldownRef = useRef(0);                 // auto-open paused until (ms since epoch)
    const prevSampleRef = useRef<Float32Array | null>(null);   // last tick's luminance sample
    const stillRef = useRef(0);                    // consecutive still ticks
    const consumedRef = useRef(false);             // this still period already produced a look

    const [camState, setCamState] = useState<'starting' | 'live' | 'denied' | 'unsupported'>(
        () => (typeof navigator !== 'undefined' && typeof navigator.mediaDevices?.getUserMedia === 'function'
            ? 'starting' : 'unsupported'));
    const [guide, setGuide] = useState<Guide | null>(null);
    const [lock, setLock] = useState('');               // '' = all games
    const [auto, setAuto] = useState(true);
    const [hits, setHits] = useState<ScanHit[] | null>(null);
    const [looking, setLooking] = useState(false);
    const [lastTop, setLastTop] = useState(0);
    const [session, setSession] = useState<Entry[]>([]);
    const [toast, setToast] = useState<string | null>(null);
    const [nextHint, setNextHint] = useState(false);   // "show the next card" after an add
    const [holding, setHolding] = useState(false);     // something is in view but still moving

    const [scan] = useScanCardMutation();
    const { data: tracked } = useFetchWatchlistQuery();
    const [addCopy] = useAddToWatchlistMutation();
    const [removeCopy] = useRemoveOwnedCopyMutation();

    // ----- camera -----
    useEffect(() => {
        let cancelled = false;
        if (typeof navigator.mediaDevices?.getUserMedia !== 'function') return;   // camState started as 'unsupported'
        navigator.mediaDevices.getUserMedia({
            video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } },
            audio: false,
        }).then(s => {
            if (cancelled) { s.getTracks().forEach(t => t.stop()); return; }
            streamRef.current = s;
            const v = videoRef.current;
            if (v) { v.srcObject = s; v.play().catch(() => { /* autoplay policy: user gesture arrives */ }); }
            setCamState('live');
        }).catch(() => { if (!cancelled) setCamState('denied'); });
        return () => {
            cancelled = true;
            streamRef.current?.getTracks().forEach(t => t.stop());
            streamRef.current = null;
        };
    }, []);

    // The guide: a portrait card outline centred on the stage, 78% of its
    // height unless the stage is too narrow for that.
    useLayoutEffect(() => {
        const st = stageRef.current;
        if (!st) return;
        // ResizeObserver reports once on observe(), so the first measurement
        // arrives without a synchronous setState here.
        const ro = new ResizeObserver(() => {
            const W = st.clientWidth, H = st.clientHeight;
            let h = H * 0.78, w = h / CARD_RATIO;
            if (w > W * 0.82) { w = W * 0.82; h = w * CARD_RATIO; }
            setGuide({ x: (W - w) / 2, y: (H - h) / 2, w, h, W, H });
        });
        ro.observe(st);
        return () => ro.disconnect();
    }, []);

    // Draw the video pixels under the guide (object-fit: cover mapping) onto
    // the work canvas and measure them on a 1-in-8 pixel luminance sample:
    // spread (std, 0-255: is anything there?) and motion (mean |Δ| against
    // the previous tick's sample: is it holding still?).
    const draw = useCallback((): { spread: number; motion: number } | null => {
        const v = videoRef.current, st = stageRef.current;
        if (!v || !st || !v.videoWidth || !v.videoHeight) return null;
        const W = st.clientWidth, H = st.clientHeight;
        let h = H * 0.78, w = h / CARD_RATIO;
        if (w > W * 0.82) { w = W * 0.82; h = w * CARD_RATIO; }
        const gx = (W - w) / 2, gy = (H - h) / 2;
        const scale = Math.max(W / v.videoWidth, H / v.videoHeight);
        const offX = (W - v.videoWidth * scale) / 2, offY = (H - v.videoHeight * scale) / 2;
        const c = canvasRef.current ?? (canvasRef.current = document.createElement('canvas'));
        c.width = CROP_W; c.height = CROP_H;
        const ctx = c.getContext('2d');
        if (!ctx) return null;
        ctx.drawImage(v, (gx - offX) / scale, (gy - offY) / scale, w / scale, h / scale, 0, 0, CROP_W, CROP_H);
        const d = ctx.getImageData(0, 0, CROP_W, CROP_H).data;
        const cur = new Float32Array(Math.ceil(d.length / 32));
        let sum = 0, sq = 0;
        for (let i = 0, j = 0; i < d.length; i += 32, j++) {
            const l = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
            cur[j] = l; sum += l; sq += l * l;
        }
        const n = cur.length, mean = sum / n;
        const spread = Math.sqrt(Math.max(0, sq / n - mean * mean));
        const prev = prevSampleRef.current;
        let motion = 999;
        if (prev && prev.length === n) {
            let acc = 0;
            for (let j = 0; j < n; j++) acc += Math.abs(cur[j] - prev[j]);
            motion = acc / n;
        }
        prevSampleRef.current = cur;
        return { spread, motion };
    }, []);

    // The work canvas as a JPEG (call after draw()).
    const encode = useCallback((): Promise<Blob | null> => new Promise(resolve => {
        const c = canvasRef.current;
        if (!c) return resolve(null);
        c.toBlob(b => resolve(b), 'image/jpeg', 0.85);
    }), []);

    // One frame -> candidates. Automatic frames only open the sheet when the
    // top score says a card is really there; a manual shot always does.
    const look = useCallback(async (blob: Blob, manual: boolean) => {
        if (inFlight.current) return;
        inFlight.current = true;
        setLooking(true);
        try {
            const res = await scan({ blob, game: lock || undefined, k: 6 }).unwrap();
            const top = res[0];
            setLastTop(top?.score ?? 0);
            if (!top) {
                if (manual) setToast('No match. Try a straighter, brighter shot.');
                return;
            }
            const key = `${top.card.game}:${top.card.id}`;
            if (!manual) {
                if (top.score < AUTO_MIN) { suppressRef.current = null; setNextHint(false); return; }
                if (suppressRef.current === key || Date.now() < cooldownRef.current) return;
            }
            setNextHint(false);
            setHits(res);
        } catch {
            // rate limit / offline / 503: the next frame simply tries again
        } finally {
            inFlight.current = false;
            setLooking(false);
        }
    }, [scan, lock]);

    // Auto mode: a local motion check every tick; one upload per still period.
    useEffect(() => {
        if (camState !== 'live' || !auto || hits) return;
        const id = setInterval(async () => {
            const m = draw();
            if (!m) return;
            if (m.spread < MIN_SPREAD) {              // nothing in the guide
                stillRef.current = 0; consumedRef.current = false;
                setHolding(false); setLastTop(0);
                return;
            }
            if (m.motion > MOTION_MAX) {              // something there, but moving
                stillRef.current = 0; consumedRef.current = false;
                setHolding(true); setLastTop(0);
                return;
            }
            stillRef.current += 1;
            if (stillRef.current < STILL_TICKS || consumedRef.current) return;
            consumedRef.current = true;               // one look per still period
            setHolding(false);
            const b = await encode();
            if (b) void look(b, false);
        }, SCAN_EVERY);
        return () => clearInterval(id);
    }, [camState, auto, hits, draw, encode, look]);

    useEffect(() => {
        if (!toast) return;
        const id = setTimeout(() => setToast(null), 2600);
        return () => clearTimeout(id);
    }, [toast]);

    const shutter = async () => {
        if (!draw()) return;
        const b = await encode();
        if (b) void look(b, true);
    };

    // ----- collection -----
    const ownedCount = (card: Card) => (tracked ?? []).filter(t =>
        t.kind === 'owned' && t.game === gameKey(card.game) && t.productId === card.id).length;

    // One request per copy (the add endpoint is one-copy). If a request
    // fails part-way, the copies that did land are still recorded (and
    // undoable) and the toast says how many made it.
    const add = async (card: Card, qty: number, grade: string, printing: string, paid: boolean) => {
        const game = gameKey(card.game);
        let done = 0;
        try {
            for (; done < qty; done++) {
                await addCopy({
                    game, productId: card.id, kind: 'owned', grade, printing,
                    source: paid ? 'paid' : 'pack', autoPrice: paid ? true : undefined,
                }).unwrap();
            }
        } catch {
            // fall through with the partial count
        }
        if (done > 0) setSession(s => [{ key: Date.now(), card, qty: done, grade, printing }, ...s]);
        suppressRef.current = `${game}:${card.id}`;
        setHits(null);
        setNextHint(done > 0);
        setToast(done === qty ? `Added ${qty} × ${card.name}`
            : done > 0 ? `Added ${done} of ${qty} × ${card.name} — try the rest again`
            : `Couldn't add ${card.name} — try again`);
    };

    // Undo removes the newest copies that match the entry's signature — the
    // ones this session created (ids come from the refreshed tracked list).
    const undo = async (e: Entry) => {
        const game = gameKey(e.card.game);
        const ids = (tracked ?? [])
            .filter(t => t.kind === 'owned' && t.game === game && t.productId === e.card.id
                && (t.grade ?? '') === e.grade && (t.printing ?? '') === e.printing)
            .sort((a, b) => b.addedAt.localeCompare(a.addedAt))
            .slice(0, e.qty)
            .map(t => t.id);
        for (const id of ids) await removeCopy({ id }).unwrap();
        setSession(s => s.filter(x => x.key !== e.key));
        setToast(`Removed ${ids.length} × ${e.card.name}`);
    };

    const dismiss = () => {
        if (hits?.[0]) suppressRef.current = `${hits[0].card.game}:${hits[0].card.id}`;
        cooldownRef.current = Date.now() + DISMISS_COOLDOWN;
        setHits(null);
    };

    const count = session.reduce((s, e) => s + e.qty, 0);
    const value = session.reduce((s, e) => s + (e.card.price ?? 0) * e.qty, 0);
    const hot = lastTop >= AUTO_MIN;
    const status =
        camState === 'starting' ? 'Starting camera…'
        : camState !== 'live' ? 'Camera unavailable'
        : nextHint ? 'Added · show the next card'
        : holding ? 'Hold still…'
        : looking ? 'Looking…'
        : auto ? (hot ? 'Card detected' : 'Hold a card inside the frame')
        : 'Tap the button to scan';

    return (
        <div className="scan full-span">
            <div className="scan__head">
                <div>
                    <span className="kicker">Collection</span>
                    <h1 className="display scan__title">Scan cards</h1>
                    <p className="scan__sub">
                        Hold a card inside the frame. When it is recognised, pick the exact card,
                        a condition and how many you have — they go straight into your collection.
                    </p>
                </div>
                <div className="scan__meta">
                    <div className="scan__totals">
                        <span className="num scan__count">{count}</span>
                        <span className="mono">added · {currencyFormat(value)}</span>
                    </div>
                    <label className="scan__auto">
                        <input type="checkbox" checked={auto} onChange={e => setAuto(e.target.checked)} />
                        Auto-scan
                    </label>
                </div>
            </div>

            <div className="scan__chips" role="radiogroup" aria-label="Limit to one game">
                <button type="button" role="radio" aria-checked={lock === ''}
                    className={`scan-chip${lock === '' ? ' is-on' : ''}`} onClick={() => setLock('')}>
                    All games
                </button>
                {GAMES.map(g => (
                    <button key={g.value} type="button" role="radio" aria-checked={lock === g.value}
                        className={`scan-chip${lock === g.value ? ' is-on' : ''}`} onClick={() => setLock(g.value)}>
                        {g.label}
                    </button>
                ))}
            </div>

            <div className="scan__body">
                <div className="scan__stage" ref={stageRef}>
                    <video ref={videoRef} className="scan__video" playsInline muted autoPlay />
                    {guide && camState === 'live' && (
                        <div className={`scan__guide${hot ? ' scan__guide--hot' : ''}`}
                            style={{ left: guide.x, top: guide.y, width: guide.w, height: guide.h }} />
                    )}
                    <div className={`scan__status mono${looking ? ' scan__status--busy' : ''}`}>{status}</div>
                    {camState === 'live' && (
                        <button type="button" className="scan__shutter" aria-label="Scan now"
                            title="Scan now" onClick={shutter} disabled={looking} />
                    )}
                    {(camState === 'denied' || camState === 'unsupported') && (
                        <div className="scan__nocam">
                            <p>
                                {camState === 'denied'
                                    ? 'The camera was blocked. Allow it in your browser settings, or use a photo instead.'
                                    : 'This browser can’t open the camera. Use a photo instead.'}
                            </p>
                            <button type="button" className="btn" onClick={() => fileRef.current?.click()}>
                                📷 Take or choose a photo
                            </button>
                        </div>
                    )}
                </div>

                <aside className="scan__side">
                    <SessionList entries={session} onUndo={undo} />
                </aside>
            </div>

            {hits && (
                <Modal title="Which card is it?" onClose={dismiss}>
                    <PickSheet hits={hits} owned={ownedCount} onAdd={add} />
                </Modal>
            )}

            <input ref={fileRef} type="file" accept="image/*" capture="environment" hidden
                onChange={e => { const f = e.target.files?.[0]; if (f) void look(f, true); e.target.value = ''; }} />
            {toast && <div className="scan__toast mono" role="status">{toast}</div>}
        </div>
    );
}

// The modal's body: the candidates, then quantity and condition.
function PickSheet({ hits, owned, onAdd }: {
    hits: ScanHit[];
    owned: (card: Card) => number;
    onAdd: (card: Card, qty: number, grade: string, printing: string, paid: boolean) => Promise<void>;
}) {
    const [sel, setSel] = useState(0);
    const [qty, setQty] = useState(1);
    const [grade, setGrade] = useState('');
    const [printing, setPrinting] = useState('');
    const [paid, setPaid] = useState(false);
    const [busy, setBusy] = useState(false);
    const card = hits[Math.min(sel, hits.length - 1)].card;
    const prints = card.printings && card.printings.length > 1 ? card.printings : null;
    const have = owned(card);

    const submit = async () => {
        setBusy(true);
        try { await onAdd(card, qty, grade, printing, paid); }
        finally { setBusy(false); }
    };

    return (
        <div className="scan-sheet">
            <ul className="scan-cands">
                {hits.map((h, i) => (
                    <li key={`${h.card.game}-${h.card.id}`}>
                        <button type="button" className={`scan-cand${i === sel ? ' is-on' : ''}`}
                            aria-pressed={i === sel}
                            onClick={() => { setSel(i); setPrinting(''); }}>
                            <img src={h.card.pictureUrl} alt="" loading="lazy" />
                            <span className="scan-cand__text">
                                <span className="scan-cand__name">{h.card.name}</span>
                                <span className="mono scan-cand__sub">
                                    {[GAMES.find(g => g.value === gameKey(h.card.game))?.label, h.card.setName,
                                      h.card.cardNumber ? `#${h.card.cardNumber}` : null].filter(Boolean).join(' · ')}
                                </span>
                            </span>
                            <span className="mono scan-cand__side">
                                <span>{h.card.price != null ? currencyFormat(h.card.price) : '—'}</span>
                                <span className="scan-cand__score">{Math.round(h.score * 100)}%</span>
                            </span>
                        </button>
                    </li>
                ))}
            </ul>
            <div className="scan-form">
                <label className="scan-form__qty">Quantity
                    <span className="scan-stepper">
                        <button type="button" className="btn btn--outline btn--circle" aria-label="One fewer"
                            onClick={() => setQty(q => Math.max(1, q - 1))} disabled={qty <= 1}>−</button>
                        <input className="input" type="number" min={1} max={99} inputMode="numeric" value={qty}
                            onChange={e => setQty(Math.min(99, Math.max(1, Number(e.target.value) || 1)))} />
                        <button type="button" className="btn btn--outline btn--circle" aria-label="One more"
                            onClick={() => setQty(q => Math.min(99, q + 1))}>＋</button>
                    </span>
                </label>
                <label>Condition
                    <select className="input" value={grade} onChange={e => setGrade(e.target.value)}>
                        {PRICE_TIER_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                </label>
                {prints && (
                    <label>Printing
                        <select className="input" value={printing} onChange={e => setPrinting(e.target.value)}>
                            {prints.map((p, i) => (
                                <option key={p} value={i === 0 ? '' : p}>{p}</option>
                            ))}
                        </select>
                    </label>
                )}
                <label className="scan-form__paid">
                    <input type="checkbox" checked={paid} onChange={e => setPaid(e.target.checked)} />
                    Bought at market price (counts toward cost)
                </label>
                <div className="scan-form__row">
                    <span className="mono scan-form__own">
                        {have ? `You own ${have} · ${tierLabel(grade)}` : 'Not in your collection yet'}
                    </span>
                    <button type="button" className="btn scan-form__add" onClick={submit} disabled={busy}>
                        {busy ? 'Adding…' : `Add ${qty} to collection`}
                    </button>
                </div>
                <p className="est-note scan-form__note">
                    Not here? Reprints share art — pick the set that matches your card, or find it in
                    the <Link to={`/catalog?search=${encodeURIComponent(card.name)}`}>catalog</Link>.
                </p>
            </div>
        </div>
    );
}

function SessionList({ entries, onUndo }: { entries: Entry[]; onUndo: (e: Entry) => Promise<void> }) {
    return (
        <div className="scan-session">
            <div className="band">
                <span className="band__title">This session</span>
                <span className="mono">{entries.length ? `${entries.length} card${entries.length === 1 ? '' : 's'}` : ''}</span>
            </div>
            {entries.length === 0 ? (
                <p className="est-note scan-session__empty">
                    Nothing scanned yet. Cards you add here also appear in your <Link to="/portfolio">portfolio</Link>.
                </p>
            ) : (
                <ul className="scan-session__list">
                    {entries.map(e => (
                        <li key={e.key} className="scan-session__row">
                            <img src={e.card.pictureUrl} alt="" loading="lazy" />
                            <span className="scan-session__text">
                                <span className="scan-session__name">{e.card.name}</span>
                                <span className="mono scan-session__sub">
                                    {e.qty} × {tierLabel(e.grade)}{e.printing ? ` · ${e.printing}` : ''}
                                    {e.card.price != null ? ` · ${currencyFormat(e.card.price * e.qty)}` : ''}
                                </span>
                            </span>
                            <button type="button" className="btn btn--outline btn--sm" onClick={() => void onUndo(e)}>
                                Undo
                            </button>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}
