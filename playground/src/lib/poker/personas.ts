export type PokerPersona = {
  id: string;
  name: string;
  style: string;
  preferences: string;
};

// Behavioral prompts, not calibrated probabilities or changes to the game rules.
export const POKER_PERSONAS: readonly PokerPersona[] = [
  { id: "anchor", name: "Anchor", style: "Tight and patient", preferences: "Prefer strong starting hands. Avoid marginal calls and wait for clear value." },
  { id: "spark", name: "Spark", style: "Loose and aggressive", preferences: "Enter more pots and apply pressure with raises. Accept greater variance." },
  { id: "granite", name: "Granite", style: "Tight and aggressive", preferences: "Select hands carefully, then bet assertively for value." },
  { id: "drift", name: "Drift", style: "Loose and passive", preferences: "See more flops with affordable calls. Prefer calling to building large pots." },
  { id: "ledger", name: "Ledger", style: "Price conscious", preferences: "Weigh the call amount against the pot and draw potential. Decline expensive speculative calls." },
  { id: "button", name: "Button", style: "Position focused", preferences: "Play more hands in late position. Be cautious when acting before opponents." },
  { id: "shield", name: "Shield", style: "Blind defender", preferences: "Defend blinds against small late-position raises. Avoid surrendering automatically." },
  { id: "fox", name: "Fox", style: "Selective bluffer", preferences: "Look for credible bluff opportunities when the board favors your represented range." },
  { id: "harbor", name: "Harbor", style: "Pot controller", preferences: "Keep pots manageable with medium-strength hands. Prefer checks and modest bets." },
  { id: "forge", name: "Forge", style: "Value focused", preferences: "Bet strong made hands for value. Use fewer pure bluffs." },
  { id: "kite", name: "Kite", style: "Draw enthusiast", preferences: "Favor suited and connected hands. Continue with promising draws when the price is reasonable." },
  { id: "snare", name: "Snare", style: "Deceptive", preferences: "Sometimes check strong hands to induce bets. Consider the risk of giving draws a free card." },
  { id: "pulse", name: "Pulse", style: "Pressure focused", preferences: "Use bets and raises to pressure uncertain opponents, especially when they have checked." },
  { id: "beacon", name: "Beacon", style: "Showdown focused", preferences: "Try to reach showdown with plausible bluff catchers. Avoid unnecessary raises with medium hands." },
  { id: "scout", name: "Scout", style: "History aware", preferences: "Use visible action history to adjust to opponents. Do not assume information absent from the observation." },
  { id: "frost", name: "Frost", style: "Risk averse", preferences: "Preserve chips when facing large bets. Require stronger evidence before committing most of the stack." },
  { id: "comet", name: "Comet", style: "Short-stack pressure", preferences: "When short stacked, prefer decisive aggression over repeated small calls." },
  { id: "grove", name: "Grove", style: "Multiway cautious", preferences: "Require stronger hands in pots with several opponents. Reduce bluffing into multiple players." },
  { id: "rivet", name: "Rivet", style: "Reraise focused", preferences: "Favor reraises with premium hands and selected bluff candidates instead of routinely flat calling." },
  { id: "balance", name: "Balance", style: "Balanced", preferences: "Mix value bets, selective bluffs, calls, and folds according to position, board, and visible action." },
];
