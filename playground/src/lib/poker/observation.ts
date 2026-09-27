import { inHand, legalActions, position, potTotal } from "./rules";
import type { Card, GameEvent, GameState, Street } from "./types";

const STREETS: Street[] = ["preflop", "flop", "turn", "river"];
const RANKS: Record<string, string> = {
  A: "Ace", K: "King", Q: "Queen", J: "Jack", T: "Ten", "9": "Nine", "8": "Eight",
  "7": "Seven", "6": "Six", "5": "Five", "4": "Four", "3": "Three", "2": "Two",
};
const SUITS: Record<string, string> = { s: "spades", h: "hearts", d: "diamonds", c: "clubs" };
const POSITIONS: Record<string, string> = {
  BTN: "dealer button", "BTN / SB": "dealer button and small blind", SB: "small blind", BB: "big blind",
  CO: "cutoff", HJ: "hijack", LJ: "lojack", UTG: "under the gun", "UTG+1": "one seat after under the gun",
  "UTG+2": "two seats after under the gun", MP: "middle position", Out: "out of the game",
};

const seatName = (seat: number) => `Seat ${seat + 1}`;
const cardName = (card: Card) => `${RANKS[card[0]] ?? card[0]} of ${SUITS[card[1]]}`;
const positionName = (s: GameState, seat: number) => POSITIONS[position(s, seat)] ?? position(s, seat);

function actionLine(e: GameEvent): string {
  const actor = seatName(e.seat!);
  const allIn = e.allIn ? " and is all-in" : "";
  if (e.kind === "ante") return `${actor} posted an ante of ${e.pay} chips.`;
  if (e.kind === "post") return `${actor} posted ${e.pay} chips as a blind${allIn}.`;
  if (e.kind === "fold") return `${actor} folded.`;
  if (e.kind === "check") return `${actor} checked and paid 0 chips.`;
  if (e.kind === "call") return `${actor} called and paid ${e.pay} chips${allIn}.`;
  return `${actor} ${e.kind === "bet" ? "bet" : "raised"} to ${e.to} chips total this street, paying ${e.pay} chips now${allIn}.`;
}

function history(s: GameState) {
  const board: Partial<Record<Street, string>> = {
    flop: s.board.length >= 3 ? s.board.slice(0, 3).map(cardName).join(", ") : undefined,
    turn: s.board.length >= 4 ? cardName(s.board[3]) : undefined,
    river: s.board.length >= 5 ? cardName(s.board[4]) : undefined,
  };
  return STREETS.filter((street) => street === s.street || board[street] !== undefined || s.history.some((e) => e.street === street)).map((street) => {
    const actions = s.history.filter((e) => e.street === street && e.seat !== null
      && ["ante", "post", "fold", "check", "call", "bet", "raise", "refund"].includes(e.kind))
      .map((e) => e.kind === "refund" ? `${seatName(e.seat!)} received ${e.pay} uncalled chips back.` : actionLine(e));
    return { street, ...(board[street] ? { community_cards_dealt: board[street] } : {}),
      actions: actions.length ? actions : ["No actions yet on this street."] };
  });
}

// Explicit allowlist: neither the deck nor opponents' private cards reach the model.
export function observation(s: GameState) {
  if (s.actor === null || s.complete) throw new Error("No player needs a decision.");
  const hero = s.players[s.actor];
  const legal = legalActions(s);
  const call = legal.call;
  const currentActions = s.history.filter((e) => e.street === s.street && (e.kind === "bet" || e.kind === "raise"));
  const lastBet = currentActions.at(-1);
  const activeSeats = s.players.filter((p) => p.status === "active").map((p) => p.seat);
  const liveSeats = s.players.filter(inHand).map((p) => p.seat);
  const postflop = s.street !== "preflop";
  const firstSeat = postflop ? s.dealer : s.bigBlindSeat;
  const actionOrder = [...activeSeats.filter((seat) => seat > firstSeat), ...activeSeats.filter((seat) => seat <= firstSeat)];
  const canAggress = legal.aggression !== null;
  return {
    game: `No-limit Texas Hold'em with integer play chips, no rake, and ${s.ante ? `an ante of ${s.ante} chips per player` : "no ante"}. The blinds are ${s.smallBlind} and ${s.bigBlind} chips. Players make their best five-card hand from any five of their two private cards and the shared community cards. The table has ${s.players.length} seats; ${liveSeats.length} players remain in the hand, ${activeSeats.length} can still bet, and ${s.players.filter((p) => p.status === "folded").length} have folded.`,
    perspective: `You are ${seatName(hero.seat)}, the ${positionName(s, hero.seat)}. It is your turn on the ${s.street}. ${seatName(s.dealer)} is the dealer button. Normal ${s.street} action order among players who can still bet: ${actionOrder.map(seatName).join(", ")}. Players may act again after a raise. ${s.dealer === s.smallBlindSeat ? `This hand started heads-up: the button is also the small blind; the button acts first preflop and last after the flop.` : ""}`,
    private_cards: hero.hole.map(cardName),
    community_cards: {
      flop: s.board.length >= 3 ? s.board.slice(0, 3).map(cardName) : "Not dealt",
      turn: s.board.length >= 4 ? cardName(s.board[3]) : "Not dealt",
      river: s.board.length >= 5 ? cardName(s.board[4]) : "Not dealt",
    },
    current_decision: `${lastBet ? `${seatName(lastBet.seat!)} most recently ${lastBet.kind === "bet" ? "bet" : "raised"} to ${lastBet.to} chips this street. ` : "No player has bet or raised on this street yet. "}The current amount to match this street is ${s.currentBet} chips. You have paid ${hero.streetBet} chips this street. ${call ? `Calling costs ${call} additional chips${call === hero.stack ? " and puts you all-in" : ""}.` : "You owe 0 chips now and may check."} ${canAggress ? "A bet or raise is available." : "No bet or raise is available in this situation."}`,
    chips: {
      current_pot_including_all_bets: `${potTotal(s)} chips`,
      terms: "Stack means uncommitted chips remaining. Street contribution is already paid this betting round. Hand contribution is already paid across the hand. A raise-to amount is the player's new street total, not the additional payment.",
      players: s.players.filter((p) => p.status !== "out").map((p) => ({
        seat: seatName(p.seat), position: positionName(s, p.seat),
        status: p.status === "active" ? "in the hand and can still bet"
          : p.status === "all-in" ? "all-in; still in the hand but cannot bet again" : "folded; no longer eligible to win",
        stack_remaining: `${p.stack} chips`, street_contribution: `${p.streetBet} chips`,
        hand_contribution: `${p.committed} chips`,
      })),
    },
    public_history: history(s),
    unknown_information: "Other players' private cards and future community cards are unknown. Their actions do not reveal their actual cards or intent.",
  };
}

export type PokerObservation = ReturnType<typeof observation>;

export type PlayingPersonality = { style: string; preferences: string };

/** Render only allowlisted public facts plus the acting bot's stated preferences. */
export function renderObservation(facts: PokerObservation, personality: PlayingPersonality): string {
  const lines = [
    facts.game,
    facts.perspective,
    `Your private cards: ${facts.private_cards.join(", ")}.`,
    "Public cards and actions by street:",
    ...facts.public_history.flatMap((street) => [
      `${street.street[0].toUpperCase()}${street.street.slice(1)}: ${street.community_cards_dealt ? `community cards dealt: ${street.community_cards_dealt}.` : "no new community cards were dealt."}`,
      ...street.actions.map((action, index) => `${index + 1}. ${action}`),
    ]),
    `Future community cards not yet dealt: ${[
      ...(facts.community_cards.flop === "Not dealt" ? ["flop"] : []),
      ...(facts.community_cards.turn === "Not dealt" ? ["turn"] : []),
      ...(facts.community_cards.river === "Not dealt" ? ["river"] : []),
    ].join(", ") || "none; all community cards have been dealt"}.`,
    "Current player status and chip contributions:",
    ...facts.chips.players.map((player) => `${player.seat} (${player.position}): ${player.status}; ${player.stack_remaining} remaining; ${player.street_contribution} contributed this street; ${player.hand_contribution} contributed this hand.`),
    `The pot contains ${facts.chips.current_pot_including_all_bets}. ${facts.chips.terms}`,
    facts.current_decision,
    facts.unknown_information,
    "Your playing personality",
    `Style: ${personality.style}.`,
    `Preferences: ${personality.preferences}`,
  ];
  return lines.join("\n");
}
