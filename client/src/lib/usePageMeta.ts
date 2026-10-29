import { useEffect } from "react";

const SITE = "CardStock";
const ORIGIN = "https://cardstock.guide";
const DEFAULT_TITLE = `${SITE}: Trading Card Price Predictions`;
const DEFAULT_DESCRIPTION =
    "AI price predictions, graded price history, and portfolio tracking for " +
    "Magic, Pokémon, One Piece, Yu-Gi-Oh!, Lorcana, Digimon, and Gundam cards.";

// Social/article tags a page can set on top of its title and description
// (2026-10-29): reports are the site's original editorial, so each one needs
// its own share card and article metadata rather than the site-wide defaults
// in index.html.
export type PageMeta = {
    /** og:type — "article" for a report or guide, "website" otherwise. */
    type?: string;
    /** Path or absolute URL this page should be indexed under. */
    canonical?: string;
    /** yyyy-MM-dd (or ISO) publication date -> article:published_time. */
    publishedAt?: string;
    /** Article grouping, e.g. "Market report" -> article:section. */
    section?: string;
};

// A tag this hook owns, so cleanup can remove exactly what it added and the
// static tags from index.html are never deleted.
const MARK = "data-page-meta";

function set(selector: string, make: () => HTMLElement, attr: string, value?: string) {
    let el = document.head.querySelector<HTMLElement>(selector);
    if (!value) {
        if (el?.hasAttribute(MARK)) el.remove();
        return;
    }
    if (!el) {
        el = make();
        el.setAttribute(MARK, "");
        document.head.appendChild(el);
    }
    el.setAttribute(attr, value);
}

function meta(key: "name" | "property", name: string, value?: string) {
    set(`meta[${key}="${name}"]`, () => {
        const el = document.createElement("meta");
        el.setAttribute(key, name);
        return el;
    }, "content", value);
}

// Per-page <title>, meta description and share/article tags for an SPA:
// crawlers that execute JS (Google) and the browser tab both see
// page-specific text; everyone else falls back to the static tags in
// index.html. Reports and guides are also prerendered server-side, so their
// tags exist without JavaScript too.
export function usePageMeta(title?: string, description?: string, extra?: PageMeta) {
    const { type, canonical, publishedAt, section } = extra ?? {};
    useEffect(() => {
        const full = title ? `${title} · ${SITE}` : DEFAULT_TITLE;
        const desc = description ?? DEFAULT_DESCRIPTION;
        document.title = full;
        const descTag = document.querySelector('meta[name="description"]');
        if (descTag) descTag.setAttribute('content', desc);
        // Share cards: title/description track the page, the image stays the
        // site card (reports have no art of their own).
        meta("property", "og:title", full);
        meta("property", "og:description", desc);
        meta("name", "twitter:title", full);
        meta("name", "twitter:description", desc);
        if (type) meta("property", "og:type", type);
        const url = canonical && (canonical.startsWith("http") ? canonical : ORIGIN + canonical);
        if (url) {
            meta("property", "og:url", url);
            set('link[rel="canonical"]', () => {
                const el = document.createElement("link");
                el.setAttribute("rel", "canonical");
                return el;
            }, "href", url);
        }
        meta("property", "article:published_time", publishedAt || undefined);
        meta("property", "article:section", section || undefined);
        return () => {
            document.title = DEFAULT_TITLE;
            if (descTag) descTag.setAttribute('content', DEFAULT_DESCRIPTION);
            // Hand the site-wide tags back to whatever index.html declared:
            // og:type/og:url/og:title/og:description exist there statically,
            // so restore rather than remove.
            meta("property", "og:title", DEFAULT_TITLE);
            meta("property", "og:description", DEFAULT_DESCRIPTION);
            meta("name", "twitter:title", DEFAULT_TITLE);
            meta("name", "twitter:description", DEFAULT_DESCRIPTION);
            if (type) meta("property", "og:type", "website");
            if (url) meta("property", "og:url", ORIGIN + "/");
            meta("property", "article:published_time", undefined);
            meta("property", "article:section", undefined);
            if (url) {
                const link = document.head.querySelector('link[rel="canonical"]');
                if (link?.hasAttribute(MARK)) link.remove();
            }
        };
    }, [title, description, type, canonical, publishedAt, section]);
}
