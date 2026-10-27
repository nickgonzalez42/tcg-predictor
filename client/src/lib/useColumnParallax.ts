import { useEffect, type RefObject } from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

gsap.registerPlugin(ScrollTrigger);

// Two side-by-side columns of unequal height finish together (2026-10-26):
// the shorter one is translated down by the height difference, in step with
// how far the page has scrolled, so both bottoms arrive at once as the page
// reaches its end. Desktop only (the columns stack below 1024px), off for
// reduced-motion, and rebuilt whenever either column changes size (chart,
// images and forecasts all arrive after first paint). Works under
// ScrollSmoother because ScrollTrigger reads the smoothed position.
export function useColumnParallax(
    a: RefObject<HTMLElement | null>, b: RefObject<HTMLElement | null>, deps: unknown[],
) {
    useEffect(() => {
        const ea = a.current, eb = b.current;
        if (!ea || !eb) return;
        if (!window.matchMedia("(min-width: 1024px)").matches) return;
        if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

        let ctx: gsap.Context | null = null;
        let timer: number | undefined;

        const build = () => {
            ctx?.revert();
            ctx = null;
            const ha = ea.offsetHeight, hb = eb.offsetHeight;
            const diff = Math.abs(ha - hb);
            if (diff < 24) return;                        // nothing worth moving
            const short = ha < hb ? ea : eb;
            ctx = gsap.context(() => {
                gsap.to(short, {
                    y: diff,
                    ease: "none",
                    scrollTrigger: {
                        start: 0,                              // page top
                        end: () => ScrollTrigger.maxScroll(window),
                        scrub: true,
                        invalidateOnRefresh: true,
                    },
                });
            });
        };
        const schedule = () => {
            window.clearTimeout(timer);
            timer = window.setTimeout(build, 120);
        };

        // First build after the parent's smoother has mounted (child effects
        // run before parent effects on the very first render).
        const raf = requestAnimationFrame(build);
        const ro = new ResizeObserver(schedule);
        ro.observe(ea);
        ro.observe(eb);
        window.addEventListener("resize", schedule);
        return () => {
            cancelAnimationFrame(raf);
            window.clearTimeout(timer);
            ro.disconnect();
            window.removeEventListener("resize", schedule);
            ctx?.revert();
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, deps);
}
