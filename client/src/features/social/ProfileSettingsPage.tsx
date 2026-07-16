import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import Modal from "../../app/shared/components/Modal";
import { usePageMeta } from "../../lib/usePageMeta";
import { useFetchMyProfileQuery, useUpdateProfileMutation } from "./socialApi";
import { useFetchWatchlistQuery } from "../watchlist/watchlistApi";
import { useFetchCardsQuery } from "../catalog/catalogApi";
import { useUserInfoQuery } from "../account/accountApi";
import { gameKey } from "../../lib/util";
import { GAMES } from "../../lib/games";
import { cardImageUrl } from "../../lib/cardImageUrl";
import CardLoader from "../../app/shared/components/CardLoader";

// Pick any card as the profile image (its art becomes the avatar): search the
// full catalog by name (capped at 50 results), or grab one of your tracked cards.
function AvatarPicker({ onPick, onClose }: {
    onPick: (game: string, productId: number) => void; onClose: () => void;
}) {
    const { data: tracked } = useFetchWatchlistQuery();
    const unique = [...new Map((tracked ?? []).map(t =>
        [`${t.game}-${t.productId}`, t])).values()];

    const [game, setGame] = useState('pokemon');
    const [term, setTerm] = useState('');
    const [debounced, setDebounced] = useState('');
    useEffect(() => {
        const t = setTimeout(() => setDebounced(term.trim()), 300);
        return () => clearTimeout(t);
    }, [term]);

    const searching = debounced.length >= 2;
    const { data: results, isFetching } = useFetchCardsQuery({
        game, orderBy: '', searchTerm: debounced, sets: [], rarities: [],
        pageNumber: 1, pageSize: 50,
    }, { skip: !searching });

    return (
        <Modal title="Choose a card as your profile image" onClose={onClose}>
            <div className="avatar-picker__search">
                <select className="input" value={game} aria-label="Game"
                    onChange={e => setGame(e.target.value)}>
                    {GAMES.map(g => <option key={g.value} value={g.value}>{g.label}</option>)}
                </select>
                <input className="input" type="search" placeholder="Search cards by name…"
                    value={term} onChange={e => setTerm(e.target.value)} autoFocus />
            </div>
            {searching ? (
                isFetching && !results ? (
                    <p className="est-note">Searching…</p>
                ) : !results?.items.length ? (
                    <p className="est-note">No cards match “{debounced}”.</p>
                ) : (
                    <div className="avatar-picker">
                        {results.items.map(c => (
                            <button key={`${game}-${c.id}`} className="avatar-picker__card"
                                title={c.name} onClick={() => onPick(game, c.id)}>
                                <img loading="lazy" alt={c.name}
                                    src={c.pictureUrl ?? cardImageUrl(game, c.id)} />
                            </button>
                        ))}
                    </div>
                )
            ) : unique.length === 0 ? (
                <p className="est-note">
                    Search for any card above, or track some cards. Anything in your
                    portfolio or watchlist shows up here.
                </p>
            ) : (
                <>
                    <p className="est-note">Your tracked cards, or search above for any card.</p>
                    <div className="avatar-picker">
                        {unique.map(t => (
                            <button key={`${t.game}-${t.productId}`} className="avatar-picker__card"
                                onClick={() => onPick(gameKey(t.game), t.productId)}>
                                <img loading="lazy" alt=""
                                    src={cardImageUrl(gameKey(t.game), t.productId)} />
                            </button>
                        ))}
                    </div>
                </>
            )}
        </Modal>
    );
}

export default function ProfileSettingsPage() {
    usePageMeta("Profile settings");
    const { data: user } = useUserInfoQuery();
    const { data: profile, isLoading } = useFetchMyProfileQuery(undefined, { skip: !user });
    const [save, { isLoading: saving, error }] = useUpdateProfileMutation();

    const [handle, setHandle] = useState('');
    const [isPublic, setIsPublic] = useState(false);
    const [showPortfolio, setShowPortfolio] = useState(false);
    const [showWatchlist, setShowWatchlist] = useState(false);
    const [storefront, setStorefront] = useState('');
    const [avatar, setAvatar] = useState<{ game: string; productId: number } | null>(null);
    const [avatarUrl, setAvatarUrl] = useState<string | null>(null);
    const [picking, setPicking] = useState(false);
    const [saved, setSaved] = useState(false);

    useEffect(() => {
        if (!profile) return;
        setHandle(profile.handle ?? '');
        setIsPublic(profile.profilePublic);
        setShowPortfolio(profile.showPortfolio);
        setShowWatchlist(profile.showWatchlist);
        setStorefront(profile.storefrontUrl ?? '');
        setAvatar(profile.avatarGame && profile.avatarProductId
            ? { game: profile.avatarGame, productId: profile.avatarProductId } : null);
        setAvatarUrl(profile.avatarUrl);
    }, [profile]);

    if (!user) return <p className="full-span est-note">Sign in to edit your profile.</p>;
    if (isLoading || !profile) return <CardLoader />;

    const submit = async () => {
        setSaved(false);
        try {
            await save({
                handle: handle.trim() || null,
                profilePublic: isPublic,
                showPortfolio,
                showWatchlist,
                storefrontUrl: storefront.trim() || null,
                avatarGame: avatar?.game ?? null,
                avatarProductId: avatar?.productId ?? null,
            }).unwrap();
            setSaved(true);
        } catch { /* error surfaces below */ }
    };

    const apiError = (error as { data?: string } | undefined)?.data;

    return (
        <div className="full-span profile-settings">
            <div className="profile-settings__header">
                <h1>Profile</h1>
                {isPublic && handle.trim() && (
                    <Link className="btn btn--outline" to={`/u/${handle.trim()}`}>
                        View public profile ↗
                    </Link>
                )}
            </div>

            <div className="panel profile-settings__panel">
                <div className="profile-settings__avatar">
                    {avatarUrl || avatar ? (
                        <img className="avatar avatar--lg" alt="Profile"
                            src={avatar ? cardImageUrl(avatar.game, avatar.productId) : avatarUrl!} />
                    ) : (
                        <div className="avatar avatar--lg avatar--empty">?</div>
                    )}
                    <button className="btn btn--outline" onClick={() => setPicking(true)}>
                        Choose a card…
                    </button>
                    {avatar && (
                        <button className="btn btn--outline" onClick={() => { setAvatar(null); setAvatarUrl(null); }}>
                            Clear
                        </button>
                    )}
                </div>

                <label className="field-label" htmlFor="handle">Username</label>
                <input id="handle" className="input" maxLength={24} placeholder="e.g. cardshark_42"
                    value={handle} onChange={e => setHandle(e.target.value)} />
                <p className="est-note">3–24 characters: letters, numbers, underscores. Shown on
                    comments and your public profile. Your email is never shown.</p>

                <label className="field-label" htmlFor="storefront">Storefront link</label>
                <input id="storefront" className="input" type="url"
                    placeholder="https://www.ebay.com/usr/yourstore"
                    value={storefront} onChange={e => setStorefront(e.target.value)} />
                <p className="est-note">Your eBay or TCGplayer store, linked from your public profile.</p>

                <div className="profile-settings__toggles">
                    <label>
                        <input type="checkbox" checked={isPublic}
                            onChange={e => setIsPublic(e.target.checked)} />
                        Public profile {handle.trim() && <span className="est-note">· /u/{handle.trim()}</span>}
                    </label>
                    <label className={isPublic ? '' : 'profile-settings__sub'}>
                        <input type="checkbox" disabled={!isPublic} checked={showWatchlist}
                            onChange={e => setShowWatchlist(e.target.checked)} />
                        Show my watchlist
                    </label>
                    <label className={isPublic ? '' : 'profile-settings__sub'}>
                        <input type="checkbox" disabled={!isPublic} checked={showPortfolio}
                            onChange={e => setShowPortfolio(e.target.checked)} />
                        Show my portfolio
                    </label>
                </div>

                {apiError && <p className="profile-settings__error">{String(apiError)}</p>}
                {saved && <p className="est-note">Saved ✓{isPublic && handle.trim() && (
                    <>. View it at <Link to={`/u/${handle.trim()}`}>/u/{handle.trim()}</Link></>
                )}</p>}
                <button className="btn" disabled={saving} onClick={submit}>Save profile</button>
            </div>

            {picking && (
                <AvatarPicker onClose={() => setPicking(false)}
                    onPick={(game, productId) => { setAvatar({ game, productId }); setAvatarUrl(null); setPicking(false); }} />
            )}
        </div>
    );
}
