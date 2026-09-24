import { deck, shuffle, validateDeck } from "./cards";
import { POKER_PERSONAS } from "./personas";
import { clockwise, inHand, legalActions, TABLE_SIZES, validateAction } from "./rules";
import { settle } from "./settlement";
import type { Action, Card, GameState, Mode, Player, Street } from "./types";

function pay(p: Player, amount: number) {
  p.stack -= amount; p.streetBet += amount; p.committed += amount;
  if (p.stack === 0) p.status = "all-in";
}

function draw(s: GameState): Card {
  const card = s.deck.shift();
  if (!card) throw new Error("Deck exhausted.");
  return card;
}

function dealStreet(s: GameState) {
  const next: Record<string, Street> = { preflop: "flop", flop: "turn", turn: "river" };
  s.street = next[s.street];
  draw(s); // Burn before each community-card street.
  for (let i = 0; i < (s.street === "flop" ? 3 : 1); i++) s.board.push(draw(s));
  s.currentBet = 0; s.lastFullRaise = s.bigBlind;
  for (const p of s.players) { p.streetBet = 0; p.actedAt = null; }
  s.history.push({ street: s.street, seat: null, text: `${s.street}: ${s.board.join(" ")}` });
}

function advance(s: GameState, after: number) {
  if (s.players.filter(inHand).length === 1) { settle(s); return; }
  const active = s.players.filter((p) => p.status === "active");
  // With no opponent able to bet, only a pending call/fold needs a decision.
  if (active.length < 2) {
    const lone = active[0];
    const highest = Math.max(...s.players.filter(inHand).map((p) => p.streetBet));
    if (lone && lone.streetBet < highest) { s.currentBet = highest; s.actor = lone.seat; return; }
    while (s.street !== "river") dealStreet(s);
    settle(s); return;
  }
  const pending = active.filter((p) => p.actedAt === null || p.streetBet < s.currentBet).map((p) => p.seat);
  if (pending.length) { s.actor = clockwise(pending, after)[0]; return; }
  if (s.street === "river") { settle(s); return; }
  dealStreet(s);
  s.actor = clockwise(active.map((p) => p.seat), s.dealer)[0];
}

export function startHand(previous: GameState, cards: Card[] = shuffle(deck())): GameState {
  if (!previous.complete) throw new Error("Finish the current hand first.");
  validateDeck(cards);
  const s = structuredClone(previous);
  const occupied = s.players.filter((p) => p.stack > 0).map((p) => p.seat);
  if (occupied.length < 2) throw new Error("Only one player has chips. Start a new table.");
  s.hand++; s.revision++; s.complete = false; s.showdown = false;
  // Moving button over funded seats; heads-up BB never posts BB twice on transition.
  s.dealer = s.hand === 1 ? occupied[0]
    : occupied.length === 2 ? clockwise(occupied, previous.bigBlindSeat)[1]
    : clockwise(occupied, previous.dealer)[0];
  const order = clockwise(occupied, s.dealer);
  s.smallBlindSeat = occupied.length === 2 ? s.dealer : order[0];
  s.bigBlindSeat = occupied.length === 2 ? order[0] : order[1];
  s.street = "preflop"; s.board = []; s.deck = [...cards]; s.history = []; s.pots = []; s.payouts = {};
  s.currentBet = s.bigBlind; s.lastFullRaise = s.bigBlind;
  for (const p of s.players) {
    p.hole = []; p.streetBet = 0; p.committed = 0; p.actedAt = null;
    p.status = p.stack > 0 ? "active" : "out";
  }
  for (let round = 0; round < 2; round++) for (const seat of order) s.players[seat].hole.push(draw(s));
  for (const [seat, blind] of [[s.smallBlindSeat, s.smallBlind], [s.bigBlindSeat, s.bigBlind]]) {
    const p = s.players[seat]; const amount = Math.min(blind, p.stack);
    pay(p, amount); s.history.push({ street: s.street, seat, text: `Posted ${blind === s.smallBlind ? "SB" : "BB"} ${amount}` });
  }
  advance(s, s.bigBlindSeat);
  return s;
}

export function createGame(options: { id: string; seats: number; mode: Mode; stack?: number; cards?: Card[]; personaOffset?: number }): GameState {
  const { id, seats, mode, stack = 2000, personaOffset = 0 } = options;
  if (!(TABLE_SIZES as readonly number[]).includes(seats)) throw new Error("Choose 2, 6, 9, or 10 seats.");
  if (!Number.isSafeInteger(stack) || stack < 20 || stack > 1_000_000) throw new Error("Invalid starting stack.");
  const players: Player[] = Array.from({ length: seats }, (_, seat) => {
    const persona = POKER_PERSONAS[(seat + personaOffset) % POKER_PERSONAS.length];
    const human = seat === 0 && mode === "human";
    return { seat, name: human ? "You" : persona.name, personaId: human ? null : persona.id, stack,
      hole: [], status: "active", streetBet: 0, committed: 0, actedAt: null };
  });
  return startHand({ id, mode, hand: 0, revision: 0, players, dealer: 0, smallBlindSeat: 0, bigBlindSeat: 1,
    smallBlind: 10, bigBlind: 20, street: "preflop", board: [], deck: [], currentBet: 0, lastFullRaise: 20,
    actor: null, complete: true, showdown: false, pots: [], payouts: {}, history: [] }, options.cards);
}

export function act(previous: GameState, seat: number, action: Action): GameState {
  if (previous.complete || previous.actor !== seat) throw new Error("It is not this player's turn.");
  validateAction(previous, action);
  const s = structuredClone(previous);
  const p = s.players[seat];
  let text: string = action.type;
  if (action.type === "fold") p.status = "folded";
  else if (action.type === "call") {
    const amount = legalActions(previous).call; pay(p, amount); text = `Call ${amount}`;
  } else if (action.type === "bet" || action.type === "raise") {
    const increase = action.to - s.currentBet;
    if (increase >= s.lastFullRaise) s.lastFullRaise = increase;
    const amount = action.to - p.streetBet;
    pay(p, amount); s.currentBet = action.to; text = `${action.type} to ${action.to} (pay ${amount})`;
  }
  p.actedAt = s.currentBet;
  s.history.push({ street: s.street, seat, text: text + (p.status === "all-in" ? " · all-in" : "") });
  s.revision++;
  advance(s, seat);
  return s;
}
