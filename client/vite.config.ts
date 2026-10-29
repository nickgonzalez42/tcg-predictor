import { defineConfig, type Plugin, type IndexHtmlTransformResult } from 'vite'
import mkcert from 'vite-plugin-mkcert'
import react from '@vitejs/plugin-react'

// Fold the built stylesheet into the HTML (2026-10-29).
//
// The shell paints the first screen without JavaScript, but the browser
// still refused to paint it until the external stylesheet arrived: one more
// round trip, which on a phone over mobile data was ~700 ms of blank screen
// on top of the HTML's own. The whole stylesheet is 21 KB gzipped, about the
// size of one extra request's overhead, so it rides inside the document
// instead. First paint then needs exactly one round trip.
//
// Inlining beats hand-written "critical CSS": there is no second copy of the
// rules to drift out of step with the real ones, and no flash when the rest
// of the sheet lands. The cost is that the CSS is no longer cached across
// visits, which suits a site whose visitors mostly arrive once from a link.
function inlineStylesheet(): Plugin {
    return {
        name: 'inline-stylesheet',
        apply: 'build',
        enforce: 'post',
        transformIndexHtml(html, ctx): IndexHtmlTransformResult {
            const entry = Object.values(ctx.bundle ?? {}).find(
                a => a.type === 'asset' && a.fileName.endsWith('.css'));
            if (!entry || entry.type !== 'asset') return html;
            const css = typeof entry.source === 'string'
                ? entry.source : new TextDecoder().decode(entry.source);
            // Drop the <link> for this sheet and put its rules in its place.
            const link = new RegExp(
                `\\s*<link[^>]+href="[^"]*${entry.fileName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}"[^>]*>`);
            if (!link.test(html)) return html;
            return html.replace(link, `\n    <style>${css}</style>`);
        },
    };
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), mkcert(), inlineStylesheet()],
})
