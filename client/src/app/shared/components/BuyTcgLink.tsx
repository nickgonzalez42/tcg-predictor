import { tcgBuyUrl } from "../../../lib/affiliate";

// Affiliated TCGplayer link for card rows — every outbound TCGplayer link on
// the site goes through the Impact deep link (2026-10-09, user decision).
// Renders nothing while the affiliate base is unset. compact = small text
// link for dense tables; default = outline button for action cells.
export default function BuyTcgLink({ productId, compact }: {
    productId: number;
    compact?: boolean;
}) {
    const url = tcgBuyUrl(productId);
    if (!url) return null;
    const shared = {
        href: url,
        target: "_blank",
        rel: "sponsored noopener",
        title: "Buy on TCGplayer (affiliate link — CardStock may earn a commission)",
        onClick: (e: React.MouseEvent) => e.stopPropagation(),
    } as const;
    return compact
        ? <a className="buy-tcg-inline mono" {...shared}>TCG ↗</a>
        : <a className="btn btn--outline" {...shared}>Buy ↗</a>;
}
