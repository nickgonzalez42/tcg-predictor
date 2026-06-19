import type { Product } from "../../app/models/product"
import ProductCard from "./ProductCard"

type Props = {
    products: Product[]
}

export default function ProductList({ products }: Props) {
  return (
    <div className="product-grid">
        {products.map(product => (
          <ProductCard product={product} key={product.id} />
        ))}
    </div>
  )
}
