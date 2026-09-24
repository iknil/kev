import { act } from "./engine";
import { askKev, decisionIsCurrent, type Decision, type DecisionProvider } from "./kev-api";
import type { GameState } from "./types";

// One in-flight decision. Cancel invalidates a response even if its provider ignores abort.
export class PokerController {
  private pending: AbortController | null = null;

  cancel() {
    this.pending?.abort();
    this.pending = null;
  }

  async step(read: () => GameState | null, commit: (state: GameState, decision: Decision) => void, provider?: DecisionProvider): Promise<void> {
    const state = read();
    if (this.pending || !state || state.actor === null || state.complete || !state.players[state.actor].personaId) return;
    const pending = new AbortController();
    this.pending = pending;
    const timeout = setTimeout(() => pending.abort(), 60_000);
    try {
      const decision = await askKev(state, pending.signal, provider);
      const current = read();
      if (this.pending !== pending || !current || !decisionIsCurrent(current, decision)) return;
      if (pending.signal.aborted) throw new Error("Kev request timed out. Retry the decision.");
      commit(act(current, decision.seat, decision.selected.action), decision);
    } catch (error) {
      if (this.pending !== pending) return;
      if (pending.signal.aborted) throw new Error("Kev request timed out. Retry the decision.");
      throw error;
    } finally {
      clearTimeout(timeout);
      if (this.pending === pending) this.pending = null;
    }
  }
}
