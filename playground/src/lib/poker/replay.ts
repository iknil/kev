import { deck } from "./cards";
import { act, startHand } from "./engine";
import { decisionRequest, type Decision } from "./kev-api";
import type { api } from "../kev";
import type { Action, Card, GameState, Street } from "./types";

export type ReplayHand = {
  id: string;
  title: string;
  collection: "Classic" | "Pluribus";
  source: string;
  sourceSha256: string;
  video?: string;
  smallBlind: number;
  bigBlind: number;
  ante: number;
  players: { name: string; stack: number; hole: Card[] }[];
  board: Card[];
  actions: { seat: number; street: Street; type: "fold" | "match" | "aggress"; to?: number }[];
  endingStacks?: number[];
};
export type ReplayFrame = { game: GameState; historicalAction?: Action };
export type ReplayDecision = Decision & { modelInfo?: Awaited<ReturnType<typeof api.models>> | null };

export function actionLabel(action: Action) {
  return "to" in action ? `${action.type} to ${action.to.toLocaleString()}` : action.type;
}

// The source is normalized to button = seat 0. Unknown folded cards and burns
// fill the internal deck only; they are never observations or displayed facts.
export function replayFrames(hand: ReplayHand): ReplayFrame[] {
  const known = [...hand.players.flatMap((p) => p.hole), ...hand.board];
  if (new Set(known).size !== known.length || known.some((c) => !deck().includes(c))) throw new Error("Duplicate or invalid source cards.");
  const spare = deck().filter((c) => !known.includes(c));
  const take = () => spare.shift()!;
  const holes = hand.players.map((p) => p.hole.length === 2 ? [...p.hole] : [take(), take()]);
  const order = [...hand.players.keys()].slice(1).concat(0);
  const cards: Card[] = [0, 1].flatMap((round) => order.map((seat) => holes[seat][round]));
  for (const street of [hand.board.slice(0, 3), hand.board.slice(3, 4), hand.board.slice(4, 5)]) {
    if (street.length) cards.push(take(), ...street);
  }
  cards.push(...spare);
  const players = hand.players.map((p, seat) => ({ seat, name: p.name, personaId: "balance", stack: p.stack,
    hole: [] as Card[], status: "active" as const, streetBet: 0, committed: 0, actedAt: null }));
  let game = startHand({ id: hand.id, hand: 0, revision: 0, mode: "self", players, dealer: 0,
    smallBlindSeat: 1, bigBlindSeat: 2, smallBlind: hand.smallBlind, bigBlind: hand.bigBlind, ante: hand.ante,
    street: "preflop", board: [], deck: [], currentBet: 0, lastFullRaise: hand.bigBlind, actor: null,
    complete: true, showdown: false, pots: [], payouts: {}, history: [] }, cards);
  const frames: ReplayFrame[] = [];
  for (const event of hand.actions) {
    if (game.actor !== event.seat || game.street !== event.street) throw new Error(`Source action does not match replay: ${hand.id}, action ${frames.length + 1}.`);
    const historicalAction: Action = event.type === "fold" ? { type: "fold" }
      : event.type === "match" ? { type: game.currentBet > game.players[event.seat].streetBet ? "call" : "check" }
      : { type: game.currentBet === 0 ? "bet" : "raise", to: event.to! };
    frames.push({ game, historicalAction });
    game = act(game, event.seat, historicalAction);
  }
  if (!game.complete || game.board.join() !== hand.board.join()) throw new Error(`Incomplete source replay: ${hand.id}.`);
  if (game.showdown && game.players.some((p) => p.status !== "folded" && hand.players[p.seat].hole.length !== 2)) {
    throw new Error("Cannot settle a source with unknown showdown cards.");
  }
  if (hand.endingStacks?.some((stack, seat) => stack !== game.players[seat].stack)) throw new Error(`Settlement differs from source: ${hand.id}.`);
  frames.push({ game });
  return frames;
}

export function replayRequest(hand: ReplayHand, frame: ReplayFrame) {
  const actor = frame.game.actor;
  if (actor === null || hand.players[actor].hole.length !== 2) throw new Error("The acting player's cards are not recorded; this decision cannot be evaluated.");
  return decisionRequest(frame.game);
}

export function replayReport(hand: ReplayHand, decisions: Record<number, ReplayDecision>, notes: Record<number, string>) {
  const frames = replayFrames(hand);
  return {
    format: "kev-poker-replay", version: 1, exportedAt: new Date().toISOString(),
    source: hand.source, sourceSha256: hand.sourceSha256, hand,
    interpretation: "Historical actions are references, not optimal-action labels. No solver EV or regret is available. Model probabilities are not winning odds or a calibrated mixed strategy.",
    decisions: frames.slice(0, -1).map((frame, index) => ({
      index, street: frame.game.street, seat: frame.game.actor,
      historicalAction: frame.historicalAction, modelDecision: decisions[index] ?? null, note: notes[index] ?? "",
    })),
  };
}
