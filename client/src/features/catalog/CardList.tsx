import type { Card } from "../../app/models/card"
import CardItem from "./CardItem"

type Props = {
    cards: Card[]
}

export default function CardList({ cards }: Props) {
  return (
    <div className="product-grid subgrid full-span">
        {cards.map(card => (
          <CardItem card={card} key={card.id} />
        ))}
    </div>
  )
}
