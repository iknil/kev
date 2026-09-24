import { cardLabel } from "@/lib/poker/cards";
import { position, potTotal } from "@/lib/poker/rules";
import type { Card, GameState } from "@/lib/poker/types";

export function PlayingCard({ card }: { card?: Card }) {
  const red = card?.endsWith("h") || card?.endsWith("d");
  return <span aria-label={card ? cardLabel(card) : "Undealt card"}
    className={`flex h-16 w-10 shrink-0 items-center justify-center rounded-md border font-mono text-lg sm:h-20 sm:w-12 ${card ? `border-border bg-background ${red ? "text-destructive" : "text-foreground"}` : "border-dashed border-border bg-background/40 text-muted-foreground"}`}>
    {card ? cardLabel(card) : "—"}
  </span>;
}

export function PokerBoard({ game, observed, onObserve }: { game: GameState; observed: number; onObserve: (seat: number) => void }) {
  const hero = game.players[game.mode === "human" ? 0 : observed];
  return (
    <section aria-label="Poker table" className="overflow-hidden rounded-xl border border-border bg-muted/20">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3 text-[12px] text-muted-foreground">
        <span>Seat 1 at the bottom · clockwise ↻</span><span>D Dealer · SB / BB Blinds</span>
      </div>
      <div className="overflow-x-auto focus-visible:outline-2 focus-visible:outline-ring" tabIndex={0} aria-label="Table seating; scroll horizontally on small screens">
        <div className="relative h-[640px] min-w-[480px]">
          <div aria-hidden className="absolute inset-x-[12%] inset-y-[10%] rounded-[50%] border border-border bg-muted/30" />
          <ol aria-label="Seats in clockwise order">
            {game.players.map((p) => {
              const angle = Math.PI / 2 + p.seat / game.players.length * 2 * Math.PI;
              const acting = p.seat === game.actor;
              return <li key={p.seat} aria-label={`Seat ${p.seat + 1}: ${p.name}, ${position(game, p.seat)}`}
                style={{ left: `${50 + 39 * Math.cos(angle)}%`, top: `${50 + 40 * Math.sin(angle)}%` }}
                className="absolute z-10 w-[88px] -translate-x-1/2 -translate-y-1/2">
                <button type="button" onClick={() => onObserve(p.seat)} disabled={game.mode === "human"}
                  aria-pressed={hero.seat === p.seat}
                  className={`w-full rounded-md border bg-card px-2 py-2 text-center focus-visible:outline-2 focus-visible:outline-ring ${acting ? "border-foreground ring-1 ring-foreground" : "border-border"} ${p.status === "folded" || p.status === "out" ? "opacity-45" : ""}`}>
                  <span className="flex items-center justify-between text-[10px] text-muted-foreground">
                    <span className="font-mono">#{p.seat + 1}</span>
                    {p.seat === game.dealer ? <span title="Dealer" className="flex size-4 items-center justify-center rounded-full bg-foreground font-mono text-background">D</span> : <span>{p.personaId ? "AI" : "You"}</span>}
                  </span>
                  <span className="mt-1 block truncate text-[12px] font-medium">{p.name}</span>
                  <span className="block font-mono text-[10px] text-muted-foreground">{position(game, p.seat)}</span>
                  <span className="mt-1 block font-mono text-[12px] tabular-nums">{p.stack.toLocaleString()}</span>
                  <span className="block text-[10px] text-muted-foreground">{p.status === "active" ? acting ? "To act" : `Bet ${p.streetBet}` : p.status}</span>
                </button>
              </li>;
            })}
          </ol>
          <div className="absolute inset-x-0 top-1/2 -translate-y-1/2 text-center" aria-live="polite">
            <p className="text-[12px] text-muted-foreground">{game.complete ? "Pot awarded" : "Pot"}</p>
            <p className="mt-1 font-mono text-2xl font-medium tabular-nums">{game.complete ? game.pots.reduce((sum, p) => sum + p.amount, 0) : potTotal(game)} <span className="font-sans text-[12px] font-normal text-muted-foreground">chips</span></p>
            <div className="mt-4 flex justify-center gap-1.5" aria-label="Community cards">
              {Array.from({ length: 5 }, (_, i) => <PlayingCard key={i} card={game.board[i]} />)}
            </div>
            <p className="mt-3 text-[12px] capitalize text-muted-foreground">{game.complete ? game.showdown ? "Showdown" : "Hand complete" : game.street}</p>
            <p className="mt-2 text-[12px] text-muted-foreground">↻ Clockwise seating</p>
          </div>
        </div>
      </div>
      <div className="flex items-center justify-between gap-4 border-t border-border bg-card px-4 py-3">
        <div><p className="text-sm font-medium">{game.mode === "human" ? "Your hand" : "Observed AI hand"}</p>
          <p className="mt-1 text-[12px] text-muted-foreground">Seat {hero.seat + 1} · {hero.name} · {position(game, hero.seat)}</p>
          {game.mode === "self" && <p className="mt-1 text-[11px] text-muted-foreground">Select a seat to observe. AI sees only its own cards.</p>}
        </div>
        <div className="flex gap-2">{[0, 1].map((i) => <PlayingCard key={i} card={hero.hole[i]} />)}</div>
      </div>
    </section>
  );
}
