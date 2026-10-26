import { useEffect, useRef } from "react";
import { Link, useParams } from "react-router-dom";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { useFetchReportQuery } from "./reportsApi";
import CardLoader from "../../app/shared/components/CardLoader";
import { usePageMeta } from "../../lib/usePageMeta";
import { sanitizeReportHtml } from "../../lib/sanitizeHtml";
import { cardImageUrl } from "../../lib/cardImageUrl";

gsap.registerPlugin(ScrollTrigger);

// One weekly market report. The body is pipeline-generated HTML, re-rendered
// through the strict report allowlist before display.
export default function ReportPage() {
    const { slug } = useParams<{ slug: string }>();
    const { data: report, isLoading } = useFetchReportQuery(slug!, { skip: !slug });
    usePageMeta(report?.title ?? "Market Report", report?.summary);
    const bodyRef = useRef<HTMLDivElement>(null);

    // Scroll-triggered chart draw-in: when a report chart enters the viewport,
    // all its bars grow out of the zero line together and all its lines trace
    // left-to-right together; labels fade in behind them. Marks are hidden up
    // front via gsap.set so nothing flashes before the trigger fires.
    //
    // The per-game sections are collapsed <details>, which complicates this
    // two ways: a chart inside a closed dropdown has no layout, so a
    // ScrollTrigger created for it computes a garbage start and plays
    // invisibly at mount; and toggling any dropdown reflows the whole page,
    // leaving every other trigger's position stale. So charts are primed
    // (hidden) immediately but armed with a trigger only once visible — at
    // mount for top-level charts, on first open for dropdown charts — and
    // every toggle refreshes all trigger positions.
    useEffect(() => {
        const root = bodyRef.current;
        if (!root || !report) return;
        if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
        const cleanups: (() => void)[] = [];
        const ctx = gsap.context(() => {
            // Hide the chart's marks now (works without layout) and return an
            // arm() that creates the scroll-triggered reveal; original
            // geometry is captured here because the gsap.set overwrites it.
            const prime = (svg: SVGSVGElement) => {
                // Diverging bar charts carry a zero line; every bar is anchored
                // to it (negative bars slide left as they grow). Without one,
                // each bar simply grows from its own left edge.
                const zeroAttr = svg.querySelector("line")?.getAttribute("x1");
                const zeroX = zeroAttr ? parseFloat(zeroAttr) : null;
                const bars = Array.from(svg.querySelectorAll<SVGRectElement>("rect"))
                    .map(bar => ({
                        bar,
                        x: parseFloat(bar.getAttribute("x") ?? "0"),
                        width: parseFloat(bar.getAttribute("width") ?? "0"),
                    }));
                for (const { bar, x } of bars)
                    gsap.set(bar, { attr: { x: zeroX ?? x, width: 0 } });
                const lines: { line: SVGPolylineElement }[] = [];
                for (const line of svg.querySelectorAll<SVGPolylineElement>("polyline")) {
                    let len = 0;
                    try { len = line.getTotalLength(); } catch { /* no layout yet */ }
                    if (!len) continue;
                    gsap.set(line, { strokeDasharray: len, strokeDashoffset: len });
                    lines.push({ line });
                }
                // text-anchor="end" marks the static labels (row names, axis
                // values) and report-chart-title the chart's caption — both
                // visible from the start; the rest (value/series labels) fade
                // in with the marks.
                const texts = Array.from(svg.querySelectorAll("text"))
                    .filter(t => t.getAttribute("text-anchor") !== "end"
                        && !t.classList.contains("report-chart-title"));
                if (texts.length) gsap.set(texts, { opacity: 0 });
                return () => {
                    const tl = gsap.timeline({
                        scrollTrigger: { trigger: svg, start: "top 85%" },
                        defaults: { ease: "power2.out" },
                    });
                    for (const { bar, x, width } of bars)
                        tl.to(bar, { attr: { x, width }, duration: 0.6 }, 0);
                    for (const { line } of lines)
                        tl.to(line, { strokeDashoffset: 0, duration: 0.9, ease: "none" }, 0);
                    if (texts.length)
                        tl.to(texts, { opacity: 1, duration: 0.35 }, "-=0.25");
                };
            };

            for (const svg of root.querySelectorAll<SVGSVGElement>("svg.report-chart")) {
                const arm = prime(svg);
                const closed = svg.closest("details:not([open])");
                if (!closed) { arm(); continue; }
                const onOpen = () => {
                    if (!(closed as HTMLDetailsElement).open) return;
                    // ctx.add so the deferred tweens still revert on unmount.
                    ctx.add(arm);
                    closed.removeEventListener("toggle", onOpen);
                };
                closed.addEventListener("toggle", onOpen);
                cleanups.push(() => closed.removeEventListener("toggle", onOpen));
            }

            // Any dropdown toggle changes the page length under every chart
            // below it; recompute all trigger positions. (Registered after
            // the arm handlers, so a fresh trigger is refreshed too.)
            for (const det of root.querySelectorAll("details")) {
                const refresh = () => ScrollTrigger.refresh();
                det.addEventListener("toggle", refresh);
                cleanups.push(() => det.removeEventListener("toggle", refresh));
            }
        }, root);
        return () => {
            for (const fn of cleanups) fn();
            ctx.revert();
        };
    }, [report]);

    // Hover card preview (2026-10-09, user request): hovering any card link
    // in the report floats that card's art beside the cursor. Pointer-device
    // only; the preview appears only once the image has actually loaded, so
    // cards without art (or slow loads) simply show nothing.
    useEffect(() => {
        const root = bodyRef.current;
        if (!root || !report) return;
        if (!window.matchMedia("(hover: hover)").matches) return;
        const peek = document.createElement("div");
        peek.className = "card-peek";
        const img = document.createElement("img");
        img.alt = "";
        peek.appendChild(img);
        document.body.appendChild(peek);
        let current = "";
        const place = (e: MouseEvent) => {
            const w = peek.offsetWidth || 244, h = peek.offsetHeight || 344;
            let x = e.clientX + 16, y = e.clientY + 16;
            if (x + w > window.innerWidth - 8) x = e.clientX - w - 16;
            if (y + h > window.innerHeight - 8) y = Math.max(8, window.innerHeight - h - 8);
            peek.style.left = `${x}px`;
            peek.style.top = `${y}px`;
        };
        const linkOf = (t: EventTarget | null) =>
            t instanceof Element ? t.closest<HTMLAnchorElement>('a[href^="/catalog/"]') : null;
        const over = (e: MouseEvent) => {
            const a = linkOf(e.target);
            if (!a) return;
            const m = a.getAttribute("href")!.match(/^\/catalog\/([a-z]+)\/(\d+)/);
            if (!m) return;
            const src = cardImageUrl(m[1], Number(m[2]));
            current = src;
            img.onload = () => {
                if (current === src) peek.classList.add("card-peek--on");
            };
            if (img.dataset.src !== src) {
                img.dataset.src = src;
                peek.classList.remove("card-peek--on");
                img.src = src;
            } else if (img.complete && img.naturalWidth) {
                peek.classList.add("card-peek--on");
            }
            place(e);
        };
        const out = (e: MouseEvent) => {
            const from = linkOf(e.target);
            if (from && linkOf(e.relatedTarget) !== from) {
                current = "";
                peek.classList.remove("card-peek--on");
            }
        };
        const move = (e: MouseEvent) => {
            if (peek.classList.contains("card-peek--on")) place(e);
        };
        root.addEventListener("mouseover", over);
        root.addEventListener("mouseout", out);
        root.addEventListener("mousemove", move);
        return () => {
            root.removeEventListener("mouseover", over);
            root.removeEventListener("mouseout", out);
            root.removeEventListener("mousemove", move);
            peek.remove();
        };
    }, [report]);

    if (isLoading) return <CardLoader />;
    if (!report) {
        return (
            <div className="reports full-span">
                <p className="est-note">
                    That report doesn't exist. <Link to="/reports">All reports</Link>
                </p>
            </div>
        );
    }

    return (
        <article className="reports report full-span">
            <h1 className="reports__title">{report.title}</h1>
            <div className="report__body" ref={bodyRef}
                dangerouslySetInnerHTML={{ __html: sanitizeReportHtml(report.bodyHtml) }} />
        </article>
    );
}
