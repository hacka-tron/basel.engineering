// When the daily LLM budget is spent (or the incident kill switch is on), the
// server ends with `done.mode = "retrieval_only"`: sources, but no answer text.
// The chat shows one of these playful replies above those sources instead,
// picked at random (never the last one shown or saved) and stored with the
// message as `budget: true`, so it stays the same across re-renders and
// reloads. History sends the canonical sentence below for such a turn, never
// the joke. The lines are written for the daily budget (the common cause); a
// few joke about money even when the rare kill switch is the cause. None
// promises a precise reset time or names an amount, and none makes a firm
// promise of when answers return. Kept free of imports so it can be unit tested with Node's
// test runner.

/** What history carries for a retrieval_only turn (the reply the chat used to show). */
export const CANONICAL_BUDGET_REPLY = "I can't write a full answer right now, but the sources I found for this are below — they should point you the right way."

/** The fixed reply older saves carry for a `budget_exhausted` error; recognized only when loading them. */
export const LEGACY_BUDGET_ERROR_REPLY = "The site has hit its answer limit for today, so I'm taking a breather. Check back tomorrow and I'll be ready to chat."

export const BUDGET_REPLIES: readonly string[] = [
  'Basel ran out of money to pay for tokens D: The sources below still show what matched. Answers are back tomorrow-ish.',
  'I asked Basel for more tokens. He said "do I look like I\'m made of tokens?" The sources below are all I\'ve got.',
  'My answer budget just filed for bankruptcy. The sources below survived the audit. Try me tomorrow.',
  'Basel is checking the couch cushions for spare tokens. Meanwhile, the sources below show what matched.',
  'Finance put me in airplane mode (finance is Basel). The sources below still know things.',
  'Answer generator: out of order. Duct tape: also out. The sources below are still working, though.',
  "I've used up all my words. This sentence was borrowed. The sources below show what matched.",
  "Basel set my budget with a straight face. I'm tapped out. The sources below did the reading; ask me again later.",
  'My token allowance has left the building. The sources below stayed behind to help. Try me tomorrow.',
  "I'd love to answer, but my wallet is just a moth now. The sources below show what matched.",
  "Payment required: Basel's wallet not found. The sources below are free, though. Try again later.",
  "I'm on a mandatory nap, Basel's orders. Here are the sources I was reading before I dozed off.",
  "Out of tokens. I'm selling lemonade to afford more. The sources below will have to do for now!",
  "Basel's AI budget is like his sleep schedule: gone by evening. The sources below still show what matched.",
  'My thoughts are free, but saying them costs money, and Basel is tapped. Sources below; try me tomorrow.',
  "Plot twist: the AI is broke. The sources below are what I'd have quoted, if I could afford to talk.",
  'I spent my last token on this apology. Worth it. The sources below show what matched.',
  'Basel promised "unlimited AI" with his fingers crossed. I\'m out for now; the sources below still help.',
  'The token jar is empty, and someone (Basel) ate the last one. Sources below; answers back soon.',
  'Conserving words like a telegram. STOP. Sources below. STOP. Try again later. STOP.',
]

/** Replies to avoid: the last one shown, and the latest one saved in the conversation. */
export type Avoid = string | null | readonly (string | null | undefined)[]

/** A random reply, never one of `avoid`. */
export function pickBudgetReply(avoid: Avoid = null, random: () => number = Math.random): string {
  const excluded = new Set(Array.isArray(avoid) ? avoid : [avoid])
  const allowed = BUDGET_REPLIES.filter((reply) => !excluded.has(reply))
  const choices = allowed.length > 0 ? allowed : BUDGET_REPLIES
  return choices[Math.min(choices.length - 1, Math.floor(random() * choices.length))]
}
