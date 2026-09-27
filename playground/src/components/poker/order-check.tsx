import type { Decision } from "@/lib/poker/kev-api";

export function OrderCheck({ decision }: { decision: Decision }) {
  const choices = new Set(decision.orderChecks.map((check) => check.choice)).size;
  return <p className="mt-3 text-xs leading-5 text-muted-foreground" role="status">
    Averaged over {decision.orderChecks.length} option orders.
    {choices > 1 ? ` Order-sensitive: ${choices} different actions were selected before averaging. Treat this decision as uncertain.`
      : " All tested orders selected the same action. Agreement is not proof of a good poker decision."}
  </p>;
}
