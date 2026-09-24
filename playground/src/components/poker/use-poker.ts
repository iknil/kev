"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { act, createGame, startHand } from "@/lib/poker/engine";
import { PokerController } from "@/lib/poker/controller";
import type { Decision } from "@/lib/poker/kev-api";
import type { Action, GameState, Mode } from "@/lib/poker/types";

export function usePoker() {
  const [game, setGame] = useState<GameState | null>(null);
  const current = useRef<GameState | null>(null);
  const [controller] = useState(() => new PokerController());
  const generation = useRef(0);
  const inFlight = useRef(false);
  const [running, setRunning] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [decision, setDecision] = useState<Decision | null>(null);

  const commit = useCallback((next: GameState) => { current.current = next; setGame(next); }, []);
  const pause = useCallback(() => {
    generation.current++; controller.cancel(); inFlight.current = false; setRunning(false); setBusy(false);
  }, [controller]);
  const reset = useCallback(() => {
    pause(); current.current = null; setGame(null); setDecision(null); setError(null);
  }, [pause]);
  const newTable = useCallback((seats: number, mode: Mode) => {
    reset();
    commit(createGame({ id: crypto.randomUUID(), seats, mode, personaOffset: Math.floor(Math.random() * 20) }));
  }, [commit, reset]);
  const nextHand = useCallback(() => {
    if (!current.current?.complete) return;
    pause(); setError(null); setDecision(null);
    try { commit(startHand(current.current)); } catch (e) { setError((e as Error).message); }
  }, [commit, pause]);
  const humanAction = useCallback((action: Action) => {
    const s = current.current;
    if (!s || s.actor === null || s.players[s.actor].personaId) return;
    try { commit(act(s, s.actor, action)); setError(null); }
    catch (e) { setError((e as Error).message); }
  }, [commit]);
  const step = useCallback(async () => {
    const s = current.current;
    if (!s || s.actor === null || !s.players[s.actor].personaId || inFlight.current) return;
    const ticket = generation.current;
    inFlight.current = true;
    setBusy(true); setError(null);
    try {
      await controller.step(() => current.current, (next, result) => { commit(next); setDecision(result); });
    } catch (e) {
      if (ticket === generation.current) { setError((e as Error).message); setRunning(false); }
    } finally {
      if (ticket === generation.current) { inFlight.current = false; setBusy(false); }
    }
  }, [commit, controller]);

  useEffect(() => {
    if (!running || busy || !game || game.complete || game.actor === null || !game.players[game.actor].personaId) return;
    const timer = setTimeout(() => { void step(); }, 350);
    return () => clearTimeout(timer);
  }, [running, busy, game, step]);
  useEffect(() => () => controller.cancel(), [controller]);

  return { game, running, busy, error, decision, newTable, nextHand, reset, pause, step, humanAction,
    play: () => { setError(null); setRunning(true); } };
}
