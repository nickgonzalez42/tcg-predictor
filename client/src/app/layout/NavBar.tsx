import { useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useAppSelector } from "../store/store";
import UserMenu from "./UserMenu";
import { useUserInfoQuery } from "../../features/account/accountApi";
import MarketTicker from "../shared/components/MarketTicker";

const midLinks = [
    { title: 'catalog', path: '/catalog' },
    { title: 'about', path: '/about' },
    { title: 'contact', path: '/contact' }
]

const rightLinks = [
    { title: 'login', path: '/login' },
    { title: 'register', path: '/register' }
]

export default function NavBar({ showTicker }: { showTicker?: boolean }) {
    const { data: user } = useUserInfoQuery();
    const { isLoading } = useAppSelector(state => state.ui)

    // Tablet/mobile: the text links collapse behind a ☰ dropdown (the button
    // is display:none on desktop). Navigating anywhere closes it.
    const [menuOpen, setMenuOpen] = useState(false);
    const { pathname } = useLocation();
    useEffect(() => { setMenuOpen(false) }, [pathname]);

    // Everything the dropdown lists: mid links, member links when signed in,
    // auth links when signed out (the avatar UserMenu stays in the bar).
    const menuLinks = [
        ...midLinks,
        ...(user
            ? [{ title: 'portfolio', path: '/portfolio' }, { title: 'watchlist', path: '/watchlist' }]
            : rightLinks),
    ];

    return (
        <header className="navbar">
            <div className="navbar__inner container">
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <NavLink to='/' className="navbar__brand">TCG PREDICTOR</NavLink>
                </div>

                <nav className="navbar__nav">
                    <ul className="navbar__links">
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

                <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                    {user ? (
                        <UserMenu />
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
                    <button className="navbar__icon-btn navbar__menu-btn"
                        aria-expanded={menuOpen} aria-label="Menu"
                        onClick={() => setMenuOpen(o => !o)}>
                        {menuOpen ? '✕' : '☰'}
                    </button>
                </div>
            </div>
            {menuOpen && (
                <nav className="navbar__menu">
                    {menuLinks.map(({ title, path }) => (
                        <NavLink key={path} to={path} className="navbar__link">
                            {title.toUpperCase()}
                        </NavLink>
                    ))}
                </nav>
            )}
            {isLoading && <div className="progress-bar" />}
            {showTicker && <MarketTicker />}
        </header>
    )
}
