import { useEffect, useState } from "react";

type Props = {
    items: string[];
    checked: string[];
    onChange: (items: string[]) => void;
}

export default function CheckBoxButtons({ items, checked, onChange }: Props) {
    const [checkedItems, setCheckedItems] = useState(checked);

    useEffect(() => {
        setCheckedItems(checked)
    }, [checked]);

    const handleToggle = (value: string) => {
        const updatedChecked = checkedItems?.includes(value)
            ? checkedItems.filter(item => item !== value)
            : [...checkedItems, value]

        setCheckedItems(updatedChecked);
        onChange(updatedChecked);
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
