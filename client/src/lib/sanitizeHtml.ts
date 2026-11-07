// Card descriptions are scraped from TCGplayer and carry light formatting HTML
// (<em>, <strong>, <br>, <p>, colored <font>/<span>, list tags). To show that
// formatting without an XSS surface, we re-render through a strict allowlist:
// only known formatting tags survive, ALL attributes are dropped, and script/
// style contents are discarded. Output is built from tag names + escaped text
// only — never copied markup — so it's safe for dangerouslySetInnerHTML.

const ALLOWED = new Set(
    ['B', 'STRONG', 'I', 'EM', 'U', 'BR', 'P', 'SPAN', 'FONT', 'UL', 'OL', 'LI', 'SUB', 'SUP']);
// Market-report bodies (our own pipeline's HTML) additionally carry headings,
// tables, internal card links, and inline SVG bar/line charts.
// IMAGE and TITLE joined for the report hero (2026-10-30): its card art and
// the tooltips naming each card and game bar.
// CLIPPATH (2026-11-07): the hero clips each card's art to its rounded
// corners; the clip's id and the image's clip-path reference are matched
// to the pipeline's own pattern below, nothing else carries an id or a url().
const REPORT_SVG = new Set(
    ['SVG', 'G', 'LINE', 'RECT', 'TEXT', 'POLYLINE', 'CIRCLE', 'IMAGE', 'TITLE', 'CLIPPATH']);
const CLIP_ID = /^hc-[a-f0-9]{8}-\d$/;
// The only off-site URL any report may reference: the card image CDN.
const IMAGE_HOST = 'https://d18pzythfo0bgy.cloudfront.net/';
// IMG (2026-11-06): the per-game articles carry card art in their body,
// CDN-only like the hero's <image>.
const REPORT_ALLOWED = new Set(
    [...ALLOWED, 'A', 'H2', 'H3', 'TABLE', 'THEAD', 'TBODY', 'TR', 'TH', 'TD',
     'DETAILS', 'SUMMARY', 'DIV', 'IMG', ...REPORT_SVG]);
const VOID = new Set(['BR', 'IMG']);
const DROP_CONTENT = new Set(['SCRIPT', 'STYLE']);

// Geometry/paint attributes the report charts use. Values are pattern-checked
// (colors, numbers, point lists, var() references — never url()/scripts).
const SVG_ATTRS = new Set([
    'viewbox', 'x', 'y', 'x1', 'y1', 'x2', 'y2', 'cx', 'cy', 'r', 'rx', 'ry',
    'width', 'height', 'points', 'fill', 'stroke', 'stroke-width',
    'text-anchor', 'font-size', 'role', 'xmlns',
    // The hero's type and geometry.
    'font-family', 'font-weight', 'transform', 'preserveaspectratio', 'opacity',
    'paint-order', 'stroke-linejoin',
]);
const SVG_VALUE = /^[\w\s.,#()%:/+-]*$/;

const escapeHtml = (s: string) =>
    s.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]!));

// Attributes that may survive in report mode: site-relative links and the
// report's own styling classes. Everything else is dropped.
function keptAttrs(el: Element, report: boolean): string {
    if (!report) return '';
    let out = '';
    const tag = el.tagName.toUpperCase();
    const href = el.getAttribute('href');
    if (tag === 'A' && href && /^\/[^/\\]/.test(href)) out += ` href="${escapeHtml(href)}"`;
    // An <image> may point at the card CDN and nowhere else; the path after
    // the host is checked too, so no query or traversal rides along.
    if (tag === 'IMAGE' && href && href.startsWith(IMAGE_HOST)
        && /^[\w-]+\/\d+\.(jpg|png|webp)$/.test(href.slice(IMAGE_HOST.length)))
        out += ` href="${escapeHtml(href)}"`;
    if (tag === 'CLIPPATH') {
        const id = el.getAttribute('id');
        if (id && CLIP_ID.test(id)) out += ` id="${id}"`;
    }
    if (tag === 'IMAGE') {
        const clip = el.getAttribute('clip-path');
        const ref = clip?.match(/^url\(#(hc-[a-f0-9]{8}-\d)\)$/);
        if (ref && CLIP_ID.test(ref[1])) out += ` clip-path="url(#${ref[1]})"`;
    }
    if (tag === 'IMG') {
        const src = el.getAttribute('src');
        if (src && src.startsWith(IMAGE_HOST)
            && /^[\w-]+\/\d+\.(jpg|png|webp)$/.test(src.slice(IMAGE_HOST.length)))
            out += ` src="${escapeHtml(src)}" alt="${escapeHtml(el.getAttribute('alt') ?? '')}" loading="lazy"`;
        else return '';   // an image from anywhere else loses its src, so it renders nothing
    }
    const cls = el.getAttribute('class');
    if (cls && /^[a-z-]+$/.test(cls) && cls.startsWith('report-')) out += ` class="${cls}"`;
    if (REPORT_SVG.has(tag)) {
        for (const name of el.getAttributeNames()) {
            const value = el.getAttribute(name) ?? '';
            if (SVG_ATTRS.has(name.toLowerCase()) && SVG_VALUE.test(value)
                && !/url/i.test(value))
                out += ` ${name}="${escapeHtml(value)}"`;   // original name: viewBox is case-sensitive
        }
    }
    return out;
}

function cleanNode(node: Node, report = false): string {
    if (node.nodeType === Node.TEXT_NODE) return escapeHtml(node.textContent ?? '');
    if (node.nodeType !== Node.ELEMENT_NODE) return '';

    const el = node as Element;
    const tag = el.tagName.toUpperCase();   // SVG elements report lowercase tag names
    if (DROP_CONTENT.has(tag)) return '';

    const inner = Array.from(el.childNodes).map(n => cleanNode(n, report)).join('');
    if (!(report ? REPORT_ALLOWED : ALLOWED).has(tag)) return inner;   // unknown tag: unwrap
    const name = tag.toLowerCase();
    return VOID.has(tag) ? `<${name}${keptAttrs(el, report)}>`
        : `<${name}${keptAttrs(el, report)}>${inner}</${name}>`;
}

export function sanitizeHtml(html: string): string {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    return Array.from(doc.body.childNodes).map(n => cleanNode(n)).join('');
}

export function sanitizeReportHtml(html: string): string {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    return Array.from(doc.body.childNodes).map(n => cleanNode(n, true)).join('');
}
