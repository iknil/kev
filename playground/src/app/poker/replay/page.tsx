import type { Metadata } from "next";
import { PokerReplay } from "@/components/poker/replay";

export const metadata: Metadata = {
  title: "kev · poker hand review",
  description: "Replay sourced poker hands and compare Kev's decisions with recorded play.",
};

export default function PokerReplayPage() {
  return <PokerReplay />;
}
