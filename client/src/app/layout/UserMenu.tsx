import { useState, useRef, useEffect } from 'react';
import type { User } from '../models/user';
import { useLogoutMutation } from '../../features/account/accountApi';

type Props = {
    user: User
}

export default function UserMenu({ user }: Props) {
    const [logout] = useLogoutMutation();
    const [open, setOpen] = useState(false);
    const ref = useRef<HTMLDivElement>(null);

    useEffect(() => {
        const handler = (e: MouseEvent) => {
            if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
        };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, []);

    return (
        <div ref={ref} style={{ position: 'relative' }}>
            <button className="navbar__link" onClick={() => setOpen(o => !o)}>
                {user.email}
            </button>
            {open && (
                <div className="panel" style={{ position: 'absolute', right: 0, top: '100%', marginTop: 8, minWidth: 180, padding: '0.5rem' }}>
                    <button className="btn btn--ghost btn--block" style={{ justifyContent: 'flex-start' }}>👤 My profile</button>
                    <button className="btn btn--ghost btn--block" style={{ justifyContent: 'flex-start' }}>🕘 My orders</button>
                    <hr className="divider" />
                    <button className="btn btn--ghost btn--block" style={{ justifyContent: 'flex-start' }} onClick={() => logout()}>
                        ⎋ Logout
                    </button>
                </div>
            )}
        </div>
    );
}
