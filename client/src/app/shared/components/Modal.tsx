import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";

// Minimal centered modal: dimmed backdrop, ✕ / backdrop click / Escape all
// close it, tall content scrolls inside, page behind is scroll-locked.
export default function Modal({ title, onClose, children }: {
    title: string; onClose: () => void; children: ReactNode;
}) {
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
        window.addEventListener('keydown', onKey);
        document.body.classList.add('modal-open');
        return () => {
            window.removeEventListener('keydown', onKey);
            document.body.classList.remove('modal-open');
        };
    }, [onClose]);

    return createPortal(
        <div className="modal__backdrop" onClick={onClose}>
            <div className="modal" role="dialog" aria-modal="true" aria-label={title}
                onClick={e => e.stopPropagation()}>
                <div className="modal__head">
                    <h4 className="mono modal__title">{title}</h4>
                    <button className="btn btn--outline" onClick={onClose} title="Close">✕</button>
                </div>
                <div className="modal__body">{children}</div>
            </div>
        </div>,
        document.body
    );
}
