import { useLogoutMutation } from '../../features/account/accountApi';

export default function UserMenu() {
    const [logout, { isLoading }] = useLogoutMutation();

    return (
        <button className="navbar__link" onClick={() => logout()} disabled={isLoading}>
            LOGOUT
        </button>
    );
}
