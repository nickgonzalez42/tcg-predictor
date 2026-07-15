// Absolute URL of a card's art served by the API (mirrors the server's
// CardImageUrl). VITE_API_URL ends in /api; images live at the host root.
const API_ORIGIN = (import.meta.env.VITE_API_URL as string).replace(/\/api\/?$/, '');

export const cardImageUrl = (game: string, productId: number) =>
    `${API_ORIGIN}/card-images/${game}/${productId}.jpg`;
