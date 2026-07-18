import { useEffect } from "react";

const SITE = "CardStock";
const DEFAULT_TITLE = `${SITE}: Trading Card Price Predictions`;
const DEFAULT_DESCRIPTION =
    "AI price predictions, graded price history, and portfolio tracking for " +
    "Magic, Pokémon, One Piece, Yu-Gi-Oh!, Lorcana, Digimon, and Gundam cards.";

// Per-page <title> + meta description for an SPA: crawlers that execute JS
// (Google) and the browser tab both see page-specific text; everyone else
// falls back to the static tags in index.html.
export function usePageMeta(title?: string, description?: string) {
    useEffect(() => {
        document.title = title ? `${title} · ${SITE}` : DEFAULT_TITLE;
        const meta = document.querySelector('meta[name="description"]');
        if (meta) meta.setAttribute('content', description ?? DEFAULT_DESCRIPTION);
        return () => {
            document.title = DEFAULT_TITLE;
            if (meta) meta.setAttribute('content', DEFAULT_DESCRIPTION);
        };
    }, [title, description]);
}
