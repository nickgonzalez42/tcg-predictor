import { NavLink } from 'react-router-dom';
import { useLogoutMutation } from '../../features/account/accountApi';

export default function UserMenu() {
    const [logout, { isLoading }] = useLogoutMutation();

    return (
        <>
            <NavLink to="/settings" className="navbar__link" title="Account settings">
                ACCOUNT
            </NavLink>
            <button className="navbar__link" onClick={() => logout()} disabled={isLoading}>
                LOGOUT
            </button>
        </>
    );
}
