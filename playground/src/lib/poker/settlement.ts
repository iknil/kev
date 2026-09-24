import { evaluate } from "./evaluate";
import { clockwise, inHand } from "./rules";
import type { GameState, Pot } from "./types";

// Called only on the engine's private copy, after all action is closed.
export function settle(s: GameState): void {
  const ranked = [...s.players].sort((a, b) => b.committed - a.committed);
  const excess = ranked[0].committed - ranked[1].committed;
  if (excess > 0) {
    ranked[0].stack += excess;
    ranked[0].committed -= excess;
    ranked[0].streetBet = Math.max(0, ranked[0].streetBet - excess);
    s.history.push({ street: s.street, seat: ranked[0].seat, text: `Uncalled ${excess} returned` });
  }
  const live = s.players.filter(inHand);
  const levels = [...new Set(s.players.map((p) => p.committed).filter(Boolean))].sort((a, b) => a - b);
  const pots: Pot[] = [];
  let previous = 0;
  for (const level of levels) {
    const contributors = s.players.filter((p) => p.committed >= level);
    const amount = (level - previous) * contributors.length;
    previous = level;
    const eligible = live.filter((p) => p.committed >= level).map((p) => p.seat);
    if (!eligible.length) throw new Error("Pot has no eligible player.");
    const last = pots.at(-1);
    // Folded contributions do not create a new side pot or another odd-chip allocation.
    if (last && last.eligible.join() === eligible.join()) last.amount += amount;
    else pots.push({ amount, eligible, winners: [] });
  }
  for (const pot of pots) {
    const scores = pot.eligible.map((seat) => live.length === 1 ? 0 : evaluate([...s.players[seat].hole, ...s.board]).score);
    const best = Math.max(...scores);
    pot.winners = clockwise(pot.eligible.filter((_, i) => scores[i] === best), s.dealer);
    const share = Math.floor(pot.amount / pot.winners.length);
    pot.winners.forEach((seat, i) => {
      const award = share + (i < pot.amount % pot.winners.length ? 1 : 0);
      s.players[seat].stack += award;
      s.payouts[seat] = (s.payouts[seat] ?? 0) + award;
    });
  }
  s.pots = pots;
  s.showdown = live.length > 1;
  s.complete = true;
  s.actor = null;
  for (const [seat, amount] of Object.entries(s.payouts)) {
    const p = s.players[Number(seat)];
    const hand = s.showdown ? ` · ${evaluate([...p.hole, ...s.board]).name}` : " · others folded";
    s.history.push({ street: s.street, seat: p.seat, text: `Won ${amount}${hand}` });
  }
  for (const p of s.players) { p.committed = 0; p.streetBet = 0; }
}
