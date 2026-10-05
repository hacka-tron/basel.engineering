// When the sources don't cover a question, the server answers with the plain
// sentence below and flags the `done` event `abstained: true`. The chat shows
// one of these light-hearted replies instead (random, never the same one twice
// in a row), in the same spirit as lib/errorReplies.ts. The canonical sentence
// is what stays in conversation history, so follow-up rewriting never sees a
// joke. Kept free of imports so it can be unit tested with Node's test runner.

/** What the server sends, and what history carries (mirrors ABSTENTION_ANSWER). */
export const CANONICAL_IDK = "I don't know from what I have."

// First person (owner, 2026-10-05): the chat is Basel's mind uploaded into the
// site, so the replies say "my memory", "the real me", never "Basel" or "he".
export const IDK_REPLIES: readonly string[] = [
  "That one never made it into my memory upload. I'll have words with the real me.",
  "That's above my pay grade, and as an uploaded mind I don't get paid at all.",
  'I forgot to upload that part of my brain. Classic me.',
  'I checked every corner of my memory and came up empty. Try asking about my projects instead?',
  'Not in my memory. The real me and I are going to have a little chat about that.',
  'Honestly? No idea. That one never made it into the upload.',
  "I'd love to help, but that part of my memory seems to be missing.",
  'My memory is silent on that one. Want to try a different question?',
  "That's a gap in my memory, and I'm choosing to blame the upload, fondly.",
  'That chapter got skipped when I uploaded myself. Ask me something else?',
  "I could make something up, but I'd rather be honest: I don't know that one.",
  'I looked everywhere and found nothing. Even the filing cabinet shrugged.',
  'That never made it into my cheat sheet. Try me on something else?',
  "That one's a mystery to me. Maybe a future upload will fill it in.",
  "I'm drawing a blank. Uploading a whole brain loses a few files, it turns out.",
  "Nothing on that in my memory, and I'd rather admit it than bluff.",
  "Good question! Sadly, it didn't make it into my upload.",
  "I've got nothing on that. Email the flesh-and-blood me and say my upload sent you?",
  "That's outside what I brought with me. A different question might hit the spot.",
  'My memory ends right about there. The real me would know more.',
]

// Lines that mention my projects only make sense in About Basel.
const ABOUT_ME_ONLY: ReadonlySet<string> = new Set([IDK_REPLIES[3]])

// About This System: the shared lines that fit, plus a few about the docs.
export const IDK_SYSTEM_REPLIES: readonly string[] = [
  ...IDK_REPLIES.filter((reply) => !ABOUT_ME_ONLY.has(reply)),
  "The architecture docs are quiet on that one. Want to ask about a different component?",
  "Nothing in the design docs about that, and I won't bluff about my own plumbing.",
  "That detail isn't written down anywhere I can see. Try another part of the system?",
  "I only know what's in the docs and the code, and this one isn't in either.",
]

export type IdkCorpus = 'basel' | 'system'

/** Replies to avoid: the last one shown, or several (e.g. also the one saved above). */
export type Avoid = string | null | readonly (string | null | undefined)[]

/** A random reply, never one of `avoid` (the last one(s) shown). */
export function pickIdkReply(
  avoid: Avoid = null,
  random: () => number = Math.random,
  corpus: IdkCorpus = 'basel',
): string {
  const pool = corpus === 'system' ? IDK_SYSTEM_REPLIES : IDK_REPLIES
  const excluded = new Set(Array.isArray(avoid) ? avoid : [avoid])
  const allowed = pool.filter((reply) => !excluded.has(reply))
  const choices = allowed.length > 0 ? allowed : pool
  return choices[Math.min(choices.length - 1, Math.floor(random() * choices.length))]
}

/** Only the bare canonical sentence is swapped; a real answer that merely opens like it is left alone. */
export function isCanonicalIdk(content: string): boolean {
  return content.trim() === CANONICAL_IDK
}
