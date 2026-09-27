import { POKER_PERSONAS } from "./personas";
import type { Decision } from "./kev-api";
import type { GameState } from "./types";

export type HandLog = ReturnType<typeof handLog>;

export function handLog(game: GameState, decisions: Decision[]) {
  const players = game.players.map((player) => {
    const contributed = game.history.filter((event) => event.seat === player.seat
      && ["ante", "post", "call", "bet", "raise"].includes(event.kind)).reduce((sum, event) => sum + (event.pay ?? 0), 0);
    const refunded = game.history.filter((event) => event.seat === player.seat && event.kind === "refund")
      .reduce((sum, event) => sum + (event.pay ?? 0), 0);
    const won = game.payouts[player.seat] ?? 0;
    const persona = POKER_PERSONAS.find((profile) => profile.id === player.personaId);
    return {
      seat: player.seat + 1,
      name: player.name,
      persona: persona ? { id: persona.id, style: persona.style, preferences: persona.preferences } : null,
      hole_cards: [...player.hole],
      starting_stack: player.stack + contributed - refunded - won,
      ending_stack: player.stack,
      contributed,
      refunded,
      won,
      final_status: player.status,
    };
  });

  return {
    hand: game.hand,
    mode: game.mode,
    dealer_seat: game.dealer + 1,
    small_blind: game.smallBlind,
    big_blind: game.bigBlind,
    board: [...game.board],
    showdown: game.showdown,
    players,
    actions: game.history.map((event) => ({
      street: event.street,
      seat: event.seat === null ? null : event.seat + 1,
      kind: event.kind,
      text: event.text,
      ...(event.pay != null ? { pay: event.pay } : {}),
      ...(event.to != null ? { to: event.to } : {}),
      ...(event.allIn ? { all_in: true } : {}),
    })),
    pots: game.pots.map((pot) => ({
      amount: pot.amount,
      eligible_seats: pot.eligible.map((seat) => seat + 1),
      winner_seats: pot.winners.map((seat) => seat + 1),
    })),
    kev_decisions: decisions.map((decision) => ({
      street: decision.street,
      acting_seat: decision.seat + 1,
      latency_ms: decision.latencyMs,
      request: decision.request,
      selected_action: decision.selected,
      probabilities: decision.probabilities,
      order_checks: decision.orderChecks,
    })),
  };
}

export function sessionLog(gameId: string, hands: HandLog[]) {
  return {
    format: "kev-poker-session",
    version: 1,
    game_id: gameId,
    exported_at: new Date().toISOString(),
    privacy: "Local simulation export. Player hole cards include hidden and folded cards for analysis; the undealt deck is omitted.",
    completed_hands: hands,
  };
}

export function downloadSessionLog(gameId: string, hands: HandLog[]) {
  const blob = new Blob([JSON.stringify(sessionLog(gameId, hands), null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `poker-session-${new Date().toISOString().replaceAll(":", "-")}.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
