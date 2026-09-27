"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { PlayingCard } from "./board";
import { OrderCheck } from "./order-check";
import { REPLAY_HANDS } from "@/lib/poker/replay-hands";
import { actionLabel, replayFrames, replayReport, replayRequest, type ReplayDecision } from "@/lib/poker/replay";
import { askKev } from "@/lib/poker/kev-api";
import { position, potTotal } from "@/lib/poker/rules";
import { api } from "@/lib/kev";

type Review = { decisions: Record<number, ReplayDecision>; notes: Record<number, string> };
const EMPTY_REVIEW: Review = { decisions: {}, notes: {} };
const panel = "rounded-lg border border-border bg-card p-5";

export function PokerReplay() {
  const [handId, setHandId] = useState(REPLAY_HANDS[0].id);
  const [step, setStep] = useState(0);
  const [omniscient, setOmniscient] = useState(false);
  const [revealed, setRevealed] = useState(false);
  const [collection, setCollection] = useState("All hands");
  const [reviews, setReviews] = useState<Record<string, Review>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef<AbortController | null>(null);
  const hand = REPLAY_HANDS.find((item) => item.id === handId)!;
  const frames = useMemo(() => replayFrames(hand), [hand]);
  const frame = frames[step];
  const { game, historicalAction } = frame;
  const review = reviews[handId] ?? EMPTY_REVIEW;
  const decision = review.decisions[step];
  const model = decision?.modelInfo?.models.find((item) => item.name === "kev-latest")?.run;
  const actor = game.actor;
  const canEvaluate = actor !== null && hand.players[actor].hole.length === 2;
  const request = canEvaluate ? replayRequest(hand, frame) : null;
  const visibleHands = REPLAY_HANDS.filter((item) => collection === "All hands" || item.collection === collection);
  const evaluated = Object.keys(review.decisions).length;
  const historicalOffered = historicalAction && request?.candidates.some((candidate) => actionLabel(candidate.action) === actionLabel(historicalAction));

  useEffect(() => () => pending.current?.abort(), []);

  function navigate(nextStep: number, nextHand = handId) {
    pending.current?.abort();
    pending.current = null;
    setBusy(false); setError(""); setRevealed(false);
    setHandId(nextHand); setStep(nextStep);
  }

  async function evaluateDecision() {
    if (!canEvaluate || pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true); setError("");
    const timer = setTimeout(() => controller.abort(new Error("The model did not respond within 60 seconds.")), 60_000);
    try {
      const [response, modelInfo] = await Promise.all([
        askKev(game, controller.signal),
        api.models(controller.signal).catch(() => null),
      ]);
      if (pending.current !== controller || controller.signal.aborted) return;
      setReviews((previous) => {
        const saved = previous[handId] ?? EMPTY_REVIEW;
        return { ...previous, [handId]: { ...saved, decisions: { ...saved.decisions, [step]: { ...response, modelInfo } } } };
      });
      setRevealed(true);
    } catch (failure) {
      if (pending.current === controller) setError(controller.signal.aborted ? "The model request timed out. Retry this decision." : failure instanceof Error ? failure.message : "The model request failed.");
    } finally {
      clearTimeout(timer);
      if (pending.current === controller) { pending.current = null; setBusy(false); }
    }
  }

  function exportReview() {
    const url = URL.createObjectURL(new Blob([JSON.stringify(replayReport(hand, review.decisions, review.notes), null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url; link.download = `${hand.id}-review.json`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  return <main className="mx-auto w-full max-w-6xl px-6 pb-16 pt-8 md:px-10">
    <header className="flex flex-wrap items-baseline justify-between gap-3">
      <nav aria-label="Playground" className="flex flex-wrap gap-4 text-[15px]">
        <Link href="/" className="text-muted-foreground hover:text-foreground">kev</Link>
        <Link href="/chess" className="text-muted-foreground hover:text-foreground">chess</Link>
        <Link href="/poker" className="text-muted-foreground hover:text-foreground">poker</Link>
        <Link href="/poker/replay" aria-current="page" className="font-medium">hand review</Link>
      </nav>
      <span className="text-xs text-muted-foreground">51 source-backed hands · 502 decisions</span>
    </header>
    <div className="mt-10 max-w-3xl">
      <h1 className="text-2xl font-medium tracking-tight">Replay a hand. Inspect the decision.</h1>
      <p className="mt-2 text-sm leading-6 text-muted-foreground">Step through one televised classic and 50 consecutive Pluribus research hands. Ask Kev what it would do with the information available at that moment.</p>
      <p className="mt-2 text-xs leading-5 text-muted-foreground">Historical actions are references, not correct answers. This collection has no solver EV labels and does not establish playing strength.</p>
    </div>
    <div className="mt-7 flex flex-wrap items-end gap-4">
      <label className="flex flex-col gap-2 text-xs text-muted-foreground">Collection
        <select value={collection} onChange={(event) => { const value = event.target.value; setCollection(value); navigate(0, REPLAY_HANDS.find((item) => value === "All hands" || item.collection === value)!.id); }} className="h-10 rounded-md border border-border bg-background px-3 text-sm text-foreground">
          {["All hands", "Classic", "Pluribus"].map((value) => <option key={value}>{value}</option>)}
        </select>
      </label>
      <label className="flex min-w-0 flex-1 flex-col gap-2 text-xs text-muted-foreground">Hand
        <select value={handId} onChange={(event) => navigate(0, event.target.value)} className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm text-foreground">
          {visibleHands.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
        </select>
      </label>
      <Button variant="outline" onClick={exportReview}>Export review</Button>
    </div>
    <div className="mt-4 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
      <a href={hand.source} target="_blank" rel="noreferrer" className="underline underline-offset-4">Pinned PHH source ↗</a>
      {hand.video && <a href={hand.video} target="_blank" rel="noreferrer" className="underline underline-offset-4">Original video ↗</a>}
      <span>Blinds {hand.smallBlind.toLocaleString()} / {hand.bigBlind.toLocaleString()} · ante {hand.ante} · no rake modeled</span>
    </div>
    <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
      <div className="min-w-0 space-y-5">
        <section className="rounded-xl border border-border bg-muted/20 p-5" aria-label="Hand replay">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm capitalize" aria-live="polite">{game.complete ? "Hand complete" : `${game.street} · ${game.players[actor!].name} to act`}</p>
            <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={omniscient} onChange={(event) => setOmniscient(event.target.checked)} />God view</label>
          </div>
          <div className="py-7 text-center">
            <p className="text-xs text-muted-foreground">{game.complete ? "Awarded pot" : "Pot"}</p>
            <p className="mt-1 font-mono text-3xl tabular-nums">{(game.complete ? game.pots.reduce((sum, pot) => sum + pot.amount, 0) : potTotal(game)).toLocaleString()}</p>
            <div className="mt-5 flex justify-center gap-2" aria-label="Community cards">{Array.from({ length: 5 }, (_, index) => <PlayingCard key={index} card={game.board[index]} />)}</div>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {game.players.map((player) => {
              const known = hand.players[player.seat].hole;
              const show = omniscient || player.seat === actor || game.showdown && player.status !== "folded";
              return <div key={player.seat} className={`rounded-lg border bg-card p-3 ${player.seat === actor ? "border-foreground" : "border-border"}`}>
                <div className="flex items-start justify-between gap-2"><div><p className="text-sm font-medium">{player.name}</p><p className="mt-1 text-xs text-muted-foreground">{position(game, player.seat)} · {player.status}</p></div><span className="font-mono text-sm">{player.stack.toLocaleString()}</span></div>
                <div className="mt-3 flex items-center gap-2">
                  {show && known.length === 2 ? known.map((card) => <PlayingCard key={card} card={card} />) : <span className="flex h-16 items-center text-xs text-muted-foreground sm:h-20">{known.length ? "Private cards hidden" : "Cards not recorded"}</span>}
                  <span className="ml-auto text-right text-xs text-muted-foreground">This street<br /><span className="font-mono">{player.streetBet.toLocaleString()}</span></span>
                </div>
              </div>;
            })}
          </div>
          <p className="mt-4 text-xs leading-5 text-muted-foreground">{omniscient ? "God view reveals recorded hole cards for your review. Kev still receives only the acting player's cards and public history." : "Player view follows the acting seat. Opponents' cards stay hidden until showdown."}</p>
        </section>
        <section className={panel}>
          <label htmlFor="replay-step" className="text-sm">{game.complete ? "Result" : `Decision ${step + 1}`} / {frames.length - 1}</label>
          <input id="replay-step" aria-label="Replay position" type="range" min={0} max={frames.length - 1} value={step} onChange={(event) => navigate(Number(event.target.value))} className="mt-4 w-full accent-foreground" />
          <div className="mt-3 flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => navigate(0)} disabled={step === 0}>Start</Button>
            <Button variant="outline" onClick={() => navigate(step - 1)} disabled={step === 0}>Previous</Button>
            <Button onClick={() => navigate(step + 1)} disabled={game.complete}>Next action</Button>
            <Button variant="outline" onClick={() => navigate(frames.length - 1)} disabled={game.complete}>Result</Button>
          </div>
          <p className="mt-3 text-xs leading-5 text-muted-foreground">Navigation follows the recorded hand. A model suggestion does not change the historical continuation.</p>
        </section>
        <section className={panel}>
          <h2 className="text-sm font-medium">Public action history</h2>
          <ol className="mt-3 max-h-64 space-y-2 overflow-y-auto text-xs text-muted-foreground">{game.history.map((event, index) => <li key={index}><span className="capitalize">{event.street}</span> · {event.seat === null ? "Board" : game.players[event.seat].name}: {event.text}</li>)}</ol>
        </section>
      </div>
      <aside className="min-w-0 space-y-5">
        <section className={panel}>
          <h2 className="text-sm font-medium">Kev at this decision</h2>
          <p className="mt-2 text-xs leading-5 text-muted-foreground">Fixed Balanced profile · {evaluated} / {frames.length - 1} decisions reviewed</p>
          {decision && <p className="mt-1 break-all font-mono text-xs text-muted-foreground">{model ?? "Checkpoint metadata unavailable"}</p>}
          <div className="mt-4 flex gap-2"><Button onClick={() => void evaluateDecision()} disabled={!canEvaluate || busy}>{busy ? "Asking Kev…" : decision ? "Run again" : "Ask Kev"}</Button>
            {busy && <Button variant="outline" onClick={() => navigate(step)}>Cancel</Button>}
          </div>
          {!canEvaluate && <p className="mt-3 text-xs text-muted-foreground">{game.complete ? "The hand is complete. Go back to a decision to ask Kev." : "The acting player's hole cards are missing from the source. Model evaluation is unavailable here."}</p>}
          {error && <p role="alert" className="mt-3 break-words text-sm text-destructive">{error}</p>}
          {decision && <div className="mt-5 space-y-3" aria-live="polite">
            <p className="text-sm">Kev chooses <strong>{actionLabel(decision.selected.action)}</strong> · {decision.latencyMs.toFixed(0)} ms</p>
            {decision.candidates.map((candidate) => <div key={candidate.id} title={candidate.description} className="grid grid-cols-[minmax(0,1fr)_5rem_3rem] items-center gap-2 text-xs">
              <span className={candidate.id === decision.selected.id ? "font-medium" : "text-muted-foreground"}>{actionLabel(candidate.action)}</span>
              <span className="h-1 rounded bg-muted"><span className="block h-1 rounded bg-foreground" style={{ width: `${decision.probabilities[candidate.id] * 100}%` }} /></span>
              <span className="text-right font-mono">{(decision.probabilities[candidate.id] * 100).toFixed(1)}%</span>
            </div>)}
          </div>}
          {decision && <OrderCheck decision={decision} />}
          <p className="mt-4 text-xs leading-5 text-muted-foreground">Averaged action probabilities are not winning odds. Bet sizes come from the same legal candidate generator as live play, without access to the recorded action.</p>
        </section>
        {historicalAction && <section className={panel}>
          <h2 className="text-sm font-medium">Historical reference</h2>
          {revealed || decision ? <><p className="mt-3 text-sm">{game.players[actor!].name}: <strong>{actionLabel(historicalAction)}</strong></p>
            {request && <p className="mt-2 text-xs leading-5 text-muted-foreground">{historicalOffered ? "This recorded action is available in Kev's candidates." : "The exact historical bet size is absent from Kev's candidates. It has not been added as an answer hint."}</p>}
            <p className="mt-3 text-xs leading-5 text-muted-foreground">Agreement does not establish correctness. EV loss is unavailable without a solver reference.</p>
          </> : <Button variant="outline" className="mt-3" onClick={() => setRevealed(true)}>Reveal recorded action</Button>}
        </section>}
        {game.complete && <section className={panel}><h2 className="text-sm font-medium">Recorded outcome</h2>
          <ul className="mt-3 space-y-2 text-sm">{game.players.map((player) => { const net = player.stack - hand.players[player.seat].stack; return <li key={player.seat} className="flex justify-between gap-2"><span>{player.name}</span><span className="font-mono">{net > 0 ? "+" : ""}{net.toLocaleString()}</span></li>; })}</ul>
          <p className="mt-3 text-xs text-muted-foreground">Realized chips, not a score for decision quality.</p></section>}
        {!game.complete && <section className={panel}>
          <label htmlFor="review-note" className="text-sm font-medium">Review notes</label>
          <textarea id="review-note" value={review.notes[step] ?? ""} onChange={(event) => { const note = event.target.value; setReviews((previous) => { const saved = previous[handId] ?? EMPTY_REVIEW; return { ...previous, [handId]: { ...saved, notes: { ...saved.notes, [step]: note } } }; }); }} placeholder="What needs investigation: sizing, range inference, pot odds…" rows={4} className="mt-3 w-full resize-y rounded-md border border-border bg-background p-3 text-sm" />
          <p className="mt-2 text-xs leading-5 text-muted-foreground">Notes and decisions stay in this page until refresh. Export this hand&apos;s review to keep them.</p>
        </section>}
        {request && <details className={panel}><summary className="cursor-pointer text-sm">Exact model input</summary><pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-words text-xs leading-5">{JSON.stringify(request.request, null, 2)}</pre></details>}
      </aside>
    </div>
  </main>;
}
