# Poker hand review sources

The playground's `/poker/replay` page contains one televised classic, Dwan–Ivey 2009, and the first 50 hands in the PHH dataset's `pluribus/100` directory, selected by numeric hand ID 0–49 before any model scoring. These are 51 hands and 502 recorded player decisions. The Pluribus sample is consecutive research play, not a curated list of famous hands or a representative strength benchmark.

Source: https://github.com/uoftcprg/phh-dataset/tree/e47fbd5816372360bade4de5d712346fe1bb70f6

The upstream MIT license is retained in `LICENSE`. Bundled PHH files use UTF-8 and LF. `sourceSha256` hashes these bundled files; each generated hand also links to its exact upstream revision. Rebuild the browser catalog offline with:

```sh
python3 playground/scripts/build-poker-replays.py
```

The converter accepts only the selected no-limit Texas Hold'em records, uniform antes and ordinary blinds. It rotates PHH's last-seat button to playground seat zero. The existing engine reconstructs each hand, checks action order and legality, and compares all 50 Pluribus ending-stack vectors with the source. It includes antes in pot accounting without counting them as street wagers. Dwan–Ivey's uncalled excess is refunded; the awarded pot is 1,109,500 chips. Currency values are replayed as integer chips with no rake modeled.

Antonius's cards in the classic are unknown. Unknown cards and burns receive internal filler cards solely to construct a valid deck. They are never shown as facts, and evaluation is disabled when the acting player's cards are missing. Unknown showdown hands are rejected. Known cards are visible only for the acting player, recorded showdown participants, or explicitly selected God view. Future board cards stay hidden until the recorded runout.

Requests use the existing observation allowlist, Balanced persona and legal candidate generator. They contain no player names, source titles, future actions, results, opponents' cards or future board cards. The UI's God view does not alter requests. Historical bet sizes are not inserted into the candidates. Exact candidate coverage is shown after revealing the historical action. Decisions average cyclic option orders through the shared poker adapter; the page reports order disagreement and exports every order result.

Kev suggestions do not branch the hand: Next always follows the recorded action. Results compare historical play with the model's choice, without treating agreement or realized winnings as correctness. No solver EV, regret, calibrated mixed strategy or aggregate strength score is claimed. This public sample may have appeared in foundation-model training.

Each hand's JSON export contains source provenance, recorded cards and actions, exact model requests, probabilities, selected actions and user notes. Opponents' cards in that local export are intentional review data, not inference inputs. Reviews persist across hand selection in the current page only; export before refresh or navigation away.

Validation: `npm run test:poker --prefix playground`, plus playground lint and TypeScript checks.
