# Poker playground

A local no-limit Texas Hold'em game using play chips. Start with 2, 6, 9, or 10 seats, 2,000 chips per seat, and fixed 10/20 blinds. There is no rake, ante, rebuy, blind escalation, or real-money integration. Eliminated seats sit out. Refresh clears the game. AI play uses the model already loaded by `kev.serve`; it does not require a poker-specific backend.

## Layers

- `components/poker-table.tsx` and `components/poker/`: presentation and React interaction. `use-poker.ts` owns the live game, automation, and cancellation. UI state is separate from the rules.
- `types.ts`, `cards.ts`, `rules.ts`, `engine.ts`, `evaluate.ts`, `settlement.ts`: pure TypeScript game domain. Moves produce a new state. Cards are shuffled once per hand, dealt clockwise, and burned before community streets. Tests can inject a fixed deck.
- `candidates.ts`, `observation.ts`, `kev-api.ts`, `controller.ts`: legal action candidates, private-information filtering, System One translation, and asynchronous decision handling. The transport reuses `lib/kev.ts`.
- `personas.ts`: 20 distinct preference prompts. A new table rotates the preset lineup; all profiles share the same candidate-generation rules.

## Rules and accounting

Amounts are integer chips. `stack`, `streetBet`, and `committed` respectively record unspent chips, this street's contribution, and the whole hand's contribution. Bets and raises use `to`, the total street contribution. The engine derives the additional payment and validates every human or AI move.

Full raises update the minimum raise size. Short all-ins do not individually reopen a previous bettor's action; cumulative short raises can reopen it when the amount faced reaches a full raise. Players who checked before an opening short all-in may raise. All-in calls are capped to the player's stack. When only one player can still bet, that player can only resolve an outstanding call or fold; otherwise the board runs out.

Uncalled excess is refunded. Contributions are divided into main and side pots; folded players contribute but cannot win. Each eligible player's best five of seven cards determines the winners, with exact kickers and ace-low straights. Split pots allocate odd chips clockwise after the button. The UI shows pot awards and all surviving showdown hands.

The button moves to the next funded seat. When transitioning to heads-up, the next big blind is the next funded seat after the old big blind. The heads-up button posts the small blind and acts first preflop, last postflop. This uses a moving button, not a tournament dead-button procedure.

Rules references: [PokerStars rules](https://www.pokerstars.com/help/articles/poker-rules-master/) and [Poker TDA reopening examples](https://www.pokertda.com/view-poker-tda-rules/). This implementation omits live-table procedures such as verbal declarations, misdeals, and mucking at showdown. A free fold is not offered when checking is available.

## Amount candidates

Each decision scores complete actions using Choice questions, not a Score interpolated into money. All amounts are calculated before inference:

- Preflop unopened: 2, 2.5, and 3 BB totals.
- Preflop facing a raise: 2.5, 3, and 4 times the current highest street contribution.
- Postflop unopened: one-third, two-thirds, one pot, and 1.5 pots.
- Postflop facing a bet: half-pot and pot-sized raises above the call, using `currentBet + fraction * (pot + callCost)`.

Add minimum aggression and all-in, clamp to the legal range, round to integer chips, deduplicate, and sort. Include legal fold/check/call actions. With the default schedules, there are at most eight candidates. Descriptions give the payment, total contribution, chips remaining, and scale; they do not attach value/bluff motives to amounts. Candidate granularity is an engineering policy, not a proven optimal poker strategy.

## Model boundary

The request sends one English state string rendered from an explicit allowlist: the acting player's cards, public board by street, stacks, contributions, statuses, current decision, and one chronological public action log. It explains payment units and ends with a separate **Your playing personality** section containing the acting bot's current style and preferences. The structured observation remains an internal facts projection; it is not sent again beside the rendered text. No deck, unrevealed board, or opponent hole cards enter the request. Personas never change legality or settlement. [STATE_STANDARD.md](STATE_STANDARD.md) defines the communication requirements and deferred hand-strength and side-pot work.

For N candidates, the adapter sends N cyclic option orders as isolated questions in one request. Each action appears in each position once. It validates every distribution, normalizes rounding error, averages by action ID, and selects the largest mean with a fixed key tie-break. One missing or invalid answer rejects the whole decision. This costs N question evaluations but shares the state prefix. It reduces a pure position preference; it does not guarantee invariance to all permutations or correct poker strategy.

All-in remains a legal candidate. Every wager description states the payment and remaining stack, including zero, without an extra all-in slogan. The UI displays whether the tested orders chose different actions. Exports retain the exact multi-question request and per-order results alongside the average. Probabilities describe model preferences, not winning odds or calibrated mixed-strategy frequencies.

A local diagnostic on the served `jaredpalmer/kev-0.8b` used six initial opening hands to investigate formatting, then 12 additional states: six preflop, three flop and three river. In the additional preflop sample, the old single-order input chose 100-BB all-in 3/6 times; the new adapter chose call 6/6 times. All six postflop decisions remained check. The same inputs still show weak hand discrimination. These are integration diagnostics without solver labels, not evidence of increased EV or playing strength.

One request is in flight at a time, with a 60-second timeout. Pause/reset cancels the request and invalidates its result even if a provider ignores cancellation. Replies must match the game ID, hand, revision, and acting seat. Errors pause automation without advancing the game; AI step retries. Auto play stops advancing at a human turn or the end of the hand; use Next hand explicitly. In human mode, enabled automation resumes after the human acts.

## Verification

After a hand finishes, **Export log** downloads a JSON session file. It contains every completed hand in the current table session, ordered actions and street, the final board, player stacks and contributions, personas, pot awards, and each Kev request with its observation, legal options, selected action, averaged probabilities, per-order checks, and latency. The local simulation knows every player's cards, including folded cards, so the export includes them for outcome analysis. It omits the undealt deck. Starting a new table clears the session log.

From `playground/`:

```bash
npm run test:poker
npm run lint
npx next typegen && npx tsc --noEmit -p .
```

Tests use Node's test runner, compiled by the existing TypeScript dependency. They cover evaluator categories/kickers, blind order, reopening, refunds, side pots, ties, legal candidate generation, hidden-information exclusion, invalid responses, canceled/stale replies, and seeded multi-hand simulations with chip conservation. CI runs the same tests. A live backend is only needed for browser and real-model integration checks.
