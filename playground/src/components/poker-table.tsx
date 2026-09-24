"use client";

import { useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { PokerBoard } from "@/components/poker/board";
import { ActionControls } from "@/components/poker/action-controls";
import { usePoker } from "@/components/poker/use-poker";
import { POKER_PERSONAS } from "@/lib/poker/personas";
import { TABLE_SIZES, inHand, position } from "@/lib/poker/rules";
import { cardLabel } from "@/lib/poker/cards";
import { evaluate } from "@/lib/poker/evaluate";
import type { Mode } from "@/lib/poker/types";

export function PokerTable() {
  const [mode, setMode] = useState<Mode>("human");
  const [seats, setSeats] = useState(6);
  const [observed, setObserved] = useState(0);
  const poker = usePoker();
  const { game, decision } = poker;
  const actor = game?.actor !== null && game?.actor !== undefined ? game.players[game.actor] : null;
  const funded = game?.players.filter((p) => p.stack > 0) ?? [];
  const newTable = () => { setObserved(0); poker.newTable(seats, mode); };

  return <main className="mx-auto flex w-full max-w-6xl flex-col px-6 pt-8 pb-16 md:px-10">
    <header className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
      <nav aria-label="Playground" className="flex items-baseline gap-4 text-[15px]">
        <Link href="/" className="text-muted-foreground hover:text-foreground">kev</Link>
        <Link href="/chess" className="text-muted-foreground hover:text-foreground">chess</Link>
        <Link href="/poker" aria-current="page" className="font-medium tracking-tight">poker</Link>
      </nav>
      <p className="text-[13px] text-muted-foreground">Texas Hold’em · Play chips</p>
    </header>
    <div className="mt-10 max-w-2xl">
      <h1 className="text-2xl font-medium tracking-tight">A seat at the table.</h1>
      <p className="mt-2 text-[15px] leading-6 text-muted-foreground">Play against AI personalities or watch them play each other. Kev chooses from legal actions with calculated bet sizes.</p>
    </div>
    <div className="mt-8 flex flex-wrap items-end justify-between gap-5">
      <fieldset className="flex flex-wrap gap-5 text-sm">
        <legend className="mb-2 text-[12px] text-muted-foreground">Game mode · changing settings clears the table</legend>
        {([{ value: "human", label: "You vs AI" }, { value: "self", label: "AI vs AI" }] as const).map((option) => <button key={option.value} type="button" aria-pressed={mode === option.value}
          onClick={() => { setMode(option.value); poker.reset(); }}
          className={`border-b pb-0.5 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring ${mode === option.value ? "border-foreground text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"}`}>{option.label}</button>)}
      </fieldset>
      <div className="flex flex-col gap-2">
        <Label htmlFor="table-size" className="text-[12px] font-normal text-muted-foreground">Table size</Label>
        <select id="table-size" value={seats} onChange={(e) => { setSeats(Number(e.target.value)); poker.reset(); }} className="h-9 rounded-md border border-border bg-background px-3 text-[13px]">
          {TABLE_SIZES.map((n) => <option key={n} value={n}>{n === 2 ? "Heads-up" : n === 6 ? "6-max" : "Full ring"} · {n} seats</option>)}
        </select>
      </div>
    </div>
    <div className="mt-6 flex flex-wrap items-center gap-2">
      <Button onClick={newTable} variant={game ? "outline" : "default"}>{game ? "Reset table" : "Start table"}</Button>
      {game && <>
        <Button onClick={poker.nextHand} disabled={!game.complete || funded.length < 2}>Next hand</Button>
        <Button variant="outline" onClick={poker.running ? poker.pause : poker.play} disabled={game.complete}>{poker.running && !game.complete ? "Pause AI" : "Play AI"}</Button>
        <Button variant="outline" onClick={() => void poker.step()} disabled={poker.running || poker.busy || !actor?.personaId}>AI step</Button>
      </>}
      <span className="ml-auto text-[12px] text-muted-foreground">Blinds 10 / 20 · starting stack 2,000 · no rake</span>
    </div>
    {poker.error && <div role="alert" className="mt-4 rounded-md border border-destructive/40 p-3 text-sm text-destructive">{poker.error}<span className="block mt-1">Play is paused. Use AI step to retry, or reset the table.</span></div>}
    {!game ? <div className="mt-8 rounded-lg border border-dashed border-border p-12 text-center text-sm text-muted-foreground">Start a {seats}-seat table to deal the first hand. AI step makes one decision; Play AI continues until the hand ends or it is your turn.</div>
      : <div className="mt-6 grid gap-10 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="flex min-w-0 flex-col gap-5">
          <p aria-live="polite" className="text-[13px] text-muted-foreground">
            Hand #{game.hand} · {game.complete ? funded.length < 2 ? `${funded[0]?.name} wins the table` : "Hand complete" : `${actor?.name} to act${poker.busy ? " · choosing" : ""}`}
          </p>
          <PokerBoard game={game} observed={Math.min(observed, seats - 1)} onObserve={setObserved} />
          {actor && !actor.personaId && <ActionControls key={`${game.id}-${game.revision}`} game={game} onAction={poker.humanAction} />}
          {game.complete && <section className="rounded-md border border-border bg-card p-4">
            <h2 className="text-sm font-medium">Settlement</h2>
            <ul className="mt-3 space-y-2 text-[13px] text-muted-foreground">
              {game.pots.map((pot, i) => <li key={i}>{i === 0 ? "Main pot" : `Side pot ${i}`}: {pot.amount} → {pot.winners.map((seat) => game.players[seat].name).join(", ")}</li>)}
            </ul>
            {game.showdown && <ul className="mt-4 space-y-2 text-[13px]">
              {game.players.filter(inHand).map((p) => <li key={p.seat}>{p.name}: <span className="font-mono">{p.hole.map(cardLabel).join(" ")}</span> · {evaluate([...p.hole, ...game.board]).name}</li>)}
            </ul>}
          </section>}
        </div>
        <aside className="flex min-w-0 flex-col gap-4">
          <section className="rounded-md border border-border bg-card p-4">
            <h2 className="font-mono text-[12px] text-muted-foreground">decision</h2>
            <p className="mt-1 text-sm">{decision ? `${game.players[decision.seat].name} · ${decision.selected.id}` : "No AI decision yet"}</p>
            {decision && <>
              <p className="mt-1 text-[12px] text-muted-foreground">Hand {decision.hand} · last completed decision · {decision.latencyMs.toFixed(0)} ms</p>
              <div className="mt-4 space-y-3">{decision.candidates.map((c) => <div key={c.id} title={c.description} className="grid grid-cols-[minmax(0,8rem)_minmax(0,1fr)_2.5rem] items-center gap-2 text-[12px]">
                <span className={`truncate font-mono ${c.id === decision.selected.id ? "font-medium" : "text-muted-foreground"}`}>{c.id}</span>
                <span className="h-1 rounded-full bg-muted"><span className="block h-1 rounded-full bg-foreground" style={{ width: `${decision.probabilities[c.id] * 100}%` }} /></span>
                <span className="text-right tabular-nums">{decision.probabilities[c.id].toFixed(2)}</span>
              </div>)}</div>
            </>}
            <p className="mt-4 text-[12px] leading-5 text-muted-foreground">These are model action probabilities, not winning odds. The highest-probability action is played.</p>
          </section>
          <section className="rounded-md border border-border bg-card p-4">
            <h2 className="text-[12px] text-muted-foreground">AI personalities</h2>
            <dl className="mt-3 space-y-3 text-[13px]">{game.players.filter((p) => p.personaId).map((p) => {
              const persona = POKER_PERSONAS.find((profile) => profile.id === p.personaId)!;
              return <div key={p.seat} title={persona.preferences}><dt>#{p.seat + 1} {p.name} · {position(game, p.seat)}</dt><dd className="text-[12px] text-muted-foreground">{persona.style}</dd></div>;
            })}</dl>
          </section>
          <section className="rounded-md border border-border bg-card p-4">
            <h2 className="text-[12px] text-muted-foreground">Activity · hand {game.hand}</h2>
            <ol className="mt-3 max-h-80 space-y-2 overflow-y-auto text-[12px]" aria-live="polite">
              {[...game.history].reverse().map((e, i) => <li key={game.history.length - i} className="text-muted-foreground"><span className="capitalize">{e.street}</span> · {e.seat === null ? "Board" : game.players[e.seat].name}: {e.text}</li>)}
            </ol>
          </section>
          <p className="text-[12px] leading-5 text-muted-foreground">Hands run locally in this browser. Refreshing clears the table. Only the acting player&apos;s cards and public information are sent to Kev.</p>
        </aside>
      </div>}
  </main>;
}
