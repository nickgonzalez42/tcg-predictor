import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useFetchPublicProfileQuery, useFetchPublicCardsQuery } from "./socialApi";
import { usePageMeta } from "../../lib/usePageMeta";
import { currencyFormat, shortDate } from "../../lib/util";
import { GAMES } from "../../lib/games";
import { tierLabel } from "../watchlist/grades";
import SortTh from "../../app/shared/components/SortTh";
import AppPagination from "../../app/shared/components/AppPagination";
import CardLoader from "../../app/shared/components/CardLoader";

const PAGE_SIZE = 25;

// One shared list (portfolio or watchlist): server-side game filter, column
// sort, and pagination — profiles with big collections stay fast.
function CardsSection({ handle, list, title, showQty }: {
    handle: string; list: 'portfolio' | 'watchlist'; title: string; showQty?: boolean;
}) {
    const [game, setGame] = useState('all');
    const [orderBy, setOrderBy] = useState('valueDesc');
    const [page, setPage] = useState(1);
    const { data, isFetching } = useFetchPublicCardsQuery({
        handle, list, game, orderBy, pageNumber: page, pageSize: PAGE_SIZE,
    });

    const sort = (v: string) => { setOrderBy(v); setPage(1); };
    const pickGame = (g: string) => { setGame(g); setPage(1); };
    const rows = data?.items ?? [];
    const total = data?.pagination?.totalCount;

    return (
        <section className="pub-section">
            <div className="pub-section__head">
                <h2>{title}{total != null && (
                    <span className="est-note"> · {total} card{total === 1 ? '' : 's'}</span>
                )}</h2>
                <div className="range-tabs" role="group" aria-label="Game">
                    {[{ value: 'all', label: 'ALL' }, ...GAMES].map(g => (
                        <button key={g.value}
                            className={`btn btn--outline range-tab${g.value === game ? ' btn--active' : ''}`}
                            onClick={() => pickGame(g.value)}>
                            {g.label.toUpperCase()}
                        </button>
                    ))}
                </div>
            </div>
            {rows.length === 0 ? (
                <p className="est-note">
                    {isFetching ? 'Loading…'
                        : game === 'all' ? 'Nothing here yet.' : 'No cards for this game.'}
                </p>
            ) : (
                <>
                    <div className="screener-wrap">
                        <table className="screener">
                            <thead>
                                <tr>
                                    <th aria-label="Card image" />
                                    <SortTh label="Card" k="name" ascFirst
                                        orderBy={orderBy} onSort={sort} />
                                    <SortTh label="Set" k="set" ascFirst
                                        orderBy={orderBy} onSort={sort} />
                                    <SortTh label="Condition" k="condition" className="screener__mid"
                                        orderBy={orderBy} onSort={sort} />
                                    {showQty && <SortTh label="Qty" k="qty" className="screener__mid"
                                        orderBy={orderBy} onSort={sort} />}
                                    <SortTh label="Value" k="value" className="screener__num"
                                        orderBy={orderBy} onSort={sort} />
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map(r => (
                                    <tr key={`${r.game}-${r.productId}-${r.grade}`} className="screener__row">
                                        <td className="screener__thumbcell">
                                            <img className="screener__thumb" src={r.pictureUrl} alt="" loading="lazy" />
                                        </td>
                                        <td>
                                            <Link className="screener__name" to={`/catalog/${r.game}/${r.productId}`}>
                                                {r.name}
                                            </Link>
                                        </td>
                                        <td><span className="mono">{r.setName}</span></td>
                                        <td className="screener__mid"><span className="owned-condition">{tierLabel(r.grade === 'ungraded' ? undefined : r.grade)}</span></td>
                                        {showQty && <td className="screener__mid">{r.quantity}</td>}
                                        <td className="screener__num screener__price">
                                            {r.price != null ? currencyFormat(r.price * r.quantity) : '—'}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    {data?.pagination && data.pagination.totalPages > 1 && (
                        <AppPagination metadata={data.pagination} scrollToTop={false}
                            onPageChange={setPage} />
                    )}
                </>
            )}
        </section>
    );
}

export default function PublicProfilePage() {
    const { handle = '' } = useParams();
    const { data: profile, isLoading, isError } = useFetchPublicProfileQuery(handle, { skip: !handle });
    usePageMeta(profile ? `@${profile.handle}` : 'Profile',
        profile ? `${profile.handle}'s public card collection on cardstock.` : undefined);

    if (isLoading) return <CardLoader />;
    if (isError || !profile) return (
        <p className="full-span est-note">This profile doesn't exist or isn't public.</p>
    );

    const showsPortfolio = profile.portfolioCount != null;
    const showsWatchlist = profile.watchlistCount != null;

    return (
        <div className="full-span pub-profile">
            <header className="pub-profile__head">
                {profile.avatarUrl
                    ? <img className="avatar avatar--lg" src={profile.avatarUrl} alt="" />
                    : <div className="avatar avatar--lg avatar--empty">{profile.handle[0]?.toUpperCase()}</div>}
                <div>
                    <h1>@{profile.handle}</h1>
                    <p className="est-note">Collecting since {shortDate(profile.joined)}</p>
                    {profile.storefrontUrl && (
                        <a className="btn btn--outline pub-profile__store" href={profile.storefrontUrl}
                            target="_blank" rel="noreferrer nofollow">
                            Visit storefront ↗
                        </a>
                    )}
                </div>
                {profile.totalValue != null && (
                    <div className="pub-profile__value">
                        <span className="est-note mono">PORTFOLIO VALUE</span>
                        <strong>{currencyFormat(profile.totalValue)}</strong>
                    </div>
                )}
            </header>

            {showsPortfolio && <CardsSection handle={handle} list="portfolio" title="Portfolio" showQty />}
            {showsWatchlist && <CardsSection handle={handle} list="watchlist" title="Watchlist" />}
            {!showsPortfolio && !showsWatchlist && (
                <p className="est-note">@{profile.handle} keeps their collection private.</p>
            )}
        </div>
    );
}
