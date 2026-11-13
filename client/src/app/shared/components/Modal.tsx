import { useEffect, useEffectEvent, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

// Everything Tab can land on; tabbables() drops the disabled and the hidden.
const FOCUSABLE = 'a[href], area[href], button, input:not([type="hidden"]), select, textarea, '
    + 'iframe, summary, [contenteditable="true"], [contenteditable=""], [tabindex]';

function tabbables(root: HTMLElement): HTMLElement[] {
    return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(el =>
        el.tabIndex >= 0 && !el.matches(':disabled') && el.getClientRects().length > 0);
}

// Dialogs open right now, innermost last. Only the top one answers Tab and
// Escape, and the page stays scroll-locked until the last one closes.
const openDialogs: HTMLElement[] = [];

// Minimal centered modal: dimmed backdrop, ✕ / backdrop click / Escape all
// close it, tall content scrolls inside, page behind is scroll-locked.
// Keyboard (2026-11-13): focus moves into the dialog when it opens (a field
// with autoFocus keeps it), Tab and Shift+Tab cycle inside it, and closing
// hands focus back to whatever opened it.
export default function Modal({ title, onClose, children, className }: {
    title: string; onClose: () => void; children: ReactNode; className?: string;
}) {
    const titleId = useId();
    const dialogRef = useRef<HTMLDivElement>(null);
    // Read while rendering, before an autoFocus field inside takes focus.
    const [opener] = useState(() => document.activeElement);
    // Callers pass inline arrows; the listeners below always call the latest.
    const close = useEffectEvent(() => onClose());

    useEffect(() => {
        const dialog = dialogRef.current;
        if (!dialog) return;
        openDialogs.push(dialog);
        document.body.classList.add('modal-open');
        if (!dialog.contains(document.activeElement)) dialog.focus({ preventScroll: true });

        const isTop = () => openDialogs[openDialogs.length - 1] === dialog;
        const onKey = (e: KeyboardEvent) => {
            if (!isTop()) return;
            if (e.key === 'Escape' && !e.isComposing && !e.defaultPrevented) {
                close();
                return;
            }
            if (e.key !== 'Tab') return;
            const items = tabbables(dialog);
            const first = items[0], last = items[items.length - 1];
            const active = document.activeElement;
            const inside = !!active && active !== dialog && dialog.contains(active);
            if (!first) {
                e.preventDefault();
            } else if (e.shiftKey && (!inside || active === first)) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && (!inside || active === last)) {
                e.preventDefault();
                first.focus();
            }
        };
        // Focus that lands outside the dialog (a click on a toast, a screen
        // reader jumping to the page) comes straight back to it.
        const onFocusIn = (e: FocusEvent) => {
            if (isTop() && e.target instanceof Node && !dialog.contains(e.target))
                dialog.focus({ preventScroll: true });
        };
        document.addEventListener('keydown', onKey);
        document.addEventListener('focusin', onFocusIn);
        return () => {
            document.removeEventListener('keydown', onKey);
            document.removeEventListener('focusin', onFocusIn);
            openDialogs.splice(openDialogs.indexOf(dialog), 1);
            if (openDialogs.length === 0) document.body.classList.remove('modal-open');
            // Back to the opener on a real close (React's development double
            // run leaves the dialog on the page), and only when focus went down
            // with the dialog: focus that moved on, such as the next dialog's
            // autoFocus field, stays put, and so does an opener that closing
            // took off the page.
            const active = document.activeElement;
            if (!dialog.isConnected && (!active || active === document.body)
                && opener instanceof HTMLElement && opener.isConnected)
                opener.focus({ preventScroll: true });
        };
    }, [opener]);

    return createPortal(
        <div className="modal__backdrop" onClick={onClose}>
            <div ref={dialogRef} className={`modal${className ? ` ${className}` : ''}`} role="dialog"
                aria-modal="true" aria-labelledby={titleId} tabIndex={-1}
                onClick={e => e.stopPropagation()}>
                <div className="modal__head">
                    <h4 id={titleId} className="mono modal__title">{title}</h4>
                    <button type="button" className="btn btn--outline" onClick={onClose}
                        title="Close" aria-label="Close">✕</button>
                </div>
                <div className="modal__body">{children}</div>
            </div>
        </div>,
        document.body
    );
}
