import type { Card } from "../app/models/card";
import { gameKey } from "./util";

// Which cards physically carry a holo/foil finish, per game (2026-10-14).
// Drives the holographic hover effect in the catalog: only cards that are
// actually foil in real life get the shine. Rules are deliberately plain
// string checks against each game's rarity vocabulary (see the per-game
// rarity counts in the card DBs) so they are easy to tune game by game.
// A printing that says holo/foil wins regardless of rarity (Pokemon
// Reverse Holofoil, Magic Foil, Star Wars Hyperspace Foil, and friends).

const RARITY_RULES: Record<string, RegExp> = {
    // Holo Rare and up; Promo included (nearly all Pokemon promos are holo).
    pokemon: /holo|ultra|secret|double rare|illustration|hyper|radiant|amazing|prism|shiny|gold|rainbow|ace spec|promo/,
    // Rare and above are foil in One Piece, as are Leaders and most promos.
    onepiece: /^rare$|super|secret|treasure|leader|promo|^sp/,
    // Magic foils are printings, never rarities.
    magic: /^$/,
    // Foil-only rarities; everything else foils via printing variants.
    lorcana: /enchanted|epic|iconic/,
    // Digimon Rare and above have a foil finish.
    digimon: /^rare$|super|secret|special|ultimate|ultra/,
    // Gundam: R/LR/P tiers and any "+" parallel are foil.
    gundam: /^r$|^r\+|lr|^p$|\+/,
    // Star Wars Unlimited: base Legendary/Special shine; the rest via printing.
    starwars: /legendary|special/,
    // Yu-Gi-Oh: Super and up (plain Rare is silver-letter, not foil).
    yugioh: /super|ultra|secret|ghost|gold|platinum|starlight|collector|prismatic|quarter/,
};

export function isHolo(card: Card): boolean {
    const printing = (card.selectedPrinting ?? card.basePrinting ?? "").toLowerCase();
    if (/holo|foil|hyperspace|showcase|etched/.test(printing)) return true;
    const rule = RARITY_RULES[gameKey(card.game)];
    return !!rule && rule.test((card.rarity ?? "").toLowerCase());
}

// Pointer-tracked holographic tilt (css-tricks.com/holographic-trading-card-effect,
// Simon Goellner's technique, simplified): pointer position inside the tile
// becomes CSS variables; CSS does the rest (perspective tilt, a rainbow shine
// positioned by the pointer, a glare that follows it). Handlers write straight
// to the element's style, no React state, so a sweep across a grid of tiles
// costs nothing but a few setProperty calls.
const still =
    typeof window !== "undefined" &&
    (window.matchMedia("(prefers-reduced-motion: reduce)").matches ||
        window.matchMedia("(hover: none)").matches);

export function holoMove(e: React.PointerEvent<HTMLElement>) {
    if (still) return;
    const el = e.currentTarget;
    const r = el.getBoundingClientRect();
    const px = Math.min(100, Math.max(0, ((e.clientX - r.left) / r.width) * 100));
    const py = Math.min(100, Math.max(0, ((e.clientY - r.top) / r.height) * 100));
    el.style.setProperty("--px", `${px.toFixed(1)}%`);
    el.style.setProperty("--py", `${py.toFixed(1)}%`);
    el.style.setProperty("--rx", `${((50 - py) / 7).toFixed(2)}deg`);
    el.style.setProperty("--ry", `${((px - 50) / 7).toFixed(2)}deg`);
}

export function holoLeave(e: React.PointerEvent<HTMLElement>) {
    const el = e.currentTarget;
    for (const v of ["--px", "--py", "--rx", "--ry"]) el.style.removeProperty(v);
}
