import { useEffect } from "react";

const SITE = "cardstock";
const DEFAULT_DESCRIPTION =
    "AI price forecasts, graded price history, and portfolio tracking for " +
    "Pokémon, One Piece, Yu-Gi-Oh!, Lorcana, Digimon, and Gundam cards.";

// Per-page <title> + meta description for an SPA: crawlers that execute JS
// (Google) and the browser tab both see page-specific text; everyone else
// falls back to the static tags in index.html.
export function usePageMeta(title?: string, description?: string) {
    useEffect(() => {
        document.title = title ? `${title} · ${SITE}` : `${SITE}: The Stock Market for Trading Cards`;
        const meta = document.querySelector('meta[name="description"]');
        if (meta) meta.setAttribute('content', description ?? DEFAULT_DESCRIPTION);
        return () => {
            document.title = `${SITE}: The Stock Market for Trading Cards`;
            if (meta) meta.setAttribute('content', DEFAULT_DESCRIPTION);
        };
    }, [title, description]);
}
