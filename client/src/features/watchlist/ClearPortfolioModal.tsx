import { useState } from "react";
import Modal from "../../app/shared/components/Modal";
import { useClearOwnedMutation } from "./watchlistApi";

// Clear-portfolio flow: a first modal explains what gets deleted; an explicit
// "are you sure" stage does the irreversible part.
export default function ClearPortfolioModal({ copies, onClose }: { copies: number; onClose: () => void }) {
    const [clearOwned, { isLoading }] = useClearOwnedMutation();
    const [confirming, setConfirming] = useState(false);
    const n = `${copies} cop${copies === 1 ? 'y' : 'ies'}`;

    const wipe = async () => {
        try {
            await clearOwned().unwrap();
            onClose();
        } catch { /* keep the modal open to retry */ }
    };

    return confirming ? (
        <Modal title="Are you sure?" onClose={onClose}>
            <p>
                This permanently deletes all <strong>{n}</strong> in your portfolio,
                including per-copy prices, dates, and notes. There is no undo.
            </p>
            <div className="modal__actions">
                <button className="btn btn--outline btn--danger" disabled={isLoading} onClick={wipe}>
                    {isLoading ? 'Deleting…' : 'Yes, delete everything'}
                </button>
                <button className="btn btn--outline" onClick={onClose}>Cancel</button>
            </div>
        </Modal>
    ) : (
        <Modal title="Clear portfolio" onClose={onClose}>
            <p>
                Remove every position from your portfolio ({n} across all games)?
                Your watchlist and alerts are not affected.
            </p>
            <p className="est-note">Tip: Export a CSV first if you might want this collection back.</p>
            <div className="modal__actions">
                <button className="btn btn--outline btn--danger" onClick={() => setConfirming(true)}>
                    Clear portfolio
                </button>
                <button className="btn btn--outline" onClick={onClose}>Cancel</button>
            </div>
        </Modal>
    );
}
