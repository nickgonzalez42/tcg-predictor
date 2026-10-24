import { useEffect, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import UserMenu from "./UserMenu";
import NotificationBell from "./NotificationBell";
import { useUserInfoQuery } from "../../features/account/accountApi";
import MarketTicker from "../shared/components/MarketTicker";

const midLinks = [
    { title: 'catalog', path: '/catalog' },
    { title: 'reports', path: '/reports' },
    { title: 'about', path: '/about' }
]

const rightLinks = [
    { title: 'login', path: '/login' },
    { title: 'register', path: '/register' }
]

export default function NavBar({ showTicker }: { showTicker?: boolean }) {
    const { data: user } = useUserInfoQuery();

    // Desktop hover devices open the rail drawer on hover (NavBar.css); touch
    // devices, and every tablet/mobile width, toggle it by tapping the logo.
    // Reset on navigation.
    const [navOpen, setNavOpen] = useState(false);
    // Tablet/mobile (2026-10-23): the nav is a top bar that slides away as
    // the page scrolls down and returns on the first scroll up. The class is
    // only styled at those widths; an open drawer keeps the bar on screen.
    const [hidden, setHidden] = useState(false);
    // Desktop drawer is hover/focus-driven, so after clicking a link it lingers
    // open (the link keeps focus / the pointer is still over it). On navigation
    // we blur focus and briefly suppress hover (see .navbar--suppress) so it
    // collapses. mobile dropdown + touch drawer just reset their state.
    const [suppress, setSuppress] = useState(false);
    const navRef = useRef<HTMLElement>(null);
    const { pathname } = useLocation();
    useEffect(() => {
        setNavOpen(false);
        setHidden(false);
        (document.activeElement as HTMLElement | null)?.blur();
        setSuppress(true);
        const t = setTimeout(() => setSuppress(false), 600);
        return () => clearTimeout(t);
    }, [pathname]);

    // A tap/click anywhere outside the open drawer closes it, as does Escape.
    // The click isn't prevented, so it still lands on whatever was tapped.
    useEffect(() => {
        if (!navOpen) return;
        const onOutside = (e: MouseEvent) => {
            if (navRef.current && !navRef.current.contains(e.target as Node)) {
                setNavOpen(false);
            }
        };
        const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setNavOpen(false); };
        document.addEventListener('click', onOutside);
        document.addEventListener('keydown', onKey);
        return () => {
            document.removeEventListener('click', onOutside);
            document.removeEventListener('keydown', onKey);
        };
    }, [navOpen]);

    // Hide-on-scroll: direction from the native scroll position (the smooth
    // scroller keeps window scrolling), read once per frame. Small jitters
    // are ignored; near the top the bar is always shown.
    useEffect(() => {
        let last = window.scrollY;
        let raf = 0;
        const onScroll = () => {
            if (raf) return;
            raf = requestAnimationFrame(() => {
                raf = 0;
                const y = window.scrollY;
                const dy = y - last;
                last = y;
                if (y < 24) setHidden(false);
                else if (dy > 6) setHidden(true);
                else if (dy < -6) setHidden(false);
            });
        };
        window.addEventListener('scroll', onScroll, { passive: true });
        return () => { window.removeEventListener('scroll', onScroll); cancelAnimationFrame(raf); };
    }, []);

    // The logo toggles the drawer instead of navigating wherever the drawer
    // can't open on hover: touch devices, and the tablet/mobile top bar.
    const logoToggles = () =>
        window.matchMedia('(hover: none), (max-width: 1023px)').matches;

    return (
        <>
        {/* Tablet/mobile: a scrim under the open drawer. Tapping it closes the
            drawer and the tap goes nowhere else — nothing beneath the nav is
            reachable while it is open. Not rendered on desktop (CSS). */}
        {navOpen && <div className="navbar__scrim" onClick={() => setNavOpen(false)} />}
        <header ref={navRef}
            className={`navbar${navOpen ? ' navbar--open' : ''}${hidden ? ' navbar--hidden' : ''}${suppress ? ' navbar--suppress' : ''}`}>
            {/* Always-visible: the logo (static top-left on desktop; in the top
                bar on tablet/mobile). It lives OUTSIDE the sliding panel so the
                panel's transform doesn't drag it off-screen — and hovering it
                (desktop) or tapping it reveals the panel. */}
            <div className="navbar__bar">
                <NavLink to='/' className="navbar__brand" aria-label="CardStock home"
                    aria-expanded={navOpen}
                    onClick={(e) => {
                        // Tap toggles the drawer instead of navigating (the
                        // drawer's HOME link handles navigation there).
                        if (logoToggles()) {
                            e.preventDefault();
                            setNavOpen(o => !o);
                        }
                    }}>
                    {/* White card outline; once it topples on its side, a yellow
                        zigzag trend line draws across it (bottom-left → top-right
                        in the lain-down orientation) ending in an arrowhead. The
                        trend is drawn in the card's upright coordinates — the
                        parent's 90° rotation carries it into place. */}
                    <svg className="navbar__logo" viewBox="0 0 63 88" fill="none"
                        role="img" aria-hidden="true">
                        <rect x="2.5" y="2.5" width="58" height="83" rx="7"
                            stroke="#fff" strokeWidth="4" />
                        {/* Right-angle zigzag (every leg a 45° diagonal, so the
                            turns are 90°): up, down, up, down, up — up-legs longer
                            than the pullbacks for a net climb — breaking out just
                            below the card's top-right (in the toppled orientation). */}
                        <polyline className="navbar__logo-trend"
                            points="52,76 30,54 40,44 18,22 28,12 -6,-22" />
                        <path className="navbar__logo-arrow" d="M-4 -11 L-6 -22 L5 -20" />
                    </svg>
                    {/* Wordmark, revealed one line at a time after the animation. */}
                    <span className="navbar__wordmark" aria-hidden="true" style={{marginTop: "5px"}}>
                        <span className="navbar__word navbar__word--1">card</span>
                        <span className="navbar__word navbar__word--2">STOCK</span>
                    </span>
                </NavLink>
            </div>

            {/* The drawer. Desktop: a glass panel off-screen left that slides
                in. Tablet/mobile: .navbar__drawer unrolls it beneath the bar. */}
            <div className="navbar__drawer">
            <div className="navbar__panel">
                <nav className="navbar__nav">
                    <ul className="navbar__links">
                        {/* Where the logo opens the drawer rather than navigating
                            (touch, tablet/mobile), HOME lives here; hidden on
                            desktop hover devices via CSS. */}
                        <li className="navbar__home-item">
                            <NavLink to="/" className="navbar__link">HOME</NavLink>
                        </li>
                        {midLinks.map(({ title, path }) => (
                            <li key={path}>
                                <NavLink to={path} className="navbar__link">
                                    {title.toUpperCase()}
                                </NavLink>
                            </li>
                        ))}
                        {user && (
                            <>
                                <li>
                                    <NavLink to="/portfolio" className="navbar__link">PORTFOLIO</NavLink>
                                </li>
                                <li>
                                    <NavLink to="/watchlist" className="navbar__link">WATCHLIST</NavLink>
                                </li>
                            </>
                        )}
                    </ul>
                </nav>

                <div className="navbar__end">
                    {user ? (
                        <div className="navbar__usermenu"><NotificationBell /><UserMenu /></div>
                    ) : (
                        <ul className="navbar__links navbar__auth">
                            {rightLinks.map(({ title, path }) => (
                                <li key={path}>
                                    <NavLink to={path} className="navbar__link">
                                        {title.toUpperCase()}
                                    </NavLink>
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            </div>
            </div>
        </header>
        {/* The crawl is fixed to the viewport bottom. It must NOT live inside
            the header: the tablet/mobile bar's backdrop blur makes the header
            the containing block for fixed descendants, which would pin the
            crawl to the bar instead. */}
        {showTicker && <MarketTicker />}
        </>
    )
}
