import type { Card } from "../../app/models/card"
import CardItem from "./CardItem"

type Props = {
    cards: Card[]
    ownGrade?: string   // condition a card's quick "Own" add defaults to
    quick?: boolean
}

export default function CardList({ cards, ownGrade, quick }: Props) {
  return (
    <div className="product-grid subgrid full-span">
        {cards.map(card => (
          <CardItem card={card} ownGrade={ownGrade} quick={quick} key={card.id} />
        ))}
    </div>
  )
}
