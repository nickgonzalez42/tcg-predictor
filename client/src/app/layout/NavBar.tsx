import { useEffect, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import UserMenu from "./UserMenu";
import { useUserInfoQuery } from "../../features/account/accountApi";
import MarketTicker from "../shared/components/MarketTicker";

const midLinks = [
    { title: 'catalog', path: '/catalog' },
    { title: 'about', path: '/about' }
]

const rightLinks = [
    { title: 'login', path: '/login' },
    { title: 'register', path: '/register' }
]

export default function NavBar({ showTicker }: { showTicker?: boolean }) {
    const { data: user } = useUserInfoQuery();

    // Tablet/mobile: the text links collapse behind a ☰ dropdown (the button
    // is display:none on desktop). Navigating anywhere closes it.
    const [menuOpen, setMenuOpen] = useState(false);
    // Touch devices can't hover, so tapping the logo toggles the rail drawer
    // (hover devices open it on hover — see NavBar.css). Reset on navigation.
    const [navOpen, setNavOpen] = useState(false);
    // Desktop drawer is hover/focus-driven, so after clicking a link it lingers
    // open (the link keeps focus / the pointer is still over it). On navigation
    // we blur focus and briefly suppress hover (see .navbar--suppress) so it
    // collapses. mobile dropdown + touch drawer just reset their state.
    const [suppress, setSuppress] = useState(false);
    const navRef = useRef<HTMLElement>(null);
    const { pathname } = useLocation();
    useEffect(() => {
        setMenuOpen(false);
        setNavOpen(false);
        (document.activeElement as HTMLElement | null)?.blur();
        setSuppress(true);
        const t = setTimeout(() => setSuppress(false), 600);
        return () => clearTimeout(t);
    }, [pathname]);

    // Touch: a tap/click anywhere outside the open drawer closes it. The click
    // isn't prevented, so it still lands on whatever was tapped (not modal).
    useEffect(() => {
        if (!navOpen) return;
        const onOutside = (e: MouseEvent) => {
            if (navRef.current && !navRef.current.contains(e.target as Node)) {
                setNavOpen(false);
            }
        };
        document.addEventListener('click', onOutside);
        return () => document.removeEventListener('click', onOutside);
    }, [navOpen]);

    // Everything the dropdown lists: mid links, member links when signed in,
    // auth links when signed out (the avatar UserMenu stays in the bar).
    const menuLinks = [
        ...midLinks,
        ...(user
            ? [{ title: 'portfolio', path: '/portfolio' }, { title: 'watchlist', path: '/watchlist' }]
            : rightLinks),
    ];

    return (
        <header ref={navRef}
            className={`navbar${navOpen ? ' navbar--open' : ''}${suppress ? ' navbar--suppress' : ''}`}>
            {/* Always-visible: the logo (static, top-left) and the mobile ☰. The
                logo lives OUTSIDE the sliding panel so the panel's transform
                doesn't drag it off-screen — and hovering it reveals the panel. */}
            <div className="navbar__bar">
                <NavLink to='/' className="navbar__brand" aria-label="cardstock home"
                    aria-expanded={navOpen}
                    onClick={(e) => {
                        // Touch: tap toggles the drawer instead of navigating
                        // (the drawer's HOME link handles navigation there).
                        if (window.matchMedia('(hover: none)').matches) {
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
                <button className="navbar__icon-btn navbar__menu-btn"
                    aria-expanded={menuOpen} aria-label="Menu"
                    onClick={() => setMenuOpen(o => !o)}>
                    {menuOpen ? '✕' : '☰'}
                </button>
            </div>

            {/* Desktop: the sliding glass drawer — off-screen left, in on hover. */}
            <div className="navbar__panel">
                <nav className="navbar__nav">
                    <ul className="navbar__links">
                        {/* Touch only: the tapped logo opens the drawer rather
                            than navigating, so HOME lives here (hidden on hover
                            devices via CSS). */}
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
                        <div className="navbar__usermenu"><UserMenu /></div>
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
            {menuOpen && (
                <nav className="navbar__menu">
                    {menuLinks.map(({ title, path }) => (
                        <NavLink key={path} to={path} className="navbar__link">
                            {title.toUpperCase()}
                        </NavLink>
                    ))}
                    {/* Signed-in: logout lives in the dropdown on mobile (the
                        in-bar UserMenu is hidden at this breakpoint). */}
                    {user && <UserMenu />}
                </nav>
            )}
            {showTicker && <MarketTicker />}
        </header>
    )
}
