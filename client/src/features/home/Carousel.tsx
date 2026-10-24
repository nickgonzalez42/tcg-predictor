import { useEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { gsap } from "gsap";
import { Draggable } from "gsap/Draggable";
import { InertiaPlugin } from "gsap/InertiaPlugin";

gsap.registerPlugin(Draggable, InertiaPlugin);

// Auto-advancing, draggable, looping carousel (the home page's Top movers).
// Slides per view is the CSS custom property --per on the root (4 / 2 / 1 by
// breakpoint in HomePage.css), so the breakpoints live in one place and the
// slide widths are pure CSS; this file only measures one slide's width. The
// track is padded with clones of the last/first slides on each end, and
// whenever a move settles on a clone the track jumps (no animation) to the
// matching real slide — that is what makes the loop seamless both ways.
// Dragging reuses the GSAP Draggable + Inertia pair the market ticker runs
// on, with a snap to the slide grid. The belt pauses while hovered, focused,
// dragged, off-screen or in a hidden tab, and never auto-advances under
// prefers-reduced-motion (dragging and the buttons still work there).
const INTERVAL = 4;      // seconds a position holds before the belt advances
const RESUME = 5;        // seconds of stillness after the user lets go
const SLIDE_S = 0.65;

export type Slide = { key: string; node: ReactNode };

type Props = { slides: Slide[]; label: string; className?: string };

export default function Carousel({ slides, label, className = '' }: Props) {
    const rootRef = useRef<HTMLDivElement>(null);
    const trackRef = useRef<HTMLDivElement>(null);
    const [active, setActive] = useState(0);
    const navigate = useNavigate();
    const nav = useRef(navigate);
    nav.current = navigate;
    // goTo(i) from the buttons; swapped in by the effect below.
    const api = useRef<(i: number) => void>(() => {});
    const n = slides.length;
    // Clones per side: at least the widest --per, so the view is never short
    // of a neighbour at either end of the loop.
    const C = Math.min(n, 4);

    useEffect(() => {
        const root = rootRef.current, track = trackRef.current;
        if (!root || !track || n < 2) return;
        const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

        let per = 1, step = 0, idx = 0;
        let drag: Draggable | undefined;
        let tween: gsap.core.Tween | undefined;
        let timer: gsap.core.Tween | undefined;
        let hovering = false, dragging = false, focused = false, visible = true, moved = false;
        const state = { x: 0 };
        const setX = gsap.quickSetter(track, 'x', 'px') as (v: number) => void;
        const render = () => setX(state.x);
        const xOf = (i: number) => -(i + C) * step;
        // The first visible slide may range over the clone padding.
        const bounds = () => ({ minX: xOf(n + C - per), maxX: xOf(-C) });
        const wrap = (i: number) => ((i % n) + n) % n;

        const measure = () => {
            per = Math.max(1, parseInt(getComputedStyle(root).getPropertyValue('--per')) || 1);
            const first = track.children[0] as HTMLElement | undefined;
            step = (first?.offsetWidth ?? 0) + (parseFloat(getComputedStyle(track).columnGap) || 0);
            state.x = xOf(idx);
            render();
            drag?.applyBounds(bounds());
        };
        // Whenever the track comes to rest: land on the real slide for this
        // position (jumping off a clone if that is where it stopped).
        const settle = () => {
            const i = Math.round(-state.x / step) - C;
            idx = wrap(i);
            if (i !== idx) { state.x = xOf(idx); render(); }
            setActive(idx);
        };
        const goTo = (i: number, animate = !reduced) => {
            tween?.kill();
            const target = Math.max(-C, Math.min(n + C - per, i));
            if (!animate || !step) { state.x = xOf(target); render(); settle(); return; }
            tween = gsap.to(state, {
                x: xOf(target), duration: SLIDE_S, ease: 'power3.out', onUpdate: render, onComplete: settle,
            });
        };

        const canAuto = () => !reduced && !hovering && !dragging && !focused && visible && !document.hidden;
        const schedule = (delay = INTERVAL) => {
            timer?.kill();
            if (!canAuto()) return;
            timer = gsap.delayedCall(delay, () => {
                if (!canAuto()) return;
                goTo(idx + 1);
                schedule();
            });
        };
        const pause = () => timer?.kill();

        measure();
        const proxy = document.createElement('div');
        drag = Draggable.create(proxy, {
            type: 'x',
            trigger: track,
            inertia: true,
            dragClickables: true,     // every slide is a link; drags must start on them too
            minimumMovement: 4,
            cursor: 'grab',
            activeCursor: 'grabbing',
            bounds: bounds(),
            snap: (v: number) => Math.round(v / step) * step,
            onPress() {
                dragging = true;
                moved = false;
                pause();
                tween?.kill();
                gsap.set(proxy, { x: state.x });
                this.update();
            },
            onDragStart() { moved = true; },
            onDrag() { state.x = this.x; render(); },
            onRelease() {
                // A tap (no drag) throws nothing, so resume from here.
                if (!moved) { dragging = false; schedule(RESUME); }
            },
            onThrowUpdate() { state.x = this.x; render(); },
            onThrowComplete() {
                state.x = this.x;
                dragging = false;
                settle();
                schedule(RESUME);
            },
        })[0];

        // Clicks are handled here, not by the slides' own Links: Draggable
        // prevents the press event and then suppresses every native click on
        // its trigger, so a tile's Link never fires. This capture listener
        // sits above the trigger, so it runs first; it stops the event (which
        // keeps Draggable and the Link out of it) and routes a clean click
        // itself. A drag that ends over a tile is not a click. Modifier and
        // middle clicks fall through to the browser (new tab). Draggable may
        // follow a tap with a synthetic click ~50ms later — the time guard
        // swallows that duplicate.
        let lastClick = 0;
        const onClick = (e: MouseEvent) => {
            if (moved) { e.preventDefault(); e.stopPropagation(); return; }
            const a = (e.target as HTMLElement).closest<HTMLAnchorElement>('a[href]');
            if (!a || !track.contains(a)) return;
            e.stopPropagation();
            if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
            e.preventDefault();
            const now = performance.now();
            if (now - lastClick < 400) return;
            lastClick = now;
            const href = a.getAttribute('href') ?? '';
            if (/^https?:/.test(href)) window.location.assign(href);
            else nav.current(href);
        };
        root.addEventListener('click', onClick, true);

        const onEnter = () => { hovering = true; pause(); };
        const onLeave = () => { hovering = false; schedule(); };
        const onFocusIn = (e: FocusEvent) => {
            focused = true;
            pause();
            // Keyboard users tabbing onto a slide that is off to one side:
            // bring it into view.
            const slide = (e.target as HTMLElement).closest<HTMLElement>('[data-index]');
            const i = slide ? Number(slide.dataset.index) : NaN;
            if (!Number.isNaN(i) && (i < idx || i >= idx + per)) goTo(i);
        };
        const onFocusOut = (e: FocusEvent) => {
            if (root.contains(e.relatedTarget as Node | null)) return;
            focused = false;
            schedule();
        };
        const onVisibility = () => (document.hidden ? pause() : schedule());
        root.addEventListener('mouseenter', onEnter);
        root.addEventListener('mouseleave', onLeave);
        root.addEventListener('focusin', onFocusIn);
        root.addEventListener('focusout', onFocusOut);
        document.addEventListener('visibilitychange', onVisibility);
        const io = new IntersectionObserver(([entry]) => {
            visible = entry.isIntersecting;
            if (visible) schedule(); else pause();
        }, { threshold: 0.2 });
        io.observe(root);
        const ro = new ResizeObserver(measure);
        ro.observe(root);

        api.current = (i: number) => { goTo(i); schedule(RESUME); };
        schedule();

        return () => {
            pause();
            tween?.kill();
            drag?.kill();
            io.disconnect();
            ro.disconnect();
            root.removeEventListener('click', onClick, true);
            root.removeEventListener('mouseenter', onEnter);
            root.removeEventListener('mouseleave', onLeave);
            root.removeEventListener('focusin', onFocusIn);
            root.removeEventListener('focusout', onFocusOut);
            document.removeEventListener('visibilitychange', onVisibility);
            gsap.set(track, { clearProps: 'transform' });
            api.current = () => {};
        };
    }, [n, C]);

    if (n === 0) return null;
    const looping = n >= 2;
    const clone = (s: Slide, side: string) => (
        <div className="carousel__slide" key={`${side}-${s.key}`} aria-hidden="true" inert>
            {s.node}
        </div>
    );

    return (
        <div ref={rootRef} className={`carousel ${className}`.trim()}
            role="region" aria-roledescription="carousel" aria-label={label}>
            <div className="carousel__viewport">
                <div ref={trackRef} className="carousel__track">
                    {looping && slides.slice(-C).map(s => clone(s, 'pre'))}
                    {slides.map((s, i) => (
                        <div className="carousel__slide" key={s.key} data-index={i}
                            role="group" aria-roledescription="slide" aria-label={`${i + 1} of ${n}`}>
                            {s.node}
                        </div>
                    ))}
                    {looping && slides.slice(0, C).map(s => clone(s, 'post'))}
                </div>
            </div>
            {looping && (
                <div className="carousel__nav">
                    <div className="carousel__dots" role="tablist" aria-label="Slides">
                        {slides.map((s, i) => (
                            <button type="button" className="carousel__dot" key={s.key}
                                role="tab" aria-selected={i === active} aria-current={i === active}
                                aria-label={`Slide ${i + 1}`} onClick={() => api.current(i)} />
                        ))}
                    </div>
                    <div className="carousel__arrows">
                        <button type="button" className="carousel__arrow" aria-label="Previous"
                            onClick={() => api.current(active - 1)}>‹</button>
                        <button type="button" className="carousel__arrow" aria-label="Next"
                            onClick={() => api.current(active + 1)}>›</button>
                    </div>
                </div>
            )}
        </div>
    );
}
