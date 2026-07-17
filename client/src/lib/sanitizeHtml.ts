// Card descriptions are scraped from TCGplayer and carry light formatting HTML
// (<em>, <strong>, <br>, <p>, colored <font>/<span>, list tags). To show that
// formatting without an XSS surface, we re-render through a strict allowlist:
// only known formatting tags survive, ALL attributes are dropped, and script/
// style contents are discarded. Output is built from tag names + escaped text
// only — never copied markup — so it's safe for dangerouslySetInnerHTML.

const ALLOWED = new Set(
    ['B', 'STRONG', 'I', 'EM', 'U', 'BR', 'P', 'SPAN', 'FONT', 'UL', 'OL', 'LI', 'SUB', 'SUP']);
// Market-report bodies (our own pipeline's HTML) additionally carry headings,
// tables, and internal card links.
const REPORT_ALLOWED = new Set(
    [...ALLOWED, 'A', 'H2', 'TABLE', 'THEAD', 'TBODY', 'TR', 'TH', 'TD']);
const VOID = new Set(['BR']);
const DROP_CONTENT = new Set(['SCRIPT', 'STYLE']);

const escapeHtml = (s: string) =>
    s.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]!));

// Attributes that may survive in report mode: site-relative links and the
// report's own styling classes. Everything else is dropped.
function keptAttrs(el: Element, report: boolean): string {
    if (!report) return '';
    let out = '';
    const href = el.getAttribute('href');
    if (el.tagName === 'A' && href && /^\/[^/\\]/.test(href)) out += ` href="${escapeHtml(href)}"`;
    const cls = el.getAttribute('class');
    if (cls && /^[a-z-]+$/.test(cls) && cls.startsWith('report-')) out += ` class="${cls}"`;
    return out;
}

function cleanNode(node: Node, report = false): string {
    if (node.nodeType === Node.TEXT_NODE) return escapeHtml(node.textContent ?? '');
    if (node.nodeType !== Node.ELEMENT_NODE) return '';

    const el = node as Element;
    const tag = el.tagName;
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
