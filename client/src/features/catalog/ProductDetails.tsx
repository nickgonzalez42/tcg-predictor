import { useParams } from "react-router-dom"
import { useFetchProductDetailsQuery } from "./catalogApi";
import { currencyFormat } from "../../lib/util";

export default function ProductDetails() {
    const { id } = useParams();
    const { data: product, isLoading } = useFetchProductDetailsQuery(id ? +id : 0)

    if (isLoading || !product) return <div>Is loading...</div>

    const productDetails = [
        { label: 'Name', value: product.name },
        { label: 'Description', value: product.description },
        { label: 'Type', value: product.type },
        { label: 'Brand', value: product.brand },
        { label: 'Quantity in stock', value: product.quantityInStock }
    ]

    return (
        <div className="detail">
            <div>
                <img src={product.pictureUrl} alt={product.name} />
            </div>
            <div>
                <h3>{product.name}</h3>
                <hr className="divider" />
                <div className="card__price" style={{ fontSize: '2rem' }}>{currencyFormat(product.price)}</div>
                <table className="detail-table">
                    <tbody>
                        {productDetails.map((detail, index) => (
                            <tr key={index}>
                                <td>{detail.label}</td>
                                <td>{detail.value}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    )
}
