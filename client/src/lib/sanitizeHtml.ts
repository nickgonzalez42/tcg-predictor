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
const REPORT_SVG = new Set(['SVG', 'G', 'LINE', 'RECT', 'TEXT', 'POLYLINE', 'CIRCLE']);
const REPORT_ALLOWED = new Set(
    [...ALLOWED, 'A', 'H2', 'TABLE', 'THEAD', 'TBODY', 'TR', 'TH', 'TD', ...REPORT_SVG]);
const VOID = new Set(['BR']);
const DROP_CONTENT = new Set(['SCRIPT', 'STYLE']);

// Geometry/paint attributes the report charts use. Values are pattern-checked
// (colors, numbers, point lists, var() references — never url()/scripts).
const SVG_ATTRS = new Set([
    'viewbox', 'x', 'y', 'x1', 'y1', 'x2', 'y2', 'cx', 'cy', 'r', 'rx',
    'width', 'height', 'points', 'fill', 'stroke', 'stroke-width',
    'text-anchor', 'font-size', 'role', 'xmlns',
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
    return VOID.has(tag) ? `<${name}>` : `<${name}${keptAttrs(el, report)}>${inner}</${name}>`;
}

export function sanitizeHtml(html: string): string {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    return Array.from(doc.body.childNodes).map(n => cleanNode(n)).join('');
}

export function sanitizeReportHtml(html: string): string {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    return Array.from(doc.body.childNodes).map(n => cleanNode(n, true)).join('');
}
