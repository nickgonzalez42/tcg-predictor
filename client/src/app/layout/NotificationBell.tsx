import { NavLink } from "react-router-dom";
import { useFetchAlertsQuery } from "../../features/watchlist/watchlistApi";

// Nav bell (signed-in only): links to the notifications page, with a badge
// counting alerts that have currently triggered.
export default function NotificationBell() {
    const { data: alerts } = useFetchAlertsQuery();
    const hits = (alerts ?? []).filter(a => a.hit).length;

    return (
        <NavLink to="/notifications" className="navbar__link navbar__bell"
            title="Notifications"
            aria-label={`Notifications${hits > 0 ? `: ${hits} alert${hits === 1 ? '' : 's'} hit` : ''}`}>
            NOTIFICATIONS
            {hits > 0 && <span className="navbar__bell-badge mono">{hits}</span>}
        </NavLink>
    );
}
