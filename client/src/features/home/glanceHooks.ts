import { useEffect, useRef, useState } from "react";

// The reveal hooks and number helpers the home panels and the About page
// share (moved out of Glance.tsx so that file exports components alone).

const reducedMotion = () =>
    typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

// True once the element has entered the viewport (never flips back).
export function useInView<T extends HTMLElement>() {
    const ref = useRef<T>(null);
    // Nothing to wait for under reduced motion, or in a browser that cannot watch.
    const [inView, setInView] = useState(() => reducedMotion() || !('IntersectionObserver' in window));
    useEffect(() => {
        const el = ref.current;
        if (!el || inView) return;
        const io = new IntersectionObserver(entries => {
            if (entries.some(e => e.isIntersecting)) { setInView(true); io.disconnect(); }
        }, { threshold: 0.25 });
        io.observe(el);
        return () => io.disconnect();
    }, [inView]);
    return { ref, inView };
}

// Eases a number from 0 to `target` over `ms` once `active`.
export function useCountUp(target: number, active: boolean, ms = 1200) {
    const [value, setValue] = useState(0);
    const instant = reducedMotion();     // no easing: the final number as soon as it is active
    useEffect(() => {
        if (!active || instant) return;
        let raf = 0;
        const t0 = performance.now();
        const tick = (t: number) => {
            const p = Math.min(1, (t - t0) / ms);
            const eased = 1 - Math.pow(1 - p, 3);
            setValue(target * eased);
            if (p < 1) raf = requestAnimationFrame(tick);
        };
        raf = requestAnimationFrame(tick);
        return () => cancelAnimationFrame(raf);
    }, [target, active, ms, instant]);
    return active && instant ? target : value;
}

export const compact = (n: number) =>
    n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}k` : `${Math.round(n)}`;

// Log-return error → the percentage a reader understands ("±12%").
export const pct = (logRet: number) => (Math.exp(Math.abs(logRet)) - 1) * 100;
