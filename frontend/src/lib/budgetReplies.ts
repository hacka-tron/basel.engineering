// When the daily LLM budget is spent (or the incident kill switch is on), the
// server ends with `done.mode = "retrieval_only"`: sources, but no answer text.
// The chat shows one of these playful replies above those sources instead,
// picked at random (never the last one shown or saved) and stored with the
// message as `budget: true`, so it stays the same across re-renders and
// reloads. History sends the canonical sentence below for such a turn, never
// the joke. The lines are written for the daily budget (the common cause); a
// few joke about money even when the rare kill switch is the cause. None
// promises a precise reset time or names an amount. Kept free of imports so it can be unit tested with Node's
// test runner.

/** What history carries for a retrieval_only turn (the reply the chat used to show). */
export const CANONICAL_BUDGET_REPLY = "I can't write a full answer right now, but the sources I found for this are below — they should point you the right way."

/** The fixed reply older saves carry for a `budget_exhausted` error; recognized only when loading them. */
export const LEGACY_BUDGET_ERROR_REPLY = "The site has hit its answer limit for today, so I'm taking a breather. Check back tomorrow and I'll be ready to chat."

export const BUDGET_REPLIES: readonly string[] = [
  'Basel ran out of money to pay for tokens D: The sources below still show what matched. Answers are back tomorrow or a bit later.',
  "My answer allowance is spent for now. Basel's wallet needs a nap. The sources below still point the way; try me tomorrow.",
  "I've talked myself hoarse. The sources below are what matched; come back tomorrow and I'll put them into words.",
  "Basel put me on a strict word diet, and I'm out of words for now. The sources below still show what matched. Back tomorrow-ish!",
  'The answer machine is resting. The sources below show what I found; full answers return tomorrow or soon after.',
  "Out of thinking tokens! Blame Basel's budget spreadsheet. The matching sources are below, and I'll be chatty again soon.",
  "I'd answer, but Basel cut off my allowance for now. The sources below show what matched. Check back tomorrow.",
  "My brain's on a break, but my filing cabinet isn't: the sources below are what matched. Answers come back later.",
  "No new answers for now. The sources below still match your question, and I'll be back to explain them tomorrow or a bit later.",
  "Basel's piggy bank is empty, so no answer from me right now. The sources below show what matched. Try again tomorrow?",
  "I'm off the clock for now. The sources below are what I'd have read from; come back tomorrow for the full answer.",
  'The AI meter ran out, and Basel will top it up eventually. Meanwhile, the sources below show what matched.',
  "No more answers for now; Basel's keeping me on a tight leash. The matching sources are below. Ask again tomorrow!",
  "I've hit my limit for now. I did find sources, though: they're just below. Full answers are back tomorrow or a bit later.",
  "Basel only bought so many tokens, and they're gone for now. The sources below still show what matched. See you tomorrow!",
  "Writing answers is paused for now, but searching isn't: the sources below are what matched. Try again tomorrow.",
  "Running on fumes, so I'll let the sources below do the talking. Proper answers return tomorrow or later.",
  "The answer budget is tapped out, and I'm choosing to blame Basel, fondly. The sources below show what matched. Back soon.",
  "I'm saving my words for tomorrow. The sources below already show what matched your question.",
  'Basel turned my answer tap down to a drip. The sources below still show what matched; full answers come back later.',
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
