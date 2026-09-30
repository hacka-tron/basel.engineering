// Failed requests show up as a friendly assistant reply in the conversation
// instead of a bare "Request failed (…)" banner. Generic failures get one of
// these at random (never the same one twice in a row); failures that carry
// something the visitor needs to know (a wait time, the daily budget) get a
// specific reply that keeps that information. Kept free of imports so it can
// be unit tested with Node's built-in runner.

export const ERROR_REPLIES: readonly string[] = [
  "Oops — looks like something's wrong with the backend. Mind trying that again?",
  'Well, that didn\'t go to plan. Could you ask me again?',
  'Hmm, my thoughts got tangled somewhere between the server and here. One more try?',
  'Something hiccuped on my end. Give it another go?',
  'The answer got lost on its way to you. Want to try again?',
  "My backend just tripped over its own shoelaces. Ask again and I'll do better.",
  'That one fell through the cracks. Mind sending it again?',
  'Looks like the servers blinked. Try that question once more?',
  'I dropped the ball on that one. Could you ask again?',
  'Something on my side went sideways. A retry usually sorts it out.',
  'The pipes got a little clogged there. Try again?',
  'Well, that was unexpected — even for me. Give it another shot?',
  "I lost my train of thought (and the connection). Let's try that again.",
  "A gremlin got into the wires. Ask again and I'll chase it off.",
  'That request wandered off somewhere. Want to send it again?',
  "Hmm, I couldn't finish that thought. Try asking once more?",
  "Something glitched while I was answering. It's not you, it's me — try again?",
  'The backend took an unscheduled coffee break. Mind asking again?',
  'My circuits got a bit scrambled there. Another try should do it.',
  'That answer got stuck in traffic. Could you try again?',
]

/** A random generic reply, never equal to `previous` (the last one shown). */
export function pickErrorReply(previous: string | null = null, random: () => number = Math.random): string {
  const choices = ERROR_REPLIES.filter((reply) => reply !== previous)
  return choices[Math.min(choices.length - 1, Math.floor(random() * choices.length))]
}

export type FailedRequest = { code: string; retry_after_s?: number }

/** The chat reply for a failed request. */
export function errorReplyFor(
  failure: FailedRequest,
  previous: string | null = null,
  random: () => number = Math.random,
): string {
  if (failure.code === 'rate_limited') {
    const wait = failure.retry_after_s && failure.retry_after_s > 0
      ? `about ${failure.retry_after_s} ${failure.retry_after_s === 1 ? 'second' : 'seconds'}`
      : 'a moment'
    return `Whoa, that's a lot of questions at once! Give me ${wait} to catch my breath, then ask again.`
  }
  if (failure.code === 'budget_exhausted') {
    return "I've used up today's answer budget, so I'm taking a breather. Come back tomorrow and I'll be ready to chat."
  }
  return pickErrorReply(previous, random)
}
