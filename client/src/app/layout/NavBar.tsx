import { NavLink } from "react-router-dom";
import { useAppDispatch, useAppSelector } from "../store/store";
import { setDarkMode } from "./uiSlice";
import UserMenu from "./UserMenu";
import { useUserInfoQuery } from "../../features/account/accountApi";

const midLinks = [
    { title: 'catalog', path: '/catalog' },
    { title: 'about', path: '/about' },
    { title: 'contact', path: '/contact' }
]

const rightLinks = [
    { title: 'login', path: '/login' },
    { title: 'register', path: '/register' }
]

export default function NavBar() {
    const { data: user } = useUserInfoQuery();
    const { isLoading, darkMode } = useAppSelector(state => state.ui)
    const dispatch = useAppDispatch();

    return (
        <header className="navbar">
            <div className="navbar__inner">
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    <NavLink to='/' className="navbar__brand">TCG PREDICTOR</NavLink>
                    <button
                        className="navbar__icon-btn"
                        onClick={() => dispatch(setDarkMode())}
                        title="Toggle theme"
                    >
                        {darkMode ? '🌙' : '☀️'}
                    </button>
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
                    </ul>
                </nav>

                <div style={{ display: 'flex', alignItems: 'center' }}>
                    {user ? (
                        <UserMenu user={user} />
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
        </header>
    )
}
