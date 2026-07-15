import { NavLink } from 'react-router-dom';
import { useLogoutMutation } from '../../features/account/accountApi';

export default function UserMenu() {
    const [logout, { isLoading }] = useLogoutMutation();

    return (
        <>
            <NavLink to="/settings/profile" className="navbar__link" title="Profile settings">
                PROFILE
            </NavLink>
            <button className="navbar__link" onClick={() => logout()} disabled={isLoading}>
                LOGOUT
            </button>
        </>
    );
}
