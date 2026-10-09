// When the daily LLM budget is spent (or the incident kill switch is on), the
// server ends with `done.mode = "retrieval_only"`: retrieved chunks, but no
// answer text. The chat shows one of these playful replies instead (no sources
// list is shown under answers; see retrievalOnlyReply below),
// picked at random (never the last one shown or saved) and stored with the
// message as `budget: true`, so it stays the same across re-renders and
// reloads. History leaves such a turn out entirely (lib/conversation.ts), so the
// server never sees the joke or any invented sentence. The lines are written for the daily budget (the common cause); a
// few joke about money even when the rare kill switch is the cause. None
// promises a precise reset time or names an amount, and none makes a firm
// promise of when answers return. Kept free of imports so it can be unit tested with Node's
// test runner.

/** The fixed reply older saves carry for a `budget_exhausted` error; recognized only when loading them. */
export const LEGACY_BUDGET_ERROR_REPLY = "The site has hit its answer limit for today, so I'm taking a breather. Check back tomorrow and I'll be ready to chat."

export const BUDGET_REPLIES: readonly string[] = [
  'Basel ran out of money to pay for tokens D: Answers are back tomorrow-ish.',
  'I asked Basel for more tokens. He said "do I look like I\'m made of tokens?" Try me later.',
  'My answer budget just filed for bankruptcy. Try me tomorrow.',
  'Basel is checking the couch cushions for spare tokens. Try me again in a bit.',
  'Finance put me in airplane mode (finance is Basel). Try again later.',
  'Answer generator: out of order. Duct tape: also out. Try again later.',
  "I've used up all my words. This sentence was borrowed. Try me later.",
  "Basel set my budget with a straight face. I'm tapped out; ask me again later.",
  'My token allowance has left the building. Try me tomorrow.',
  "I'd love to answer, but my wallet is just a moth now. Try again later.",
  "Payment required: Basel's wallet not found. Try again later.",
  "I'm on a mandatory nap, Basel's orders. Ask me again later.",
  "Out of tokens. I'm selling lemonade to afford more. Try again soon!",
  "Basel's AI budget is like his sleep schedule: gone by evening. Try me later.",
  'My thoughts are free, but saying them costs money, and Basel is tapped. Try me tomorrow.',
  "Plot twist: the AI is broke. I'd tell you more, if I could afford to talk.",
  'I spent my last token on this apology. Worth it. Try me again later.',
  'Basel promised "unlimited AI" with his fingers crossed. I\'m out for now; try later.',
  'The token jar is empty, and someone (Basel) ate the last one. Answers back soon.',
  'Conserving words like a telegram. STOP. Try again later. STOP.',
]

/**
 * The reply when the server returns `retrieval_only` (no answer text). Chat
 * shows no sources list in any topic (owner, 2026-10-03). About This System
 * points to the "Retrieved chunks" list in the diagram pane, which still shows
 * what was found; About Basel, whose private sources are never shown, gets a
 * playful reply with nothing to point to.
 */
export const SYSTEM_RETRIEVAL_ONLY_REPLY = "I can't write an answer right now, but what I found is listed under Retrieved chunks in the diagram."

export function retrievalOnlyReply(
  topic: 'basel' | 'system',
  avoid: Avoid = null,
  random: () => number = Math.random,
): string {
  return topic === 'system' ? SYSTEM_RETRIEVAL_ONLY_REPLY : pickBudgetReply(avoid, random)
}

/** Replies to avoid: the last one shown, and the latest one saved in the conversation. */
export type Avoid = string | null | readonly (string | null | undefined)[]

/** A random reply, never one of `avoid`. */
export function pickBudgetReply(avoid: Avoid = null, random: () => number = Math.random): string {
  const excluded = new Set(Array.isArray(avoid) ? avoid : [avoid])
  const allowed = BUDGET_REPLIES.filter((reply) => !excluded.has(reply))
  const choices = allowed.length > 0 ? allowed : BUDGET_REPLIES
  return choices[Math.min(choices.length - 1, Math.floor(random() * choices.length))]
}
