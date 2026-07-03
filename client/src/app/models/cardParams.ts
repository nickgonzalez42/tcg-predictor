export type CardParams = {
    game: string;
    orderBy: string;
    searchTerm?: string;
    sets: string[];
    rarities: string[];
    grade?: string;
    pageNumber: number;
    pageSize: number;
}
