import { useEffect, useRef, useState } from "react"
import type { Card } from "../../app/models/card"
import { Link } from "react-router-dom"
import { gameKey } from "../../lib/util"
import TrackButton from "../watchlist/TrackButton"
import { tierLabel } from "../watchlist/grades"
import ChangePill from "../../app/shared/components/ChangePill"
import Sparkline from "../../app/shared/components/Sparkline"
import PricePair from "../../app/shared/components/PricePair"
import { cardBackSrc, fallbackToCardBack } from "../../lib/cardImages";

type Props = {
    card: Card
    ownGrade?: string   // condition the quick "Own" add defaults to
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
        const sync = () => { rotator.style.height = `${media.clientHeight + 1}px` }
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
                        onError={e => fallbackToCardBack(e, card.game, card.cardType)}
                    />
                </Link>
                <div className={`card__rotator${active ? ' active' : ''}`}>
                    <div className="rotator" ref={rotatorRef}>
                        <div className="rotator__condition">{tierLabel(ownGrade)}</div>
                        <div className="card3d">
                            <div className="card3d__inner">
                                <img
                                    className="card3d__face"
                                    src={card.pictureUrl}
                                    alt=""
                                    onError={e => fallbackToCardBack(e, card.game, card.cardType)}
                                />
                                <div className="card3d__core"></div>
                                <img className="card3d__face card3d__face--back" src={cardBackSrc(card.game, card.cardType)} alt="" />
                            </div>
                        </div>
                    </div>
                    {/* Anchored to the reveal box (not the fixed-height, 3D-spinning
                        .rotator): iOS Safari clips the bottom of a preserve-3d layer,
                        which hid these buttons on iPhone. */}
                    <div className="rotator__actions">
                        <TrackButton game={gameKey(card.game)} productId={card.id} ownGrade={ownGrade} compact />
                    </div>
                </div>
            </div>

            <div className="card__body">
                {/* Title + set at the top of the body; price row + footer sit at
                    the bottom (card__main is pushed down). */}
                <div className="card__head">
                    <Link className="card__title" to={detailPath} style={{ display: 'block' }}>{card.name}</Link>
                    {card.setName && <div className="card__set">{card.setName}</div>}
                </div>
                <div className="card__main">
                <div className="card__row">
                    <div className="card__info">
                        <div className="card__price">
                            {/* no asOf: catalog tiles omit the price date (it lives on the card page) */}
                            <PricePair
                                price={card.price}
                                forecast={card.fcstTo}
                                horizon={(card.fcstHorizon ?? '12m').toUpperCase()}
                            />
                        </div>
                    </div>
                    <button className="btn btn--outline card__add" onClick={() => setActive(a => !a)}
                        aria-pressed={active} title="Show / hide actions">
                        ＋
                    </button>
                </div>
                {/* Past-movement pill + sparkline share one line. */}
                <div className="card__footer">
                    {(card.trendPct != null || (card.sparkline?.length ?? 0) >= 2) && (
                        <div className="card__market"
                            title={`Price history over the past ${(card.trendPeriod ?? '1m').toUpperCase()}`}>
                            {card.trendPct != null && (
                                <ChangePill value={card.trendPct}
                                    title={`Price change over the past ${(card.trendPeriod ?? '1m').toUpperCase()}`} />
                            )}
                            {card.trendPct != null && (
                                <span className="mono">PAST {(card.trendPeriod ?? '1m').toUpperCase()}</span>
                            )}
                            {(card.sparkline?.length ?? 0) >= 2 && (
                                <Sparkline points={card.sparkline} />
                            )}
                        </div>
                    )}
                </div>
                </div>
            </div>
        </div>
    )
}
