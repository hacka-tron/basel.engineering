---
title: Buildpair
one_liner: A to-do app you share with the few people who keep you honest.
kind: personal
year: 2026
order: 1
stack: [React Native, Cloudflare Workers, Durable Objects, TypeScript, Expo, Hono, D1, Drizzle, Zod]
links:
  code: https://github.com/hacka-tron/buildpair
cover: buildpair/logo.png
visuals:
  - src: buildpair/overview.png
    alt: Buildpair overview with the headline Set goals. Bring people along. beside the Today view on a phone and on desktop
    aspect: "16/10"
  - src: buildpair/phone-signin.png
    alt: Buildpair sign-in screen on a phone with the headline Set goals. Bring people along. and an email field
    caption: Sign in with a code from your email. No password to remember.
    aspect: "9/19.5"
  - src: buildpair/phone-today.png
    alt: Today view on a phone with to-dos grouped by space and a floating add button
    caption: Today shows what's due across every space.
    aspect: "9/19.5"
  - src: buildpair/phone-add.png
    alt: Add to-do sheet on a phone with a title field, a choice of day and a choice of space
    caption: Adding a to-do takes a few taps.
    aspect: "9/19.5"
  - src: buildpair/phone-spaces.png
    alt: Spaces list on a phone showing Ledgerly, Life and Running
    caption: A space for each part of your life, from a side project to a half marathon.
    aspect: "9/19.5"
  - src: buildpair/phone-space.png
    alt: The Ledgerly space on a phone with its to-dos above its chat
    caption: Every space is a list with a chat beside it.
    aspect: "9/19.5"
  - src: buildpair/desktop-today.png
    alt: Today view on desktop with to-dos grouped by space and a sidebar of spaces
    caption: The same app on desktop.
    aspect: "16/10"
  - src: buildpair/desktop-space.png
    alt: The Ledgerly space on desktop with a completed to-do in the chat, reactions and replies from two friends
    caption: Check something off and your friends see it right away.
    aspect: "16/10"
  - src: buildpair/desktop-invite.png
    alt: Invite dialog on desktop with a join link and a Copy link button
    caption: Bring someone in with a link.
    aspect: "16/10"
draft: false
---

## Why I built Buildpair

Building something on your own gets lonely. You set a goal, nobody notices when it slips,
and it fades away. I wanted a to-do app with a small audience. Not a public feed, just a few
people who care enough to ask how it's going.

Buildpair is a to-do and goals app for builders that you share with a few people you trust.
It's still a work in progress. It runs on a test site for invited people, and the code is
on GitHub.

## What Buildpair does

Everything lives in a space, like a side project, a training plan or family life. Each
space is a list with a chat beside it. When you check something off, the chat says so, and
your friends can cheer you on or ask what's next. If a goal goes quiet, they get a nudge to
check in, so it feels like a friend reaching out.

You invite people with a link, sign in with a code from your email, and keep working when
you're offline.

## How Buildpair is built

One Expo (React Native) codebase runs on iOS, Android and the web, and the backend runs on
Cloudflare Workers.

The part I'm proudest of is that each space has its own Durable Object that owns its event
log, chat and live connections. Every space is its own tiny database, so it stays
consistent without one central server in the way. The phone and the server also run the
same rules code. The app updates instantly and the server still has the final say.

Private items are filtered on the server so nobody else ever receives them, and every pull
request gets its own full preview environment.
