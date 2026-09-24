import { api, type SystemOneRequest, type SystemOneResponse } from "../kev";
import { actionCandidates } from "./candidates";
import { observation } from "./observation";
import { POKER_PERSONAS } from "./personas";
import type { Candidate, GameState } from "./types";

export type Decision = {
  gameId: string; hand: number; revision: number; seat: number;
  selected: Candidate; candidates: Candidate[]; probabilities: Record<string, number>; latencyMs: number;
};
export type DecisionProvider = (request: SystemOneRequest, signal?: AbortSignal) => Promise<SystemOneResponse>;

export function decisionRequest(s: GameState) {
  if (s.actor === null || !s.players[s.actor].personaId) throw new Error("An AI player must be acting.");
  const persona = POKER_PERSONAS.find((p) => p.id === s.players[s.actor!].personaId);
  if (!persona) throw new Error("Unknown AI personality.");
  const candidates = actionCandidates(s);
  const request: SystemOneRequest = {
    model: "kev-latest",
    state: { ...observation(s), personality: { style: persona.style, preferences: persona.preferences } },
    questions: { action: {
      type: "choice",
      instructions: "Choose one legal poker action using only the visible facts and your playing preferences. Consider position, board, public action history, possible opposing hands, and chips left for later streets. The amounts are already calculated. You do not know opponents' private cards. Both value bets and bluffs may use the same amount.",
      criteria: Object.fromEntries(candidates.map((c) => [c.id, c.description])),
    } },
  };
  return { request, candidates };
}

export async function askKev(s: GameState, signal?: AbortSignal, provider: DecisionProvider = api.systemOne): Promise<Decision> {
  const { request, candidates } = decisionRequest(s);
  const response = await provider(request, signal);
  const answer = response.answers?.action;
  if (!answer || answer.type !== "choice") throw new Error("Kev did not return a Choice action.");
  const selected = candidates.find((c) => c.id === answer.choice);
  const ids = candidates.map((c) => c.id);
  const probs = answer.probabilities;
  if (!selected || !probs || Object.keys(probs).length !== ids.length || ids.some((id) => !Number.isFinite(probs[id]) || probs[id] < 0 || probs[id] > 1)
      || Math.abs(ids.reduce((sum, id) => sum + probs[id], 0) - 1) > 0.02) {
    throw new Error("Kev returned an invalid action distribution. Retry the decision.");
  }
  return { gameId: s.id, hand: s.hand, revision: s.revision, seat: s.actor!, selected, candidates,
    probabilities: probs, latencyMs: response.latency_ms };
}

export function decisionIsCurrent(s: GameState, d: Decision) {
  return !s.complete && s.id === d.gameId && s.hand === d.hand && s.revision === d.revision && s.actor === d.seat;
}
