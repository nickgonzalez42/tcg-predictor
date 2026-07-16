import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { gsap } from "gsap";
import { Draggable } from "gsap/Draggable";
import { InertiaPlugin } from "gsap/InertiaPlugin";
import { useFetchMoversQuery } from "../../../features/catalog/catalogApi";
import { gameKey } from "../../../lib/util";

gsap.registerPlugin(Draggable, InertiaPlugin);

// Full-width scrolling strip of top movers under the navbar. GSAP drives a
// continuous marquee; the user can grab and drag it, and 3s after they let go
// the auto-scroll resumes from wherever they left it. prefers-reduced-motion
// renders it static. Change figures are the model's 1-year forecast.
const SPEED = 60;          // px/sec
const RESUME_DELAY = 3;    // seconds of stillness before auto-scroll resumes

export default function MarketTicker() {
    const { data: movers } = useFetchMoversQuery({ count: 12 });
    const trackRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        const track = trackRef.current;
        if (!track || !movers?.length) return;
        if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

        // The content is duplicated, so one group's width is the loop distance;
        // wrapping x into [-groupW, 0] makes the belt seamless in both auto and
        // drag. quickSetter writes the transform; state.x is the source of truth.
        const groupW = (track.children[0] as HTMLElement).offsetWidth;
        if (!groupW) return;
        const wrap = gsap.utils.wrap(-groupW, 0);
        const setX = gsap.quickSetter(track, 'x', 'px') as (v: number) => void;
        const state = { x: 0 };
        const render = () => setX(wrap(state.x));

        // Auto-scroll runs only when the user is neither hovering nor dragging.
        let auto: gsap.core.Tween | undefined;
        let resume: gsap.core.Tween | undefined;
        let hovering = false;
        let dragging = false;

        const startAuto = () => {
            auto?.kill();
            auto = gsap.to(state, {
                x: '-=' + groupW,
                duration: groupW / SPEED,
                ease: 'none',
                repeat: -1,
                onUpdate: render,
            });
        };
        const stopAuto = () => { auto?.kill(); auto = undefined; resume?.kill(); };
        const scheduleResume = (delay: number) => {
            resume?.kill();
            resume = gsap.delayedCall(delay, () => {
                if (!hovering && !dragging) startAuto();
            });
        };
        startAuto();

        // Hover pauses the belt for reading; leaving resumes it right away.
        const onEnter = () => { hovering = true; stopAuto(); };
        const onLeave = () => { hovering = false; if (!dragging) scheduleResume(0); };
        track.addEventListener('mouseenter', onEnter);
        track.addEventListener('mouseleave', onLeave);

        // Draggable moves an off-DOM proxy (so it never fights our own wrapped
        // transform); we mirror its x onto the track each frame. Releasing
        // throws the belt with inertia; the auto-scroll only resumes once that
        // glide has settled AND the user has been still for the delay.
        const proxy = document.createElement('div');
        const drag = Draggable.create(proxy, {
            type: 'x',
            trigger: track,
            inertia: true,
            cursor: 'grab',
            activeCursor: 'grabbing',
            onPress() {
                dragging = true;
                stopAuto();
                gsap.set(proxy, { x: state.x });
                this.update();
            },
            onDrag() {
                state.x = this.x;
                render();
            },
            onThrowUpdate() {
                state.x = this.x;
                render();
            },
            onThrowComplete() {
                state.x = this.x;
                dragging = false;
                scheduleResume(RESUME_DELAY);
            },
        })[0];

        return () => {
            auto?.kill();
            resume?.kill();
            drag.kill();
            track.removeEventListener('mouseenter', onEnter);
            track.removeEventListener('mouseleave', onLeave);
            gsap.set(track, { clearProps: 'transform' });
        };
    }, [movers]);

    if (!movers?.length) return null;

    const chips = movers.map(m => {
        // Mixed horizons: young games carry a 6m forecast instead of 12m.
        const pct = (m.fcstTo != null && m.price
            ? (m.fcstTo / m.price - 1) * 100 : m.fcst12Pct) ?? 0;
        const up = pct >= 0;
        return (
            <Link
                key={`${m.game}-${m.id}`}
                className={`tkc ${up ? 'tkc--up' : 'tkc--down'}`}
                to={`/catalog/${gameKey(m.game)}/${m.id}`}
                draggable={false}
            >
                {m.name} {up ? '▲' : '▼'} {up ? '+' : '−'}{Math.abs(pct).toFixed(1)}%
            </Link>
        );
    });

    return (
        <div className="ticker" aria-label="Top movers, model forecast (1Y, or 6M for newer games)">
            <div className="ticker__track" ref={trackRef}>
                {/* content twice for a seamless -groupW loop */}
                <div className="ticker__group">{chips}<span className="mono ticker__tag">· 1Y FORECAST ·</span></div>
                <div className="ticker__group" aria-hidden="true">{chips}<span className="mono ticker__tag">· 1Y FORECAST ·</span></div>
            </div>
        </div>
    );
}
