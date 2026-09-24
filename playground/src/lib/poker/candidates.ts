import { legalActions, potTotal, validateAction } from "./rules";
import type { Candidate, GameState } from "./types";

export function actionCandidates(s: GameState): Candidate[] {
  if (s.actor === null || s.complete) return [];
  const p = s.players[s.actor];
  const legal = legalActions(s);
  const result: Candidate[] = [];
  if (legal.fold) result.push({ id: "fold", action: { type: "fold" }, description: "Fold. Give up this hand; pay 0 additional chips." });
  if (legal.check) result.push({ id: "check", action: { type: "check" }, description: `Check. Pay 0; keep ${p.stack} chips.` });
  if (legal.call) result.push({ id: "call", action: { type: "call" }, description: `Call ${legal.call}. Street total ${p.streetBet + legal.call}; keep ${p.stack - legal.call} chips.${legal.call === p.stack ? " All-in call." : ""}` });
  const range = legal.aggression;
  if (!range) return result;
  const pot = potTotal(s);
  const isOpen = s.street === "preflop" && s.currentBet === s.bigBlind;
  const sizes = s.street === "preflop"
    ? (isOpen ? [2, 2.5, 3].map((multiple) => multiple * s.bigBlind) : [2.5, 3, 4].map((multiple) => multiple * s.currentBet))
    : s.currentBet === 0
      ? [1 / 3, 2 / 3, 1, 1.5].map((fraction) => fraction * pot)
      : [0.5, 1].map((fraction) => s.currentBet + fraction * (pot + legal.call));
  const amounts = [...new Set([range.minTo, ...sizes, range.maxTo].map((amount) => Math.max(range.minTo, Math.min(range.maxTo, Math.round(amount)))))].sort((a, b) => a - b);
  for (const to of amounts) {
    const pay = to - p.streetBet;
    const action = { type: range.type, to };
    validateAction(s, action);
    const scale = s.street === "preflop" ? `${(to / s.bigBlind).toFixed(2)} BB street total`
      : s.currentBet === 0 ? `${(pay / pot).toFixed(2)} times the pot`
      : `${((to - s.currentBet) / (pot + legal.call)).toFixed(2)} times the pot after calling, added above the current bet`;
    result.push({ id: `${range.type}_to_${to}`, action,
      description: `${range.type === "bet" ? "Bet" : "Raise"} to ${to}. Pay ${pay}; keep ${p.stack - pay} chips. ${scale}.${pay === p.stack ? " All-in." : ""}` });
  }
  return result;
}
