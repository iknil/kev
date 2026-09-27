import { api, type SystemOneRequest, type SystemOneResponse } from "../kev";
import { actionCandidates } from "./candidates";
import { observation, renderObservation } from "./observation";
import { POKER_PERSONAS } from "./personas";
import type { Candidate, GameState } from "./types";

export type Decision = {
  gameId: string; hand: number; revision: number; seat: number; street: GameState["street"];
  selected: Candidate; candidates: Candidate[]; probabilities: Record<string, number>; latencyMs: number;
  request: SystemOneRequest;
  orderChecks: { order: string[]; choice: string; probabilities: Record<string, number> }[];
};
export type DecisionProvider = (request: SystemOneRequest, signal?: AbortSignal) => Promise<SystemOneResponse>;

export function decisionRequest(s: GameState) {
  if (s.actor === null || !s.players[s.actor].personaId) throw new Error("An AI player must be acting.");
  const persona = POKER_PERSONAS.find((p) => p.id === s.players[s.actor!].personaId);
  if (!persona) throw new Error("Unknown AI personality.");
  const candidates = actionCandidates(s);
  if (!candidates.length) throw new Error("No legal decision is available.");
  const question: SystemOneRequest["questions"][string] = {
    type: "choice",
    instructions: "Choose one offered legal action. The state says whose turn it is, which cards are visible, what has happened on each street, and how many chips each action costs. Use only those facts and your stated playing preference. Opponents' private cards and future community cards are unknown. A bet amount alone does not reveal whether a player has a strong hand or is bluffing.",
    criteria: {},
  };
  // Each action occupies each position once. Questions share only the public
  // state; their question branches are isolated by the model's scoring interface.
  const request: SystemOneRequest = {
    model: "kev-latest",
    state: renderObservation(observation(s), persona),
    questions: Object.fromEntries(candidates.map((_, index) => [index === 0 ? "action" : `action_${index}`, {
      ...question,
      criteria: Object.fromEntries([...candidates.slice(index), ...candidates.slice(0, index)].map((c) => [c.id, c.description])),
    }])),
  };
  return { request, candidates };
}

export async function askKev(s: GameState, signal?: AbortSignal, provider: DecisionProvider = api.systemOne): Promise<Decision> {
  const { request, candidates } = decisionRequest(s);
  const response = await provider(request, signal);
  const ids = candidates.map((c) => c.id);
  const orderChecks = Object.entries(request.questions).map(([key, question]) => {
    const answer = response.answers?.[key];
    if (!answer || answer.type !== "choice") throw new Error("Kev did not return all Choice actions.");
    const probs = answer.probabilities;
    if (!ids.includes(answer.choice) || !probs || Object.keys(probs).length !== ids.length
      || ids.some((id) => !Number.isFinite(probs[id]) || probs[id] < 0 || probs[id] > 1)) {
      throw new Error("Kev returned an invalid action distribution. Retry the decision.");
    }
    const total = ids.reduce((sum, id) => sum + probs[id], 0);
    if (Math.abs(total - 1) > 0.02) throw new Error("Kev returned an invalid probability sum.");
    return { order: Object.keys(question.criteria!), choice: answer.choice,
      probabilities: Object.fromEntries(ids.map((id) => [id, probs[id] / total])) };
  });
  const probs = Object.fromEntries(ids.map((id) => [id,
    orderChecks.reduce((sum, check) => sum + check.probabilities[id], 0) / orderChecks.length]));
  // A fixed key tie-break avoids restoring the first-option advantage on ties.
  const selected = [...candidates].sort((a, b) => probs[b.id] - probs[a.id] || a.id.localeCompare(b.id))[0];
  return { gameId: s.id, hand: s.hand, revision: s.revision, seat: s.actor!, street: s.street, selected, candidates,
    probabilities: probs, latencyMs: response.latency_ms, request, orderChecks };
}

export function decisionIsCurrent(s: GameState, d: Decision) {
  return !s.complete && s.id === d.gameId && s.hand === d.hand && s.revision === d.revision && s.actor === d.seat;
}
