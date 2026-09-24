import type { Action, GameState, LegalActions, Player } from "./types";

export const TABLE_SIZES = [2, 6, 9, 10] as const;
export const inHand = (p: Player) => p.status === "active" || p.status === "all-in";
export const potTotal = (s: GameState) => s.players.reduce((sum, p) => sum + p.committed, 0);

export function clockwise(seats: number[], after: number): number[] {
  return [...seats.filter((seat) => seat > after), ...seats.filter((seat) => seat <= after)];
}

export function position(s: GameState, seat: number): string {
  const occupied = s.players.filter((p) => p.status !== "out").map((p) => p.seat);
  if (!occupied.includes(seat)) return "Out";
  if (seat === s.dealer) return occupied.length === 2 ? "BTN / SB" : "BTN";
  if (seat === s.smallBlindSeat) return "SB";
  if (seat === s.bigBlindSeat) return "BB";
  const early = clockwise(occupied, s.bigBlindSeat).filter((id) => ![s.dealer, s.smallBlindSeat, s.bigBlindSeat].includes(id));
  const labels: Record<number, string[]> = {
    1: ["CO"], 2: ["HJ", "CO"], 3: ["UTG", "HJ", "CO"],
    4: ["UTG", "LJ", "HJ", "CO"], 5: ["UTG", "UTG+1", "LJ", "HJ", "CO"],
    6: ["UTG", "UTG+1", "UTG+2", "LJ", "HJ", "CO"],
    7: ["UTG", "UTG+1", "UTG+2", "MP", "LJ", "HJ", "CO"],
  };
  return labels[early.length][early.indexOf(seat)];
}

export function legalActions(s: GameState): LegalActions {
  const empty: LegalActions = { fold: false, check: false, call: 0, aggression: null };
  if (s.complete || s.actor === null) return empty;
  const p = s.players[s.actor];
  if (p.status !== "active") return empty;
  const owed = Math.max(0, s.currentBet - p.streetBet);
  const call = Math.min(owed, p.stack);
  const maxTo = p.streetBet + p.stack;
  const reopened = p.actedAt === null || p.actedAt === 0 || s.currentBet - p.actedAt >= s.lastFullRaise;
  const opponentCanCall = s.players.some((other) => other.seat !== p.seat && other.status === "active");
  const minTo = s.currentBet + s.lastFullRaise;
  return {
    fold: owed > 0, check: owed === 0, call,
    aggression: reopened && opponentCanCall && maxTo > s.currentBet ? {
      type: s.currentBet === 0 ? "bet" : "raise", minTo: Math.min(minTo, maxTo), maxTo, shortAllIn: maxTo < minTo,
    } : null,
  };
}

export function validateAction(s: GameState, action: Action): void {
  const legal = legalActions(s);
  const valid = action.type === "fold" ? legal.fold
    : action.type === "check" ? legal.check
    : action.type === "call" ? legal.call > 0
    : legal.aggression && action.type === legal.aggression.type && Number.isSafeInteger(action.to)
      && action.to >= legal.aggression.minTo && action.to <= legal.aggression.maxTo;
  if (!valid) throw new Error("That action is not legal in the current state.");
}
