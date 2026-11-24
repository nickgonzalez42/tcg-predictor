// "#rrggbb" -> rgba() at the given opacity (non-hex values pass through).
// Shared by the value chart and the Value outlook chart.
export const fade = (hex: string, alpha: number) => {
    const m = /^#?([\da-f]{6})$/i.exec(hex.trim());
    if (!m) return hex;
    const n = parseInt(m[1], 16);
    return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
};
