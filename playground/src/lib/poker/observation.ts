import { inHand, legalActions, position, potTotal } from "./rules";
import type { GameState } from "./types";

// Deliberate allowlist: never serialize GameState or spread a Player into an API request.
export function observation(s: GameState) {
  if (s.actor === null || s.complete) throw new Error("No player needs a decision.");
  const hero = s.players[s.actor];
  const opponents = s.players.filter((p) => p.seat !== hero.seat && inHand(p));
  return {
    game: "No-limit Texas Hold'em, integer play chips, no rake",
    hand: s.hand, street: s.street, board: [...s.board], pot_including_current_bets: potTotal(s),
    big_blind: s.bigBlind, current_highest_street_bet: s.currentBet,
    hero: { seat: hero.seat + 1, position: position(s, hero.seat), hole_cards: [...hero.hole], stack: hero.stack,
      street_contribution: hero.streetBet, total_contribution: hero.committed, call_cost: legalActions(s).call },
    players: s.players.map((p) => ({ seat: p.seat + 1, position: position(s, p.seat), stack: p.stack,
      status: p.status, street_contribution: p.streetBet, total_contribution: p.committed })),
    effective_stacks: opponents.map((p) => ({ against_seat: p.seat + 1, chips: Math.min(hero.stack + hero.streetBet, p.stack + p.streetBet) })),
    // Keep requests bounded; recent public actions complement the exact contribution totals above.
    recent_actions: s.history.slice(-24).map((e) => ({ street: e.street, seat: e.seat === null ? null : e.seat + 1, action: e.text })),
  };
}
