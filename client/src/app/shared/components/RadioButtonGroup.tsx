import type { ChangeEvent } from "react";

type Props = {
    options: { value: string, label: string }[]
    onChange: (event: ChangeEvent<HTMLInputElement>) => void
    selectedValue: string
}

export default function RadioButtonGroup({ options, onChange, selectedValue }: Props) {
    return (
        <div className="radio-list">
            {options.map(({ value, label }) => (
                <label key={label}>
                    <input
                        type="radio"
                        value={value}
                        checked={selectedValue === value}
                        onChange={onChange}
                    />
                    {label}
                </label>
            ))}
        </div>
    )
}
