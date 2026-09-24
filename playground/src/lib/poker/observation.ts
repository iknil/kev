import { inHand, legalActions, position, potTotal } from "./rules";
import type { EventKind, GameEvent, GameState, Street } from "./types";

const STREETS: Street[] = ["preflop", "flop", "turn", "river"];
const LINE: EventKind[] = ["post", "fold", "check", "call", "bet", "raise"];

function phrase(e: GameEvent) {
  if (e.kind === "post") return `post ${e.pay}`;
  if (e.kind === "call") return `call ${e.pay}`;
  if (e.kind === "bet" || e.kind === "raise") return `${e.kind} to ${e.to} (pay ${e.pay})`;
  return e.kind;
}

function streets(s: GameState) {
  const seen = new Set(s.history.map((e) => e.street));
  return STREETS.filter((street) => seen.has(street)).map((street) => {
    const events = s.history.filter((e) => e.street === street && e.seat !== null && LINE.includes(e.kind));
    const seats = [...new Set([
      ...events.map((e) => e.seat!),
      ...(street === s.street ? s.players.filter(inHand).map((p) => p.seat) : []),
    ])].sort((a, b) => a - b);
    return {
      street,
      order: events.map((e) => ({
        seat: e.seat! + 1, position: position(s, e.seat!), did: e.kind,
        ...(e.pay != null ? { pay: e.pay } : {}), ...(e.to != null ? { to: e.to } : {}),
        ...(e.allIn ? { all_in: true } : {}),
      })),
      players: seats.map((seat) => {
        const acts = events.filter((e) => e.seat === seat);
        const kinds = acts.map((e) => e.kind);
        const checkAt = kinds.indexOf("check");
        const raiseAt = kinds.indexOf("raise");
        const voluntary = kinds.filter((k) => k !== "post")[0];
        return {
          seat: seat + 1, position: position(s, seat),
          put_in: acts.reduce((n, e) => n + (e.pay ?? 0), 0),
          line: acts.map(phrase).join("; "),
          checked: kinds.includes("check"), bet: kinds.includes("bet"), raised: kinds.includes("raise"),
          folded: kinds.includes("fold"),
          check_raised: checkAt >= 0 && raiseAt > checkAt,
          limped: street === "preflop" && voluntary === "call",
        };
      }),
    };
  });
}

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
    streets: streets(s),
  };
}
