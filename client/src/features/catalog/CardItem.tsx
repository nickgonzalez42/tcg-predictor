import { useEffect, useRef, useState } from "react"
import type { Card } from "../../app/models/card"
import { Link } from "react-router-dom"
import { currencyFormat, gameKey } from "../../lib/util"
import TrackButton from "../watchlist/TrackButton"
import { conditionLabel } from "../watchlist/grades"
import ExpectedChange from "./ExpectedChange"

type Props = {
    card: Card
    ownGrade?: string   // condition the quick "Own" add defaults to
}

// Back-of-card art: One Piece leaders and DON!! cards have their own backs;
// all other One Piece cards share the standard back, Pokémon has its own.
function cardBackSrc(card: Card) {
    if (gameKey(card.game) !== 'onepiece') return '/images/pokemon-back.jpg';
    if (card.cardType === 'Leader') return '/images/one-piece-leader-back.png';
    if (card.cardType === 'DON!!') return '/images/one-piece-don-card-back.jpg';
    return '/images/one-piece-card-back.jpg';
}

export default function CardItem({ card, ownGrade }: Props) {
    const [active, setActive] = useState(false)
    const mediaRef = useRef<HTMLDivElement>(null)
    const rotatorRef = useRef<HTMLDivElement>(null)

    const detailPath = `/catalog/${gameKey(card.game)}/${card.id}`

    // The reveal animates .card__rotator's max-height, so the inner .rotator needs a
    // fixed pixel height equal to the media container (a % would collapse with the
    // parent). A ResizeObserver keeps it in sync on first paint, image load, and resize.
    useEffect(() => {
        const media = mediaRef.current
        const rotator = rotatorRef.current
        if (!media || !rotator) return
        const sync = () => { rotator.style.height = `${media.clientHeight}px` }
        sync()
        const ro = new ResizeObserver(sync)
        ro.observe(media)
        return () => ro.disconnect()
    }, [])

    return (
        <div className="card">
            <div className="media__container" ref={mediaRef}>
                <Link to={detailPath} style={{ display: 'block' }}>
                    <img
                        className="card__media"
                        style={{ width: '100%', objectFit: 'contain' }}
                        src={card.pictureUrl}
                        alt={card.name}
                        onError={(e) => {
                            const img = e.currentTarget;
                            if (card.imageUrl && img.src !== card.imageUrl) img.src = card.imageUrl;
                            else img.onerror = null;
                        }}
                    />
                </Link>
                <div className={`card__rotator${active ? ' active' : ''}`}>
                    <div className="rotator" ref={rotatorRef}>
                        <div className="rotator__condition">{conditionLabel(ownGrade || 'nm')}</div>
                        <div className="card3d">
                            <div className="card3d__inner">
                                <img
                                    className="card3d__face"
                                    src={card.pictureUrl}
                                    alt=""
                                    onError={(e) => {
                                        const img = e.currentTarget;
                                        if (card.imageUrl && img.src !== card.imageUrl) img.src = card.imageUrl;
                                        else img.onerror = null;
                                    }}
                                />
                                <div className="card3d__core"></div>
                                <img className="card3d__face card3d__face--back" src={cardBackSrc(card)} alt="" />
                            </div>
                        </div>
                        <div className="rotator__actions">
                            <TrackButton game={gameKey(card.game)} productId={card.id} ownGrade={ownGrade} compact />
                        </div>
                    </div>
                </div>
            </div>

            <div className="card__body">
                <div className="card__info">
                    <Link className="card__title" to={detailPath} style={{ display: 'block' }}>{card.name}</Link>
                    {card.setName && <div className="card__set">{card.setName}</div>}
                    <div className="card__price">
                        {card.expectedChange != null
                            ? <ExpectedChange card={card} />
                            : card.price != null ? currencyFormat(card.price) : '—'}
                    </div>
                </div>
                <button className="btn btn--outline card__add" onClick={() => setActive(a => !a)}
                    aria-pressed={active} title="Show / hide actions">
                    ＋
                </button>
            </div>
        </div>
    )
}
