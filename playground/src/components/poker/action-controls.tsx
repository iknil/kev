"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { actionCandidates } from "@/lib/poker/candidates";
import { legalActions } from "@/lib/poker/rules";
import type { Action, GameState } from "@/lib/poker/types";

export function ActionControls({ game, onAction }: { game: GameState; onAction: (action: Action) => void }) {
  const legal = legalActions(game);
  const range = legal.aggression;
  const [amount, setAmount] = useState(range?.minTo ?? 0);
  const valid = range && Number.isSafeInteger(amount) && amount >= range.minTo && amount <= range.maxTo;
  const actor = game.players[game.actor!];
  return <section aria-labelledby="poker-actions" className="rounded-md border border-border bg-card px-4 py-4">
    <h2 id="poker-actions" className="text-sm font-medium">Your action</h2>
    <p className="mt-2 text-[12px] text-muted-foreground">Stack {actor.stack} · already invested this street {actor.streetBet}</p>
    {range && <>
      <div className="mt-4 flex items-center justify-between gap-4">
        <Label htmlFor="raise-size" className="text-[13px] text-muted-foreground">{range.type === "bet" ? "Bet to" : "Raise to"}</Label>
        <input id="raise-size" type="number" min={range.minTo} max={range.maxTo} step={1} value={Number.isNaN(amount) ? "" : amount}
          onChange={(e) => setAmount(e.target.value === "" ? NaN : Number(e.target.value))}
          className="h-9 w-28 rounded-md border border-border bg-background px-2 font-mono text-sm" />
      </div>
      <input aria-label="Bet size slider" type="range" min={range.minTo} max={range.maxTo} step={1} value={valid ? amount : range.minTo}
        onChange={(e) => setAmount(Number(e.target.value))} className="mt-3 w-full accent-foreground" />
      <p className="mt-1 text-[12px] text-muted-foreground">{range.shortAllIn ? "Only a short all-in is available." : `Allowed total: ${range.minTo}–${range.maxTo}.`}{valid ? ` Pay ${amount - actor.streetBet} additional chips.` : " Enter a legal integer amount."}</p>
      <div className="mt-2 flex flex-wrap gap-1">
        {actionCandidates(game).filter((c) => "to" in c.action).map((c) => "to" in c.action && <Button key={c.id} variant="ghost" size="sm" title={c.description} onClick={() => setAmount("to" in c.action ? c.action.to : 0)}>
          {c.action.to === range.maxTo ? "All-in" : c.action.to}
        </Button>)}
      </div>
    </>}
    <div className="mt-4 flex flex-wrap gap-2">
      {legal.fold && <Button variant="outline" onClick={() => onAction({ type: "fold" })}>Fold</Button>}
      {legal.check && <Button variant="outline" onClick={() => onAction({ type: "check" })}>Check</Button>}
      {legal.call > 0 && <Button variant="outline" onClick={() => onAction({ type: "call" })}>Call {legal.call}{legal.call === actor.stack ? " · all-in" : ""}</Button>}
      {range && <Button disabled={!valid} onClick={() => onAction({ type: range.type, to: amount })}>{range.type === "bet" ? "Bet" : "Raise"} to {valid ? amount : "…"}</Button>}
    </div>
  </section>;
}
