import type { CSSProperties } from "react";
import { Link } from "react-router-dom";
import { useFetchArticlesQuery, useFetchCatalogCountQuery, type Article, type ArticleSummary } from "./articlesApi";
import { useUserInfoQuery } from "../account/accountApi";
import { GAME_LABEL, gameColor } from "../../lib/games";
import { useFetchMoversQuery } from "../catalog/catalogApi";
import { currencyFormat } from "../../lib/util";
import { fallbackToCardBack } from "../../lib/cardImages";
import { longDate } from "../reports/reportKind";
import { articleKindLabel } from "./articleKind";

// The reader's next steps on an article (2026-11-13). Thirteen readers came
// from Reddit to the One Piece Heroines article in a day and every one left
// without opening a card, the catalog or the home page: nothing on the page
// asked them to. After the opening section, every card the piece is about,
// in the catalog with our forecasts; after the piece, those cards again, a
// free portfolio for the cards you own and more articles on the game. The
// static copy of every article carries the same two blocks with the same
// words (pipeline/article_pages.py + static_nav.py) and reads the same
// stylesheet, ArticleNext.css.

const MORE_N = 3;

type Browse = {
    href: string;          // into the catalog
    set: string | null;    // the article's set; null for the whole game
    label: string;         // the game's name
    count: number | null;  // cards the catalog lists there, once it says
};

// The cards an article is about: its set for a set report, the whole game for
// a game report or a new-releases piece. A set whose name carries a comma
// can't ride the catalog's comma-separated sets filter, so it gets the whole
// game (game_articles.py skips those links too). Both blocks ask; the count
// is fetched once.
function useBrowse(article: Article): Browse {
    const set = article.kind === 'set' && article.focus !== 'recent'
        && article.setName && !article.setName.includes(',') ? article.setName : null;
    const { data: count } = useFetchCatalogCountQuery({ game: article.game, set });
    return {
        href: `/catalog?game=${article.game}&sets=${set ? encodeURIComponent(set) : 'all'}`,
        set,
        label: GAME_LABEL[article.game] ?? article.game,
        count: count ?? null,
    };
}

// "all 98 cards in this set", "all 2,185 Gundam cards", the number in the
// brand yellow; "every card" until the catalog has answered.
function Cards({ browse, accent }: { browse: Browse; accent: string }) {
    const game = browse.set ? '' : `${browse.label} `;
    const where = browse.set ? ' in this set' : '';
    if (browse.count && browse.count > 1)
        return <>all <span className={accent}>{browse.count.toLocaleString('en-US')} {game}cards</span>{where}</>;
    return <>every {game}card{where}</>;
}

function browseCopy(browse: Browse) {
    return browse.set
        ? `Today's price, the price history and our forecast for every card in ${browse.set}.`
        : `Today's price, the price history and our forecast for every ${browse.label} card we track.`;
}

// The game's own advert inside the block (2026-11-15): the four cards of the
// article's game we expect to move most next month, art first, each a link
// to its card page. The same request the game's home page makes, so it is
// already warm; nothing shows until it answers, or if it has no cards.
const AD_CARDS = 4;
function GameMovers({ game }: { game: string }) {
    const { data } = useFetchMoversQuery({ count: 24, horizon: '1m', trend: '6m', game });
    const cards = (data ?? []).filter(c => c.price && c.fcstTo != null && c.pictureUrl).slice(0, AD_CARDS);
    if (cards.length < 2) return null;
    return (
        <div className="article-browse__cards">
            <span className="article-browse__cards-label">
                The {GAME_LABEL[game] ?? game} cards we expect to move most next month
            </span>
            {cards.map(c => {
                const pct = (c.fcstTo! / c.price! - 1) * 100;
                const flat = Math.abs(pct) < 0.5;
                return (
                    <Link key={c.id} className="article-browse__card" to={`/catalog/${game}/${c.id}`}>
                        <img className="article-browse__art" src={c.pictureUrl} alt="" loading="lazy"
                            onError={e => fallbackToCardBack(e, c.game, c.cardType)} />
                        <span className="article-browse__card-name">{c.name}</span>
                        <span className="article-browse__card-price">
                            {currencyFormat(c.price!)}{' '}
                            <span className={`article-browse__move${flat ? '' : pct > 0 ? ' article-browse__move--up' : ' article-browse__move--down'}`}>
                                {flat ? '0' : `${pct > 0 ? '+' : ''}${pct.toFixed(0)}`}%
                            </span>
                        </span>
                    </Link>
                );
            })}
        </div>
    );
}

// After the opening section.
export function ArticleBrowse({ article }: { article: Article }) {
    const browse = useBrowse(article);
    return (
        <aside className="article-browse" aria-label="Every card in this piece"
            style={{ '--game': gameColor(article.game) } as CSSProperties}>
            <div className="article-browse__text">
                <span className="article-browse__kicker">
                    {browse.set ? 'This set on CardStock' : `${browse.label} on CardStock`}
                </span>
                <span className="article-browse__title">
                    See <Cards browse={browse} accent="article-browse__count" /> with our forecasts
                </span>
                <span className="article-browse__copy">{browseCopy(browse)}</span>
            </div>
            <Link className="article-browse__btn" to={browse.href}>
                {browse.set ? 'Browse the set' : 'Browse the cards'}
            </Link>
            <GameMovers game={article.game} />
        </aside>
    );
}

// Up to MORE_N other articles on the same game, newest first; with none yet,
// the newest from as many other games instead.
function useMore(article: Article) {
    const { data: same } = useFetchArticlesQuery({ game: article.game, limit: MORE_N + 1 });
    const mine = (same?.items ?? []).filter(a => a.slug !== article.slug).slice(0, MORE_N);
    const { data: recent } = useFetchArticlesQuery({ limit: 12 }, { skip: !same || mine.length > 0 });
    const label = GAME_LABEL[article.game] ?? article.game;
    const meta = (a: ArticleSummary, withGame: boolean) => [
        longDate(a.publishedAt),
        withGame ? GAME_LABEL[a.game] ?? a.game : null,
        articleKindLabel(a.kind, false, a.focus),
    ].filter(Boolean).join(' · ');
    if (mine.length) return {
        title: `More from ${label}`, all: `/articles?game=${article.game}`, allText: `Every ${label} article`,
        items: mine.map(a => ({ slug: a.slug, title: a.title, meta: meta(a, false) })),
    };
    const newest = new Map<string, ArticleSummary>();
    for (const a of recent?.items ?? [])
        if (a.slug !== article.slug && !newest.has(a.game)) newest.set(a.game, a);
    return {
        title: 'More articles', all: '/articles', allText: 'All articles',
        items: [...newest.values()].slice(0, MORE_N).map(a => ({ slug: a.slug, title: a.title, meta: meta(a, true) })),
    };
}

// After the article.
export function ArticleNext({ article }: { article: Article }) {
    const browse = useBrowse(article);
    const { data: user } = useUserInfoQuery();
    const more = useMore(article);
    return (
        <section className="article-next" aria-labelledby="article-next-title">
            <span className="article-next__kicker">Keep going</span>
            <h2 className="article-next__title" id="article-next-title">Where to next</h2>
            <div className="article-next__steps">
                <Link className="article-next__step" to={browse.href}>
                    <span className="article-next__num">1</span>
                    <span className="article-next__step-title">
                        See <Cards browse={browse} accent="article-next__count" />
                    </span>
                    <span className="article-next__step-copy">{browseCopy(browse)}</span>
                    <span className="article-next__go">{browse.set ? 'Browse the set' : 'Browse the cards'} &rarr;</span>
                </Link>
                <Link className="article-next__step" to={user ? '/portfolio' : '/register'}>
                    <span className="article-next__num">2</span>
                    <span className="article-next__step-title">Track the cards you own</span>
                    <span className="article-next__step-copy">
                        Add them to a free portfolio and see what your collection is worth today, with our
                        forecast beside every card.
                    </span>
                    <span className="article-next__go">
                        {user ? 'Open your portfolio' : 'Start a free portfolio'} &rarr;
                    </span>
                </Link>
            </div>
            {more.items.length > 0 && (
                <div className="article-next__more">
                    <div className="article-next__more-head">
                        <h3 className="article-next__more-title">{more.title}</h3>
                        <Link className="article-next__all" to={more.all}>{more.allText} &rarr;</Link>
                    </div>
                    <ul className="article-next__list">
                        {more.items.map(a => (
                            <li key={a.slug}>
                                <Link className="article-next__item" to={`/articles/${a.slug}`}>
                                    <span className="article-next__item-title">{a.title}</span>
                                    <span className="article-next__item-meta">{a.meta}</span>
                                </Link>
                            </li>
                        ))}
                    </ul>
                </div>
            )}
        </section>
    );
}
