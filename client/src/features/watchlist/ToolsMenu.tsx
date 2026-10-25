import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

// Portfolio "Tools" dropdown (2026-10-25). The menu is portaled to <body>
// and positioned from the button's rect, because the header's actions row
// scrolls sideways on phones (overflow clips anything positioned inside
// it). Outside click, Escape and a selection close it.
export type ToolItem = { label: string; hint?: string; onSelect: () => void };

const MENU_W = 272;

export default function ToolsMenu({ label = 'Tools', items }: { label?: string; items: ToolItem[] }) {
    const btnRef = useRef<HTMLButtonElement>(null);
    const menuRef = useRef<HTMLDivElement>(null);
    const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
    const open = pos !== null;

    const place = () => {
        const r = btnRef.current?.getBoundingClientRect();
        if (!r) return null;
        return { top: r.bottom + 6, left: Math.max(8, Math.min(r.left, window.innerWidth - MENU_W - 8)) };
    };

    useEffect(() => {
        if (!open) return;
        const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setPos(null); };
        const onDown = (e: Event) => {
            const t = e.target as Node;
            if (btnRef.current?.contains(t) || menuRef.current?.contains(t)) return;
            setPos(null);
        };
        const onMove = () => setPos(place());
        window.addEventListener('keydown', onKey);
        document.addEventListener('mousedown', onDown);
        document.addEventListener('touchstart', onDown);
        window.addEventListener('resize', onMove);
        window.addEventListener('scroll', onMove, true);
        return () => {
            window.removeEventListener('keydown', onKey);
            document.removeEventListener('mousedown', onDown);
            document.removeEventListener('touchstart', onDown);
            window.removeEventListener('resize', onMove);
            window.removeEventListener('scroll', onMove, true);
        };
    }, [open]);

    return (
        <>
            <button ref={btnRef} type="button"
                className={`btn btn--outline tools-btn${open ? ' btn--active' : ''}`}
                aria-haspopup="menu" aria-expanded={open}
                onClick={() => setPos(open ? null : place())}>
                {label}<span className="tools-btn__caret" aria-hidden="true">▾</span>
            </button>
            {open && createPortal(
                <div ref={menuRef} className="tools-menu" role="menu" aria-label={label}
                    style={{ top: pos.top, left: pos.left, width: MENU_W }}>
                    {items.map(it => (
                        <button key={it.label} type="button" role="menuitem" className="tools-menu__item"
                            onClick={() => { setPos(null); it.onSelect(); }}>
                            <span className="tools-menu__label">{it.label}</span>
                            {it.hint && <span className="tools-menu__hint">{it.hint}</span>}
                        </button>
                    ))}
                </div>,
                document.body
            )}
        </>
    );
}
