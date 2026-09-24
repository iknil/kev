import test from "node:test";
import assert from "node:assert/strict";
import { deck, shuffle } from "../src/lib/poker/cards";
import { evaluate } from "../src/lib/poker/evaluate";
import { createGame, startHand, act } from "../src/lib/poker/engine";
import { legalActions, potTotal, validateAction, position } from "../src/lib/poker/rules";
import { actionCandidates } from "../src/lib/poker/candidates";
import { observation } from "../src/lib/poker/observation";
import { askKev, decisionRequest, decisionIsCurrent } from "../src/lib/poker/kev-api";
import { PokerController } from "../src/lib/poker/controller";
import { settle } from "../src/lib/poker/settlement";
import { POKER_PERSONAS } from "../src/lib/poker/personas";
import type { Card, GameState } from "../src/lib/poker/types";
import type { SystemOneResponse, SystemOneRequest } from "../src/lib/kev";

const cards = (s: string) => s.split(" ") as Card[];
const game = (seats = 6) => createGame({ id: "test", mode: "self", seats, cards: deck() });
const move = (s: GameState, action: Parameters<typeof act>[2]) => act(s, s.actor!, action);
const sum = (s: GameState) => s.players.reduce((n, p) => n + p.stack + p.committed, 0);
function streetState(street: GameState["street"] = "flop") {
  const s = game();
  s.street = street; s.currentBet = 0; s.lastFullRaise = 20; s.actor = 0;
  for (const p of s.players) { p.stack = 1000; p.streetBet = 0; p.committed = 0; p.actedAt = null; }
  return s;
}
function reply(request: SystemOneRequest): SystemOneResponse {
  const question = request.questions.action;
  assert.equal(question.type, "choice");
  const ids = Object.keys(question.criteria!);
  return { model: "kev-latest", latency_ms: 1, usage: { input_tokens: 1, output_tokens: 1 },
    answers: { action: { type: "choice", choice: ids[0], confidence: 1, probabilities: Object.fromEntries(ids.map((id, i) => [id, i === 0 ? 1 : 0])) } } };
}

test("deck, shuffling, input validation and immutable moves", () => {
  assert.equal(new Set(shuffle(deck())).size, 52);
  assert.throws(() => createGame({ id: "x", mode: "self", seats: 6, cards: Array(52).fill("As") }));
  const s = game(); const saved = structuredClone(s);
  assert.equal(s.actor, 3); assert.equal(s.smallBlindSeat, 1); assert.equal(s.bigBlindSeat, 2);
  move(s, { type: "call" }); assert.deepEqual(s, saved);
  assert.throws(() => act(s, 0, { type: "fold" }));
  assert.throws(() => move(s, { type: "raise", to: 21 }));
  assert.throws(() => move(s, { type: "raise", to: 40.5 }));
});

test("heads-up dealer acts first preflop, BB acts first postflop; dealer rotates", () => {
  let s = game(2);
  assert.equal(s.dealer, 0); assert.equal(s.actor, 0); assert.equal(position(s, 0), "BTN / SB");
  s = move(s, { type: "call" });
  assert.equal(s.actor, 1); assert.equal(legalActions(s).check, true);
  s = move(s, { type: "check" }); assert.equal(s.street, "flop"); assert.equal(s.actor, 1);
  while (!s.complete) s = move(s, { type: "check" });
  s = startHand(s, deck()); assert.equal(s.dealer, 1); assert.equal(s.bigBlindSeat, 0); assert.equal(s.actor, 1);
});

test("BB retains its option after limps; board burns and street progression", () => {
  let s = game();
  for (let i = 0; i < 5; i++) s = move(s, { type: "call" });
  assert.equal(s.actor, s.bigBlindSeat); assert.equal(s.street, "preflop");
  s = move(s, { type: "check" });
  assert.equal(s.board.length, 3); assert.equal(s.deck.length, 36);
  assert.equal(s.actor, s.smallBlindSeat); assert.equal(s.currentBet, 0);
  assert.equal(new Set([...s.players.flatMap((p) => p.hole), ...s.board, ...s.deck]).size, 51);
});

test("short all-in does not reopen a caller; cumulative shorts do", () => {
  let s = streetState(); s.players[2].stack = 150; s.players[3].stack = 200;
  s = move(s, { type: "bet", to: 100 });
  s = move(s, { type: "call" });
  s = move(s, { type: "raise", to: 150 });
  assert.equal(s.lastFullRaise, 100);
  const loneShort = structuredClone(s);
  for (let i = 0; i < 3; i++) s = move(s, { type: "fold" });
  assert.equal(s.actor, 0); assert.equal(legalActions(s).aggression, null);
  s = loneShort;
  s = move(s, { type: "raise", to: 200 });
  s = move(s, { type: "fold" }); s = move(s, { type: "fold" });
  assert.equal(s.actor, 0); assert.equal(legalActions(s).aggression?.minTo, 300);
});

test("checking does not prevent raising a later short opening all-in", () => {
  let s = streetState(); s.players[1].stack = 10;
  s = move(s, { type: "check" }); s = move(s, { type: "bet", to: 10 });
  for (let i = 0; i < 4; i++) s = move(s, { type: "call" });
  assert.equal(s.actor, 0); assert.equal(legalActions(s).aggression?.minTo, 30);
});

test("short BB retains full bring-in with several active opponents", () => {
  let s = game(); s.complete = true; s.hand = 0;
  s.players.forEach((p) => { p.stack = p.seat === 2 ? 5 : 100; });
  s = startHand(s, deck());
  assert.equal(legalActions(s).call, 20); assert.equal(legalActions(s).aggression?.minTo, 40);
});

test("one opponent all-in: no dry side-pot betting; short calls run out and refund", () => {
  let s = game(2);
  s.complete = true; s.hand = 0; s.players[0].stack = 100; s.players[1].stack = 15;
  s = startHand(s, deck());
  assert.equal(legalActions(s).call, 5); assert.equal(legalActions(s).aggression, null);
  s = move(s, { type: "call" });
  assert.equal(s.complete, true); assert.equal(s.board.length, 5); assert.equal(sum(s), 115);
  s = game(2); s = move(s, { type: "raise", to: 100 });
  s = move(s, { type: "fold" });
  assert.equal(s.complete, true); assert.equal(s.board.length, 0);
  assert.equal(s.pots[0].amount, 40); assert.equal(sum(s), 4000);
  assert.ok(s.history.some((e) => e.text === "Uncalled 80 returned"));
});

test("all hand categories, wheel, kickers, two trips and board ties", () => {
  const hands = ["As Jd 9c 6h 3s", "As Ad 9c 6h 3s", "As Ad 9c 9h 3s", "As Ad Ac 6h 3s", "As 2d 3c 4h 5s", "As Js 9s 6s 3s", "As Ad Ac 6h 6s", "As Ad Ac Ah 3s", "9s Ts Js Qs Ks"];
  const ranks = hands.map((h) => evaluate(cards(h)));
  for (let i = 1; i < ranks.length; i++) assert.ok(ranks[i].score > ranks[i - 1].score);
  assert.ok(evaluate(cards("2s 3h 4d 5c 6s")).score > evaluate(cards(hands[4])).score);
  assert.ok(evaluate(cards("As Ad Kc 8h 3s")).score > evaluate(cards("Ah Ac Qc Jh 9s")).score);
  assert.equal(evaluate(cards("As Ad Ac Kh Kd Kc 2s")).score, evaluate(cards("As Ad Ac Kh Kd")).score);
  assert.equal(evaluate(cards("As Ks Qs Js Ts 2d 3h")).score, evaluate(cards("As Ks Qs Js Ts 4d 5h")).score);
});

test("main pot, side pot and uncalled excess award independently", () => {
  const s = streetState("river"); s.board = cards("2s 3h 7d 9c Js");
  s.players.forEach((p, i) => { p.stack = 0; p.status = i < 3 ? "all-in" : "out"; p.committed = [100, 200, 300][i] ?? 0; });
  s.players[0].hole = cards("As Ah"); s.players[1].hole = cards("Ks Kh"); s.players[2].hole = cards("Qs Qh");
  settle(s);
  assert.deepEqual(s.pots.map((p) => p.amount), [300, 200]);
  assert.deepEqual(s.players.slice(0, 3).map((p) => p.stack), [300, 200, 100]);
  assert.equal(sum(s), 600);
});

test("ties split with odd chips clockwise after dealer; folded hands cannot win", () => {
  const s = streetState("river"); s.dealer = 0; s.board = cards("As Ks Qs Js Ts");
  s.players.forEach((p, i) => { p.stack = 0; p.committed = i < 3 ? 5 : 0; p.status = i < 2 ? "all-in" : i === 2 ? "folded" : "out"; });
  s.players[0].hole = cards("2d 3d"); s.players[1].hole = cards("4d 5d");
  settle(s); assert.equal(s.players[1].stack, 8); assert.equal(s.players[0].stack, 7); assert.equal(s.players[2].stack, 0);
});

test("candidate schedules: postflop raises use pot after calling; amounts unique and legal", () => {
  const s = streetState(); s.currentBet = 100; s.lastFullRaise = 100;
  s.players[0].streetBet = 20; s.players[0].committed = 20; s.players[0].stack = 980;
  s.players[1].committed = 280;
  const options = actionCandidates(s);
  assert.deepEqual(options.map((c) => c.id), ["fold", "call", "raise_to_200", "raise_to_290", "raise_to_480", "raise_to_1000"]);
  for (const c of options) validateAction(s, c.action);
  s.players[0].stack = 90;
  assert.deepEqual(actionCandidates(s).map((c) => c.id), ["fold", "call", "raise_to_110"]);
  const opening = actionCandidates(game());
  assert.deepEqual(opening.map((c) => c.id), ["fold", "call", "raise_to_40", "raise_to_50", "raise_to_60", "raise_to_2000"]);
});

test("20 distinct personas and API observation excludes hidden information", () => {
  assert.equal(POKER_PERSONAS.length, 20); assert.equal(new Set(POKER_PERSONAS.map((p) => p.id)).size, 20);
  const s = game(); const original = decisionRequest(s);
  const changed = structuredClone(s);
  changed.deck.reverse(); changed.players[0].hole = cards("Ah Ad");
  assert.deepEqual(decisionRequest(changed), original);
  const state = observation(s);
  assert.deepEqual(state.hero.hole_cards, s.players[s.actor!].hole);
  const blob = JSON.stringify(state);
  assert.ok(!blob.includes('"deck"'));
  assert.equal("recent_actions" in state, false);
  assert.ok(state.streets[0].order.some((e) => e.did === "post"));
});

test("observation reports per-street lines, put-in, limp and check-raise", () => {
  let s = streetState();
  s = move(s, { type: "check" });
  s = move(s, { type: "bet", to: 100 });
  s = move(s, { type: "fold" });
  s = move(s, { type: "fold" });
  s = move(s, { type: "fold" });
  s = move(s, { type: "fold" });
  s = move(s, { type: "raise", to: 300 });
  const flop = observation(s).streets.find((st) => st.street === "flop")!;
  const hero = flop.players.find((p) => p.seat === 1)!;
  const bettor = flop.players.find((p) => p.seat === 2)!;
  assert.equal(hero.check_raised, true); assert.equal(hero.checked, true); assert.equal(hero.raised, true);
  assert.equal(hero.put_in, 300); assert.match(hero.line, /check; raise to 300/);
  assert.equal(bettor.bet, true); assert.equal(bettor.check_raised, false); assert.equal(bettor.put_in, 100);
  assert.deepEqual(flop.order.map((e) => e.did), ["check", "bet", "fold", "fold", "fold", "fold", "raise"]);
  let pre = game();
  pre = move(pre, { type: "call" });
  const utg = observation(pre).streets[0].players.find((p) => p.seat === 4)!;
  assert.equal(utg.limped, true); assert.equal(utg.put_in, 20);
});

test("Kev adapter rejects unknown actions and malformed distributions", async () => {
  const s = game();
  const d = await askKev(s, undefined, async (req) => reply(req));
  assert.ok(decisionIsCurrent(s, d));
  assert.equal(decisionIsCurrent(move(s, d.selected.action), d), false);
  await assert.rejects(askKev(s, undefined, async (req) => { const r = reply(req); r.answers.action = { type: "choice", choice: "invented", confidence: 1, probabilities: {} }; return r; }));
  await assert.rejects(askKev(s, undefined, async (req) => { const r = reply(req); if (r.answers.action.type === "choice") r.answers.action.probabilities.fold = NaN; return r; }));
});

test("controller ignores canceled and stale replies; duplicate step sends once", async () => {
  const controller = new PokerController(); let s = game(); let commits = 0; let calls = 0;
  let resolve!: (r: SystemOneResponse) => void; let request!: SystemOneRequest;
  const provider = (req: SystemOneRequest) => { calls++; request = req; return new Promise<SystemOneResponse>((r) => { resolve = r; }); };
  const first = controller.step(() => s, () => { commits++; }, provider);
  await controller.step(() => s, () => { commits++; }, provider); assert.equal(calls, 1);
  controller.cancel(); resolve(reply(request)); await first; assert.equal(commits, 0);
  const second = controller.step(() => s, () => { commits++; }, provider);
  s = move(s, { type: "fold" }); resolve(reply(request)); await second; assert.equal(commits, 0);
  await controller.step(() => s, (next) => { s = next; commits++; }, async (req) => reply(req)); assert.equal(commits, 1);
});

test("seeded simulations conserve chips, terminate, and never generate illegal candidates", () => {
  let seed = 173;
  const random = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 2 ** 32; };
  for (const seats of [2, 6, 9, 10]) {
    for (let iteration = 0; iteration < 25; iteration++) {
      let s = createGame({ id: "simulation", seats, mode: "self", cards: shuffle(deck(), random) });
      for (let hand = 0; hand < 5; hand++) {
        let actions = 0;
        while (!s.complete) {
          const candidates = actionCandidates(s);
          assert.ok(candidates.length > 0 && candidates.length <= 8);
          assert.equal(new Set(candidates.map((c) => c.id)).size, candidates.length);
          candidates.forEach((c) => validateAction(s, c.action));
          s = move(s, candidates[Math.floor(random() * candidates.length)].action);
          assert.equal(sum(s), seats * 2000);
          assert.ok(s.players.every((p) => p.stack >= 0 && p.committed >= 0));
          assert.ok(++actions < 300, "hand must terminate");
        }
        assert.equal(potTotal(s), 0);
        if (s.players.filter((p) => p.stack > 0).length < 2) break;
        s = startHand(s, shuffle(deck(), random));
      }
    }
  }
});

test("folded contribution levels merge before odd chips are allocated", () => {
  const s = streetState("river"); s.dealer = 0; s.board = cards("As Ks Qs Js Ts");
  s.players.forEach((p, i) => { p.stack = 0; p.committed = [3, 3, 3, 1, 2, 0][i]; p.status = i < 3 ? "all-in" : "folded"; });
  s.players[0].hole = cards("2d 3d"); s.players[1].hole = cards("4d 5d"); s.players[2].hole = cards("6d 7d");
  settle(s);
  assert.equal(s.pots.length, 1); assert.equal(s.pots[0].amount, 12);
  assert.deepEqual(s.players.slice(0, 3).map((p) => p.stack), [4, 4, 4]);
});

test("elimination to heads-up skips busted seats and rotates the big blind", () => {
  let s = game(); s.complete = true; s.hand = 2; s.dealer = 0; s.bigBlindSeat = 2;
  s.players.forEach((p) => { p.stack = [2, 5].includes(p.seat) ? 100 : 0; });
  s = startHand(s, deck());
  assert.equal(s.dealer, 2); assert.equal(s.smallBlindSeat, 2); assert.equal(s.bigBlindSeat, 5); assert.equal(s.actor, 2);
  assert.equal(s.players[0].status, "out"); assert.equal(s.players[0].hole.length, 0);
  assert.equal(position(s, 0), "Out");
});

test("a player's own last call determines cumulative reopening", () => {
  let s = streetState(); s.players[2].stack = 150; s.players[4].stack = 200;
  s = move(s, { type: "bet", to: 100 }); s = move(s, { type: "call" });
  s = move(s, { type: "raise", to: 150 }); s = move(s, { type: "call" });
  s = move(s, { type: "raise", to: 200 }); s = move(s, { type: "fold" });
  assert.ok(legalActions(s).aggression); s = move(s, { type: "call" }); s = move(s, { type: "call" });
  assert.equal(s.actor, 3); assert.equal(legalActions(s).aggression, null);
});

test("provider failure leaves the game unchanged and can be retried", async () => {
  const controller = new PokerController(); let s = game(); const before = structuredClone(s);
  const commit = (next: GameState) => { s = next; };
  await assert.rejects(controller.step(() => s, commit, async () => { throw new Error("offline"); }));
  assert.deepEqual(s, before);
  await controller.step(() => s, commit, async (req) => reply(req));
  assert.equal(s.revision, before.revision + 1);
});
