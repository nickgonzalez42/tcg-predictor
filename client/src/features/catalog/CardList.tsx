import type { Card } from "../../app/models/card"
import CardItem from "./CardItem"

type Props = {
    cards: Card[]
    ownGrade?: string   // condition a card's quick "Own" add defaults to
}

export default function CardList({ cards, ownGrade }: Props) {
  return (
    <div className="product-grid subgrid full-span">
        {cards.map(card => (
          <CardItem card={card} ownGrade={ownGrade} key={card.id} />
        ))}
    </div>
  )
}
