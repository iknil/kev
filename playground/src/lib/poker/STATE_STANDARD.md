# Poker state communication standard

Status: design standard, September 25, 2026. Sections on made-hand facts and side pots are future work. The current observation implements the communication changes in the other sections; model understanding has not been measured.

## Purpose

A model should be able to identify the acting player, the visible cards, the action faced, and the consequences of each available action using ordinary English and basic arithmetic. The observation must explain facts that otherwise require knowledge of poker notation, betting conventions, or engine internals.

Poker-specific training is not assumed. General pretraining may contain poker knowledge, but that is not evidence of reliable understanding. Clear observations reduce interpretation work; they do not establish strategic competence.

In this document, **must** is an acceptance requirement, **should** is a default that needs a documented reason to omit, and **may** is optional. These are project requirements, not an externally validated industry standard.

## Information contract

| Topic | Required meaning |
| --- | --- |
| Perspective | Identify the acting seat and say explicitly that it is that player's turn. Use “you” consistently for that seat. |
| Game | Name no-limit Texas Hold'em, the chip unit, blinds, and applicable ante or rake. Briefly explain that players form their best five-card hand from their two private cards and the shared board. |
| Position | Give the seat, dealer button, blind roles, and what those roles mean for action order. Expand abbreviations on first use. |
| Players | Identify who remains in the hand, who folded, who is all-in, and who can still act. Explain that an all-in player can still win eligible pots but cannot bet again. |
| Current decision | State the street, who last bet or raised on this street if anyone did, the amount faced, and the additional payment needed to call. Say explicitly when no payment is needed to continue. |
| Cards | Show only the acting player's private cards and revealed community cards. Group the board into flop, turn, and river. Mark future streets as not dealt. |
| Chips | Separate chips remaining, chips paid this street, chips paid across the hand, and chips currently in the pot. |
| History | Give public actions in chronological order, grouped by street, with actor and explicit payment semantics. |
| Actions | Supply legal candidates with their additional payment, resulting street total, and remaining stack. Explain fold, check, call, bet, and raise in ordinary language where offered. |
| Unknowns | State that opponents' private cards and future board cards are unknown. Observed actions do not reveal an opponent's actual hand or intent. |

Every observation must stand alone. A model must not need an earlier request, UI state, or a source-code definition to interpret it.

## Position and action order

“Seat 3, BB” is insufficient on its own. Prefer “You are seat 3, the big blind. Seat 6 is the dealer button. On this street, you act before seat 6.”

The observation must distinguish the normal order for the street from who acts next after the current decision. A raise can cause players who already acted to act again. Do not describe a previously acting player as permanently finished with the street.

For multiple opponents, explain relative order using seat identities. Do not reduce the entire table to one IP/OOP label. In heads-up play, explain that the button also posts the small blind, acts first before the flop, and acts last after the flop.

Seat identity must remain stable throughout the hand. Folded players' earlier actions retain their original seat and position.

## Cards and visible hand facts

Use full card names, such as “Ace of spades,” or include an explicit legend for every compact notation used. Separate the three flop cards from the turn and river so the model does not have to infer when each card appeared.

Future TODO: decide whether the model should determine the acting player's current hand category from visible cards or an external deterministic calculator should supply it. If supplied, include the constituent cards. For example: “Your current best hand is one pair, aces; your remaining three cards are king, nine, and four.” Before the flop, say that only two private cards are available and there is no five-card hand yet.

If categories or draws are supplied, explain them in ordinary language. Describe a flush draw as four cards of one suit with one further card of that suit needed. Define whether a draw can complete on the next card or requires both remaining cards. Never imply that completing a draw guarantees winning.

Derived facts must come from the acting player's permitted observation. Do not calculate equity using hidden opponent cards, the undealt deck, or a known future runout. Equity against an assumed range must be labeled as an estimate with its assumptions; it is not a visible fact.

## Chip accounting

All amounts must have one stated unit. If big-blind equivalents are also shown, retain the exact chip amount as the authoritative value.

Use these definitions consistently:

- **Chips remaining:** chips the player has not committed and can still spend.
- **Street contribution:** chips already committed during the current betting round.
- **Hand contribution:** chips committed across the hand, net of any returned excess.
- **Call payment:** additional chips paid now to call, capped by chips remaining.
- **Raise to:** the player's total contribution on this street after raising.
- **Raise payment:** raise-to total minus the player's existing street contribution.
- **Current pot:** chips committed by all players, including current street bets, with completed refunds accounted for.

For example: “You have paid 4 chips this street. Raising to 18 means paying 14 more chips.” A bare “raise 18” must not be used.

If a player cannot fully match a bet, describe the action as an all-in call for the actual payment. Explain the resulting limit on pot eligibility. Do not present the unmatched full bet as the player's call payment.

Future TODO: calculate each current pot's amount and eligible seats in multiway all-in situations. When an action changes eligibility or creates a side pot, explain the resulting pots for that action. Identify any uncalled amount that will be returned. The model must not treat the entire table's pot as winnable by every player.

“Effective stack” may be included only with an explicit accounting definition and the opponent it refers to. Prefer concrete remaining stacks and action costs when the term adds no useful information.

Any displayed pot odds must identify the action, eligible pot, and assumptions. A simple call-cost ratio must not be presented as sufficient to justify a call when further betting or side pots affect the decision.

## Public action history

Use one authoritative chronological history. Each street should begin with the board visible at that time, followed by that street's actions. Include blind posts, actual payments, all-in actions, and refunds where they affect accounting.

Prefer “Seat 6 raised to 18 chips, paying 14 additional chips” over `IP_RAISE_18`. Compact machine identifiers may accompany readable descriptions, but must not carry essential meaning alone.

Summaries may repeat selected facts when they make the decision easier to read. Every summary must agree with the history. Avoid maintaining a full chronological log, a second full prose log, and redundant flags for every action.

A limp means calling the big blind before any voluntary raise. Calling a raise must not be labeled a limp. A check-raise means checking and later raising on the same street. An empty history must be described as “no actions yet,” not left ambiguous.

Do not silently truncate history. If input limits prevent a full history, disclose the omission and preserve current accounting, relevant prior aggression, and all actions on the current street. The summary policy must be evaluated separately.

## Facts, preferences, and decisions

Keep factual state and playing preferences in distinct sections. Preferences must not alter amounts, legality, known cards, or descriptions of opponents. Labels such as “weak opponent,” “profitable bluff,” and “safe call” are strategic judgments and must not appear as observed facts.

Action descriptions must explain mechanics without recommending an answer. Both a value bet and a bluff can use the same amount. The model chooses among offered candidates; the observation must not imply those candidates exhaust every possible legal wager size.

Use a fixed neutral objective when evaluating comprehension. Evaluate personality prompts separately so a preference for aggression cannot hide a misunderstanding of the amount faced.

## Suggested rendering

Plain English with a small player table is the preferred starting point. Structured JSON is also acceptable if field names and an accompanying explanation satisfy the same contract. Neither format is presumed superior without measurement. Keep the order stable: perspective, current facts, players and chips, public history, then the decision and preferences.

The following is an illustrative current-street excerpt, not a complete request. Earlier street history and the complete candidate list would also be supplied.

```text
You are seat 3. It is your turn on the turn, the betting round after
the fourth community card is dealt. All amounts are play chips.
The small blind is 1 chip and the big blind is 2 chips.

Two players remain: you and seat 6. You posted the big blind.
Seat 6 is the dealer button and acts after you on postflop streets.
Both players can still bet. Their private cards are unknown to you.

Your private cards: Ace of spades, King of diamonds.
Flop: Ace of hearts, Nine of clubs, Four of diamonds.
Turn: Two of spades. River: not dealt.

The pot contains 40 chips, including seat 6's current bet of 10.
You have 85 chips remaining, have paid 0 this street,
and have contributed 15 across the hand.
Seat 6 has 75 chips remaining, has paid 10 this street,
and has contributed 25 across the hand.

Turn actions so far:
1. You checked, paying 0 chips.
2. Seat 6 bet 10 chips, paying 10 chips.

You now face that bet. Calling costs 10 additional chips.
A call would leave you with 75 chips and make the pot 50 chips.
In this two-player situation, that call completes the turn betting.
Checking is unavailable because you have not matched the bet.

Example candidate description:
Raise to 30 chips. Pay 30 additional chips; keep 55 chips.
Your total contribution this street becomes 30 chips.
Seat 6 would need 20 additional chips to match your raise
and would get another decision.
```

## Acceptance and evaluation

Before adoption, every generated observation must satisfy deterministic checks for card visibility, stable seat identity, chronological actions, legal candidates, chip accounting, and agreement between summaries and underlying events. Pot eligibility becomes an additional gate when the future side-pot work is implemented. Incorrect factual summaries are release blockers.

Evaluate model comprehension separately from strategic action quality. Use labeled Choice questions with answers computed from the game state:

- Which seat are you, and whose turn is it?
- Who acts before you on this street?
- Which card was dealt on the turn, and is the river known?
- Who most recently bet or raised on this street?
- How many additional chips does calling cost?
- How much do you pay to raise to a stated total?
- How many chips remain after that action?
- Which pots can you win after the proposed call? (future side-pot work)
- What is your current made-hand category? (future hand-category evaluation)
- Are an opponent's private cards known?

Include heads-up and multiway hands, unopened streets, reraises, folds, check-raises, calls facing raises, short all-in calls, side pots, and long histories. Paired cases should change one fact and require the corresponding answer to change. Vary seat labels and absolute chip scale to detect reliance on superficial cues.

Compare the existing observation and proposed rendering on the same held-out cases, checkpoint, inference settings, and neutral objective. Record sample counts, accuracy by question type and scenario, uncertainty, input tokens, and latency. Report critical errors individually, particularly hidden-card claims, wrong actor, wrong payment, and wrong pot eligibility. Overall accuracy must not conceal failures in these groups.

Set numerical acceptance thresholds and the held-out evaluation set before comparing formats. No measured threshold or passing result exists yet. Understanding tests can support a claim that the model reads the state correctly; solver agreement, playing strength, and personality behavior require separate evaluations.

## Current implementation gaps

The [observation builder](observation.ts) supplies an allowlisted structured facts projection. The [request builder](kev-api.ts) renders that projection once as an English state string, then appends a separate “Your playing personality” section with the acting bot's current style and preferences. [Candidate generation](candidates.ts) supplies the unchanged action descriptions, including payments and remaining chips.

The rendered state explicitly names the actor and position, separates board streets, expands card names, defines chip amounts, and uses one chronological action history. The structured projection is internal and is not duplicated in the request. The former `limped` summary treated the first voluntary preflop call as a limp even when it faced a raise; it is no longer sent to the model. Made-hand facts and side-pot eligibility remain future work, along with the decision about whether to calculate hand strength inside or outside the model. The wording change has not been evaluated with a model and does not establish better understanding or play.

This document is the review target for the current renderer. Its examples remain illustrative and do not claim model performance.
