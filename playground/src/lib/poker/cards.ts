import type { Card } from "./types";

export function deck(): Card[] {
  return [..."shdc"].flatMap((suit) => [..."23456789TJQKA"].map((rank) => `${rank}${suit}` as Card));
}

export function shuffle<T>(items: readonly T[], random: () => number = Math.random): T[] {
  const result = [...items];
  for (let i = result.length - 1; i > 0; i--) {
    const j = Math.floor(random() * (i + 1));
    [result[i], result[j]] = [result[j], result[i]];
  }
  return result;
}

export function validateDeck(cards: readonly Card[]) {
  const valid = new Set(deck());
  if (cards.length !== 52 || new Set(cards).size !== 52 || cards.some((card) => !valid.has(card))) {
    throw new Error("A deck must contain all 52 distinct cards.");
  }
}

export function cardLabel(card: Card): string {
  return card[0].replace("T", "10") + ({ s: "♠", h: "♥", d: "♦", c: "♣" }[card[1]] ?? "");
}
