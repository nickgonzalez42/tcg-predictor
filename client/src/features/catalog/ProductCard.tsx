import type { Product } from "../../app/models/product"
import { Link } from "react-router-dom"
import { currencyFormat } from "../../lib/util"

type Props = {
    product: Product
}

export default function ProductCard({ product }: Props) {
    return (
        <div className="card">
            <div
                className="card__media"
                style={{ backgroundImage: `url(${product.pictureUrl})` }}
                title={product.name}
            />
            <div className="card__body">
                <div className="card__title">{product.name}</div>
                <div className="card__price">{currencyFormat(product.price)}</div>
            </div>
            <div className="card__actions">
                <Link className="btn btn--outline" to={`/catalog/${product.id}`}>View</Link>
            </div>
        </div>
    )
}
