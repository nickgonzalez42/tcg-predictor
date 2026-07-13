// Card descriptions are scraped from TCGplayer and carry light formatting HTML
// (<em>, <strong>, <br>, <p>, colored <font>/<span>, list tags). To show that
// formatting without an XSS surface, we re-render through a strict allowlist:
// only known formatting tags survive, ALL attributes are dropped, and script/
// style contents are discarded. Output is built from tag names + escaped text
// only — never copied markup — so it's safe for dangerouslySetInnerHTML.

const ALLOWED = new Set(
    ['B', 'STRONG', 'I', 'EM', 'U', 'BR', 'P', 'SPAN', 'FONT', 'UL', 'OL', 'LI', 'SUB', 'SUP']);
const VOID = new Set(['BR']);
const DROP_CONTENT = new Set(['SCRIPT', 'STYLE']);

const escapeHtml = (s: string) =>
    s.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]!));

function cleanNode(node: Node): string {
    if (node.nodeType === Node.TEXT_NODE) return escapeHtml(node.textContent ?? '');
    if (node.nodeType !== Node.ELEMENT_NODE) return '';

    const el = node as Element;
    const tag = el.tagName;
    if (DROP_CONTENT.has(tag)) return '';

    const inner = Array.from(el.childNodes).map(cleanNode).join('');
    if (!ALLOWED.has(tag)) return inner;   // unknown tag: unwrap, keep clean children
    const name = tag.toLowerCase();
    return VOID.has(tag) ? `<${name}>` : `<${name}>${inner}</${name}>`;
}

export function sanitizeHtml(html: string): string {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    return Array.from(doc.body.childNodes).map(cleanNode).join('');
}
