import { useEffect, useRef, useState } from "react";

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

    // close when clicking outside
    useEffect(() => {
        const onDoc = (e: MouseEvent) => {
            if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
        };
        document.addEventListener("mousedown", onDoc);
        return () => document.removeEventListener("mousedown", onDoc);
    }, []);

    const toggle = (item: string) =>
        onChange(checked.includes(item) ? checked.filter(i => i !== item) : [...checked, item]);

    const filtered = items.filter(i => i.toLowerCase().includes(query.toLowerCase()));

    return (
        <div className="msd" ref={ref}>
            <button type="button" className="btn btn--outline msd__toggle" onClick={() => setOpen(o => !o)}>
                {label}{checked.length > 0 ? ` (${checked.length})` : ''} ▾
            </button>
            {open && (
                <div className="msd__panel">
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
                            <div className="est-note" style={{ padding: '0.4rem' }}>No matches</div>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
