import { useEffect, useRef, useState } from "react";
import Modal from "./Modal";
import { useMediaQuery } from "../../../lib/useMediaQuery";

type Props = {
    label: string;
    items: string[];
    checked: string[];
    onChange: (items: string[]) => void;
}

export default function MultiSelectDropdown({ label, items, checked, onChange }: Props) {
    const [open, setOpen] = useState(false);
    const [query, setQuery] = useState("");
    const ref = useRef<HTMLDivElement>(null);
    // Tablet/mobile: the choices open in a centered modal (the inline dropdown
    // is cramped inside the full-screen filter overlay). Desktop keeps the
    // anchored dropdown panel.
    const asModal = useMediaQuery('(max-width: 1023px)');

    // Desktop only: close when clicking outside the anchored panel. In modal
    // mode the backdrop/✕/Escape handle closing, so skip the doc listener.
    useEffect(() => {
        if (asModal) return;
        const onDoc = (e: MouseEvent) => {
            if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
        };
        document.addEventListener("mousedown", onDoc);
        return () => document.removeEventListener("mousedown", onDoc);
    }, [asModal]);

    const toggle = (item: string) =>
        onChange(checked.includes(item) ? checked.filter(i => i !== item) : [...checked, item]);

    const filtered = items.filter(i => i.toLowerCase().includes(query.toLowerCase()));

    const choices = (
        <>
            <input
                className="input msd__search"
                placeholder={`Search ${label.toLowerCase()}…`}
                value={query}
                onChange={e => setQuery(e.target.value)}
            />
            {checked.length > 0 && (
                <button type="button" className="msd__clear" onClick={() => onChange([])}>
                    Clear {checked.length} selected
                </button>
            )}
            <div className="msd__list">
                {filtered.map(item => (
                    <label key={item} className="msd__item">
                        <input
                            type="checkbox"
                            checked={checked.includes(item)}
                            onChange={() => toggle(item)}
                        />
                        {item}
                    </label>
                ))}
                {filtered.length === 0 && (
                    <div className="est-note" style={{ padding: 'var(--space-5)' }}>No matches</div>
                )}
            </div>
        </>
    );

    return (
        <div className="msd" ref={ref}>
            <button type="button" className="btn btn--outline msd__toggle" onClick={() => setOpen(o => !o)}>
                {label}{checked.length > 0 ? ` (${checked.length})` : ''} ▾
            </button>
            {open && (asModal ? (
                <Modal title={label} onClose={() => setOpen(false)}>
                    <div className="msd__modal">{choices}</div>
                </Modal>
            ) : (
                <div className="msd__panel">{choices}</div>
            ))}
        </div>
    );
}
