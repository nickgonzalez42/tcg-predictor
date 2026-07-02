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
    pictureUrl?: string
    imageUrl?: string
    attributes: Record<string, string>
    gradedPrices?: GradedPrices
}

export type Forecast = {
    target: string
    horizon: string
    asOf?: string
    basePrice: number
    forecastPrice: number
    low: number
    high: number
    ret: number
    months?: number
}

export type GradedPrices = {
    ungraded?: number
    grade7?: number
    grade8?: number
    grade9?: number
    grade95?: number
    psa10?: number
    bgs10?: number
    cgc10?: number
    sgc10?: number
    salesVolume?: number
}
