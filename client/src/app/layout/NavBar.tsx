import { NavLink } from "react-router-dom";
import { useAppDispatch, useAppSelector } from "../store/store";
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
    const dispatch = useAppDispatch();

    return (
        <header className="navbar">
            <div className="navbar__inner container">
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <NavLink to='/' className="navbar__brand">TCG PREDICTOR</NavLink>
                </div>

                <nav>
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

                <div style={{ display: 'flex', alignItems: 'center' }}>
                    {user ? (
                        <UserMenu />
                    ) : (
                        <ul className="navbar__links">
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
            {isLoading && <div className="progress-bar" />}
            {showTicker && <MarketTicker />}
        </header>
    )
}
