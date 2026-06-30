export type Card = {
    id: number
    name: string
    game: string
    setName?: string
    rarity?: string
    cardNumber?: string
    cardType?: string
    description?: string
    price?: number
    predictedPrice?: number
    usedImage?: boolean
    pictureUrl?: string
    imageUrl?: string
    attributes: Record<string, string>
}
