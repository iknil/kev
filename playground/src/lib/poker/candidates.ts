import { legalActions, potTotal, validateAction } from "./rules";
import type { Candidate, GameState } from "./types";

export function actionCandidates(s: GameState): Candidate[] {
  if (s.actor === null || s.complete) return [];
  const p = s.players[s.actor];
  const legal = legalActions(s);
  const result: Candidate[] = [];
  if (legal.fold) result.push({ id: "fold", action: { type: "fold" }, description: "Fold this hand. Pay 0 additional chips and give up any claim to the pot." });
  if (legal.check) result.push({ id: "check", action: { type: "check" }, description: `Check. Pay 0 additional chips. Keep ${p.stack} chips in your stack and remain in the hand.` });
  if (legal.call) result.push({ id: "call", action: { type: "call" }, description: `Call by paying ${legal.call} additional chips. Your total payment this street becomes ${p.streetBet + legal.call} chips; ${p.stack - legal.call} chips remain in your stack.` });
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
    result.push({ id: `${range.type}_to_${to}`, action,
      description: `${range.type === "bet" ? "Bet" : "Raise"} to a total of ${to} chips paid this street. Pay ${pay} additional chips now; ${p.stack - pay} chips remain in your stack.` });
  }
  return result;
}
