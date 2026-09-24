import type { Metadata } from "next";
import { PokerTable } from "@/components/poker-table";

export const metadata: Metadata = {
  title: "kev · poker",
  description: "Play Texas Hold'em against Kev AI personalities, or watch AI play AI.",
};

export default function PokerPage() {
  return <PokerTable />;
}
