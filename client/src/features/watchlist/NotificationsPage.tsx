import { Link } from "react-router-dom";
import { useFetchAlertsQuery, useDeleteAlertMutation, type CardAlert } from "./watchlistApi";
import { describeAlert, alertCurrentLabel } from "./alertFormat";
import { usePageMeta } from "../../lib/usePageMeta";
import { useUserInfoQuery } from "../account/accountApi";
import CardLoader from "../../app/shared/components/CardLoader";

// One notification row: card art + name, what the alert asked for, where the
// value sits now. The row links to the card; ✕ dismisses (deletes) the alert.
function AlertRow({ a, hit }: { a: CardAlert; hit: boolean }) {
    const [deleteAlert] = useDeleteAlertMutation();
    return (
        <li className={`alert-list__row notif-row${hit ? ' alert-list__row--hit' : ''}`}>
            {a.pictureUrl && (
                <img className="notif-row__thumb" src={a.pictureUrl} alt="" loading="lazy"
                    onError={e => { e.currentTarget.style.visibility = 'hidden'; }} />
            )}
            <span className="alert-list__desc">
                <Link className="notif-row__name" to={`/catalog/${a.game}/${a.productId}`}>
                    {a.name ?? `${a.game} #${a.productId}`}
                </Link>
                {a.setName && <span className="est-note"> · {a.setName}</span>}
                <div>
                    {describeAlert(a)}
                    <span className="alert-list__now mono"> · {alertCurrentLabel(a)}</span>
                </div>
            </span>
            {hit && <span className="alert-list__hit mono">HIT</span>}
            <button className="btn btn--outline btn--circle" title={hit ? 'Dismiss (deletes the alert)' : 'Delete alert'}
                onClick={() => deleteAlert({ id: a.id })}>✕</button>
        </li>
    );
}

// Notifications: every alert that has triggered, then the still-armed ones
// below so the page doubles as an all-alerts overview.
export default function NotificationsPage() {
    usePageMeta("Notifications");
    const { data: user, isLoading: userLoading } = useUserInfoQuery();
    const { data: alerts, isLoading } = useFetchAlertsQuery(undefined, { skip: !user });

    if (userLoading || (user && isLoading)) return <CardLoader />;
    if (!user) return <p className="full-span est-note">Sign in to see your notifications.</p>;

    const hits = (alerts ?? []).filter(a => a.hit);
    const armed = (alerts ?? []).filter(a => !a.hit);

    return (
        <div className="full-span notif-page">
            <h1>Notifications</h1>

            {hits.length > 0 ? (
                <ul className="alert-list">
                    {hits.map(a => <AlertRow key={a.id} a={a} hit />)}
                </ul>
            ) : (
                <p className="est-note">
                    Nothing has triggered. When a price alert hits its target, it shows up here
                    {armed.length === 0 && (
                        <> — set alerts from the 🔔 button on your <Link to="/watchlist">watchlist</Link></>
                    )}.
                </p>
            )}

            {armed.length > 0 && (
                <>
                    <h2 className="notif-page__sub">Waiting to trigger</h2>
                    <ul className="alert-list">
                        {armed.map(a => <AlertRow key={a.id} a={a} hit={false} />)}
                    </ul>
                </>
            )}
        </div>
    );
}
