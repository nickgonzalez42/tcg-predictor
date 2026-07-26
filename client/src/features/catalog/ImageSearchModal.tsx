import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import Modal from "../../app/shared/components/Modal";
import { useImageSearchMutation } from "./catalogApi";

// Search-by-photo. Pick or shoot a picture of a card; the server embeds it
// and returns the closest matches across every game. The photo is uploaded
// for the search only — nothing is stored — and the local preview object-URL
// is revoked on close.
export default function ImageSearchModal({ onClose }: { onClose: () => void }) {
    const navigate = useNavigate();
    const [search, { data: hits, isLoading, reset }] = useImageSearchMutation();
    const [preview, setPreview] = useState<string | null>(null);
    const fileInput = useRef<HTMLInputElement>(null);

    useEffect(() => () => { if (preview) URL.revokeObjectURL(preview); }, [preview]);

    const onPick = (file: File | undefined) => {
        if (!file) return;
        if (preview) URL.revokeObjectURL(preview);
        setPreview(URL.createObjectURL(file));
        reset();
        search(file);
    };

    return (
        <Modal title="Search by photo" onClose={onClose}>
            <div className="imgsearch">
                <input
                    ref={fileInput}
                    type="file"
                    accept="image/*"
                    capture="environment"
                    style={{ display: 'none' }}
                    onChange={e => onPick(e.target.files?.[0])}
                />
                <button className="btn btn--block" onClick={() => fileInput.current?.click()}>
                    {preview ? 'Try another photo' : '📷 Take or choose a photo'}
                </button>
                {preview && <img className="imgsearch__preview" src={preview} alt="Your photo" />}
                {isLoading && <p className="est-note">Searching…</p>}
                {hits && hits.length === 0 && (
                    <p className="est-note">No close matches — try a straighter, brighter shot.</p>
                )}
                {hits && hits.length > 0 && (
                    <div className="imgsearch__results">
                        {hits.map(h => (
                            <button key={`${h.game}-${h.productId}`} className="imgsearch__hit"
                                onClick={() => { onClose(); navigate(`/catalog/${h.game}/${h.productId}`); }}>
                                <img src={h.image} alt="" loading="lazy" />
                                <span className="imgsearch__hit-name">{h.name}</span>
                                <span className="imgsearch__hit-sub">{h.set}</span>
                                <span className="mono imgsearch__hit-score">
                                    {Math.round(h.score * 100)}% match
                                </span>
                            </button>
                        ))}
                    </div>
                )}
                <p className="est-note imgsearch__privacy">
                    Your photo is used only for this search and never stored.
                </p>
            </div>
        </Modal>
    );
}
