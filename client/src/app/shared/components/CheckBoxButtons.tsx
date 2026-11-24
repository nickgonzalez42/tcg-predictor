type Props = {
    items: string[];
    checked: string[];
    onChange: (items: string[]) => void;
}

export default function CheckBoxButtons({ items, checked, onChange }: Props) {
    // Driven by the parent: both filter panels keep the picks in the store
    // and hand them back as `checked`, so no second copy lives here.
    const checkedItems = checked ?? [];

    const handleToggle = (value: string) => {
        onChange(checkedItems.includes(value)
            ? checkedItems.filter(item => item !== value)
            : [...checkedItems, value]);
    }

    return (
        <div className="check-list">
            {items.map(item => (
                <label key={item}>
                    <input
                        type="checkbox"
                        checked={checkedItems.includes(item)}
                        onChange={() => handleToggle(item)}
                    />
                    {item}
                </label>
            ))}
        </div>
    )
}
