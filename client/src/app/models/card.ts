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
    ownedCopies?: OwnedCopy[]   // present only in the Owned list; the copies at ownedGrade
    ownedGrade?: string         // Owned list: the condition this tile represents ('' -> undefined)
    ownedQuantity?: number      // Owned list: number of copies at that condition
    expectedChange?: number     // set only when sorting by forecast: the sorted metric's value
    expectedUnit?: 'percent' | 'usd'
    expectedHorizon?: string    // '6m' | '12m'
    expectedFrom?: number       // current (forecast base) price
    expectedTo?: number         // forecast price
}

// One owned physical copy of a card (grade/purchase detail all optional).
export type OwnedCopy = {
    id: number
    grade?: string
    purchasePrice?: number
    acquiredAt?: string
    note?: string
    addedAt: string
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
    reason?: string
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
