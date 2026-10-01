// Pack-pull vs individual-purchase toggle, shared by every add/edit flow so
// the two options always read the same. Pack is the add default: most copies
// come out of sealed product and carry no individual cost basis.
export default function SourceToggle({ value, onChange, disabled }: {
    value: 'pack' | 'paid';
    onChange: (v: 'pack' | 'paid') => void;
    disabled?: boolean;
}) {
    const chip = (v: 'pack' | 'paid', label: string, title: string) => (
        <button type="button" disabled={disabled}
            className={`btn btn--outline btn--sm${value === v ? ' btn--active' : ''}`}
            onClick={() => onChange(v)} aria-pressed={value === v} title={title}>
            {label}
        </button>
    );
    return (
        <div className="src-toggle" role="group" aria-label="How it was acquired">
            {chip('pack', 'Pack pull', 'Opened in a sealed pack — no individual price paid')}
            {chip('paid', 'Paid', 'Bought individually — tracks price paid and P/L')}
        </div>
    );
}
