import { useEffect, useRef } from "react";
import { Link, useParams } from "react-router-dom";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { useFetchReportQuery } from "./reportsApi";
import CardLoader from "../../app/shared/components/CardLoader";
import { usePageMeta } from "../../lib/usePageMeta";
import { sanitizeReportHtml } from "../../lib/sanitizeHtml";

gsap.registerPlugin(ScrollTrigger);

// One weekly market report. The body is pipeline-generated HTML, re-rendered
// through the strict report allowlist before display.
export default function ReportPage() {
    const { slug } = useParams<{ slug: string }>();
    const { data: report, isLoading } = useFetchReportQuery(slug!, { skip: !slug });
    usePageMeta(report?.title ?? "Market Report", report?.summary);
    const bodyRef = useRef<HTMLDivElement>(null);

    // Scroll-triggered chart draw-in: when a report chart enters the viewport,
    // its bars grow out of the zero line and its lines trace left-to-right,
    // strictly one mark at a time; labels fade in behind them. Marks are
    // hidden up front via gsap.set (a timeline of .from()s would leave later
    // bars visible until their turn, then blink them out to re-grow).
    useEffect(() => {
        const root = bodyRef.current;
        if (!root || !report) return;
        if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
        const ctx = gsap.context(() => {
            for (const svg of root.querySelectorAll<SVGSVGElement>("svg.report-chart")) {
                const tl = gsap.timeline({
                    scrollTrigger: { trigger: svg, start: "top 85%" },
                    defaults: { ease: "power2.out" },
                });
                // Diverging bar charts carry a zero line; every bar is anchored
                // to it (negative bars slide left as they grow). Without one,
                // each bar simply grows from its own left edge.
                const zeroAttr = svg.querySelector("line")?.getAttribute("x1");
                const zeroX = zeroAttr ? parseFloat(zeroAttr) : null;
                for (const bar of svg.querySelectorAll<SVGRectElement>("rect")) {
                    const x = parseFloat(bar.getAttribute("x") ?? "0");
                    const width = parseFloat(bar.getAttribute("width") ?? "0");
                    gsap.set(bar, { attr: { x: zeroX ?? x, width: 0 } });
                    tl.to(bar, { attr: { x, width }, duration: 0.3 });
                }
                for (const line of svg.querySelectorAll<SVGPolylineElement>("polyline")) {
                    const len = line.getTotalLength();
                    gsap.set(line, { strokeDasharray: len, strokeDashoffset: len });
                    tl.to(line, { strokeDashoffset: 0, duration: 0.6, ease: "none" });
                }
                const texts = svg.querySelectorAll("text");
                if (texts.length) {
                    gsap.set(texts, { opacity: 0 });
                    tl.to(texts, { opacity: 1, duration: 0.35 }, "-=0.2");
                }
            }
        }, root);
        return () => ctx.revert();
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
