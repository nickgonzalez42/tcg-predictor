// Absolute URL of a card's art. Prod art lives on the CloudFront CDN
// (VITE_CARD_IMG_BASE, mirroring the server's CardImages:BaseUrl); dev,
// where the scraped files are local, uses the API's own /card-images route.
// VITE_API_URL ends in /api; images live at the host root.
const CDN = import.meta.env.VITE_CARD_IMG_BASE as string | undefined;
const API_ORIGIN = (import.meta.env.VITE_API_URL as string).replace(/\/api\/?$/, '');

export const cardImageUrl = (game: string, productId: number) =>
    CDN ? `${CDN}/${game}/${productId}.jpg`
        : `${API_ORIGIN}/card-images/${game}/${productId}.jpg`;
