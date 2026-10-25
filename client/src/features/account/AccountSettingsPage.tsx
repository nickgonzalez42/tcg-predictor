import { useState } from "react";
import { Link } from "react-router-dom";
import Modal from "../../app/shared/components/Modal";
import CardLoader from "../../app/shared/components/CardLoader";
import { usePageMeta } from "../../lib/usePageMeta";
import {
    useSettingsQuery, useUpdateSettingsMutation, useChangePasswordMutation,
    useDeleteAccountMutation, useLogoutMutation,
} from "./accountApi";

// Account settings (2026-10-25): the minimal self-service surface a typical
// site offers — the email on file, the alert-email opt-in, password change
// (or set one for a Google sign-up), sign out, and account deletion. The
// public profile / handle / avatar / comments layer was removed the same day.
export default function AccountSettingsPage() {
    usePageMeta("Account");
    const { data: s, isLoading } = useSettingsQuery();
    const [logout, { isLoading: loggingOut }] = useLogoutMutation();
    const [confirmDelete, setConfirmDelete] = useState(false);

    if (isLoading || !s) return <CardLoader />;

    return (
        <div className="full-span account">
            <div className="account__head">
                <span className="kicker">Account</span>
                <h1 className="display account__title">Settings</h1>
            </div>

            <section className="panel account__section">
                <h2 className="account__h">Sign-in</h2>
                <dl className="account__facts">
                    <dt>Email</dt><dd>{s.email}</dd>
                    <dt>Signs in with</dt>
                    <dd>{[s.hasPassword ? 'password' : null, ...s.providers].filter(Boolean).join(' and ') || 'password'}</dd>
                    <dt>Member since</dt><dd>{s.createdAt}</dd>
                </dl>
            </section>

            <AlertToggle initial={s.alertEmails} />

            <PasswordSection hasPassword={s.hasPassword} />

            <section className="panel account__section">
                <h2 className="account__h">Session</h2>
                <button className="btn btn--outline" onClick={() => logout()} disabled={loggingOut}>Sign out</button>
            </section>

            <section className="panel account__section account__section--danger">
                <h2 className="account__h">Delete account</h2>
                <p className="account__hint">
                    Removes your collection, watchlist, sales and alerts permanently. There is no undo.
                </p>
                <button className="btn btn--outline btn--danger" onClick={() => setConfirmDelete(true)}>
                    Delete my account
                </button>
            </section>

            {confirmDelete && <DeleteModal onClose={() => setConfirmDelete(false)} />}
        </div>
    );
}

// Optimistic: the box flips at once and only snaps back if the save fails.
function AlertToggle({ initial }: { initial: boolean }) {
    const [on, setOn] = useState(initial);
    const [updateSettings, { isLoading }] = useUpdateSettingsMutation();
    const flip = async (v: boolean) => {
        setOn(v);
        try { await updateSettings({ alertEmails: v }).unwrap(); }
        catch { setOn(!v); }
    };
    return (
        <section className="panel account__section">
            <h2 className="account__h">Notifications</h2>
            <label className="account__toggle">
                <input type="checkbox" checked={on} disabled={isLoading} onChange={e => void flip(e.target.checked)} />
                <span>
                    <strong>Email me when a price alert hits</strong>
                    <span className="account__hint">
                        Alerts are set from your <Link to="/watchlist">watchlist</Link>; hits always show on the <Link to="/notifications">notifications</Link> page.
                    </span>
                </span>
            </label>
        </section>
    );
}

function PasswordSection({ hasPassword }: { hasPassword: boolean }) {
    const [change, { isLoading, error }] = useChangePasswordMutation();
    const [current, setCurrent] = useState('');
    const [next, setNext] = useState('');
    const [again, setAgain] = useState('');
    const [done, setDone] = useState(false);
    const mismatch = again !== '' && next !== again;
    const apiError = (error as { data?: string } | undefined)?.data;

    const submit = async () => {
        setDone(false);
        if (mismatch || !next) return;
        try {
            await change({ currentPassword: hasPassword ? current : undefined, newPassword: next }).unwrap();
            setCurrent(''); setNext(''); setAgain(''); setDone(true);
        } catch { /* the error shows below */ }
    };

    return (
        <section className="panel account__section">
            <h2 className="account__h">{hasPassword ? 'Change password' : 'Set a password'}</h2>
            {!hasPassword && (
                <p className="account__hint">
                    You signed up with Google. Setting a password also lets you sign in with your email.
                </p>
            )}
            <form className="account__form" onSubmit={e => { e.preventDefault(); void submit(); }}>
                {hasPassword && (
                    <label className="field-label">Current password
                        <input className="input" type="password" autoComplete="current-password"
                            value={current} onChange={e => setCurrent(e.target.value)} />
                    </label>
                )}
                <label className="field-label">New password
                    <input className="input" type="password" autoComplete="new-password" minLength={6}
                        value={next} onChange={e => setNext(e.target.value)} />
                </label>
                <label className="field-label">Repeat new password
                    <input className="input" type="password" autoComplete="new-password"
                        value={again} onChange={e => setAgain(e.target.value)} />
                </label>
                {mismatch && <p className="comment-error">The passwords don't match.</p>}
                {apiError && <p className="comment-error">{String(apiError)}</p>}
                {done && <p className="est-note">Password updated.</p>}
                <div>
                    <button className="btn" type="submit" disabled={isLoading || !next || mismatch || (hasPassword && !current)}>
                        {hasPassword ? 'Update password' : 'Set password'}
                    </button>
                </div>
            </form>
        </section>
    );
}

function DeleteModal({ onClose }: { onClose: () => void }) {
    const [remove, { isLoading, error }] = useDeleteAccountMutation();
    const [typed, setTyped] = useState('');
    const apiError = (error as { data?: string } | undefined)?.data;
    return (
        <Modal title="Delete your account?" onClose={onClose}>
            <p>
                This permanently deletes your account, every card in your collection and watchlist,
                your sales history and your alerts. Type <strong>DELETE</strong> to confirm.
            </p>
            <input className="input" value={typed} onChange={e => setTyped(e.target.value)}
                placeholder="DELETE" autoComplete="off" aria-label="Type DELETE to confirm" />
            {apiError && <p className="comment-error">{String(apiError)}</p>}
            <div className="modal__actions">
                <button className="btn btn--outline btn--danger" disabled={typed !== 'DELETE' || isLoading}
                    onClick={() => remove()}>
                    {isLoading ? 'Deleting…' : 'Delete everything'}
                </button>
                <button className="btn btn--outline" onClick={onClose}>Cancel</button>
            </div>
        </Modal>
    );
}
