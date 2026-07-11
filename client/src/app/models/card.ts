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
    // Market context for tiles / screener rows, computed for the shown condition
    // tier over the requested trend window (1w|1m|6m|1y).
    priceAsOf?: string          // date of the shown price's latest history point
    sparkline?: number[]        // prices inside the trend window, oldest first
    historyMonths?: number      // months of history, full series (confidence proxy)
    trendPct?: number           // % change across the window
    trendPeriod?: string        // normalized window this was computed for
    fcst6Pct?: number           // 6m forecast % change
    fcst12Pct?: number          // 12m forecast % change
    fcst12To?: number           // 12m forecast price
    fcstTo?: number             // forecast price matched to the trend window
    fcstHorizon?: string        // '1w' | '1m' | '6m' | '12m'
    fcstConfidence?: string     // model-reported: high | med | low
    // Wishlist rows only.
    watchedAtPrice?: number     // price when the card was wishlisted
    alertTargetPrice?: number   // "notify at or below" price
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
    confidence?: string  // model-reported: high | med | low
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
    updatedAt?: string   // when the PriceCharting snapshot was taken
}
