import { PROMO, promoLive } from "../../../lib/promo";
import { trackPromoClick } from "../../../lib/pageview";

// The site's one TCGplayer banner (see lib/promo.ts). Renders nothing once
// the promotion has ended.
export default function TcgPromo() {
    if (!promoLive()) return null;
    return (
        <aside className="tcg-promo container" aria-label="Advertisement">
            <span className="tcg-promo__tag mono">Advertisement</span>
            <a href={PROMO.href} target="_blank" rel="sponsored noopener"
                title="TCGplayer (affiliate link; CardStock may earn a commission)"
                onClick={() => trackPromoClick(PROMO.id)}
                onAuxClick={e => { if (e.button === 1) trackPromoClick(PROMO.id); }}>
                <picture>
                    <source media="(max-width: 600px)" srcSet={PROMO.narrow} width={320} height={50} />
                    <img src={PROMO.wide} width={970} height={90} loading="lazy" decoding="async"
                        alt={PROMO.alt} />
                </picture>
            </a>
        </aside>
    );
}
