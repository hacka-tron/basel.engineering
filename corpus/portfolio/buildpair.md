---
title: Buildpair
one_liner: A social to-do and goals app where a few trusted supporters keep your goals moving.
kind: personal
year: 2026
order: 1
stack: [React Native, Cloudflare Workers, Durable Objects, TypeScript, Expo, Hono, D1, Drizzle, Zod]
links:
  code: https://github.com/hacka-tron/buildpair
cover: buildpair/cover.png
visuals:
  - src: buildpair/desktop-space.png
    alt: A space called Ledgerly on desktop, with its live chat in the middle, its open to-dos on the right and the people in it below
    caption: A space is a list plus one chat. Finishing a to-do posts a line that supporters react to.
    aspect: "16/10"
  - src: buildpair/desktop-today.png
    alt: Today view on desktop listing to-dos and habits grouped by space, with each space's supporters shown as avatars
    caption: Today pulls every space's to-dos and habits into one list.
    aspect: "16/10"
  - src: buildpair/phone-space.png
    alt: The Ledgerly space at phone width, with its to-dos above its chat
    caption: The same space at phone width.
    aspect: "9/19.5"
  - src: buildpair/phone-today.png
    alt: Today view at phone width with a tab bar and a floating add button
    caption: Today on a phone, with a tab bar and a floating add button.
    aspect: "9/19.5"
  - src: buildpair/phone-add.png
    alt: Add to-do sheet at phone width with a title field, Today, Tomorrow or No date, and a choice of space
    caption: Adding a to-do takes a title, a day and a space.
    aspect: "9/19.5"
  - src: buildpair/desktop-invite.png
    alt: Invite dialog over a space on desktop, with a join link and a Copy link button
    caption: Anyone with a space's link joins it as a supporter.
    aspect: "16/10"
  - src: buildpair/landing.png
    alt: Buildpair landing page with the headline Set goals. Bring people along. and a sign-up form that emails a code
    caption: The landing page, with passwordless sign-up.
    aspect: "16/10"
draft: false
---

## Buildpair: the problem it solves

Builders such as founders, indie hackers and people shipping side projects usually work
alone. Public feeds and maker communities turn into broadcasting with little back and
forth, and to-do apps have no sense of anyone watching. Buildpair is a to-do and goals app
for builders that you share with a few people you trust. They see your goals move, notice
when one goes quiet, and tell you how it looks from the outside. The idea is that
accountability comes from a small audience of specific people who are close enough to care
and far enough from the work to be honest.

Buildpair is a work in progress. It runs on staging for invited people, production has not
been released yet, and the code is public on GitHub. The screenshots are the real web app
with made-up people.

## Buildpair: what I built

Buildpair is one Expo (React Native) codebase for iOS, Android and the web, with a backend
that runs entirely on Cloudflare.

- **Spaces:** everything lives in a space, which is a list plus one chat, such as a product,
  family life or training for a half marathon. Sharing is set per space, and any single item
  can stay private inside a shared space.
- **Updates come from the work itself:** ticking an item posts a line to the space's chat,
  and supporters react and reply in real time.
- **Supporters:** people you invite see progress, react and reply. When a goal goes quiet,
  only they get a nudge, so a check-in reads as a friend reaching out.
- **Invites by link:** anyone with a space's link can join it, the way group chats work.
- **Offline first:** changes apply instantly on the device, queue while offline and sync on
  reconnect with no duplicates.
- **Passwordless sign-in:** people sign in with a 6-digit code sent by email.

The app has a Today view across all spaces, a sidebar layout on desktop, and a tab bar with
a floating add button on phones.

The API is Cloudflare Workers with Hono. Each space has its own Durable Object that owns its
event log, chat and live WebSocket connections, and each person has one for their inbox.
Space data lives in SQLite inside each Durable Object, global data in D1 with a Drizzle
schema, and sign-in uses Better Auth with email codes. Zod schemas define every command,
event and socket message under a versioned protocol.

## Buildpair: what was interesting

**Each space is its own small database.** A Durable Object per space is the only writer for
that space. It numbers every event, stores the log in its own SQLite database and runs the
sockets for everyone in it. That gives strong consistency inside a space without a central
database becoming the bottleneck.

**One set of rules on the phone and on the server.** Every change to a space is a command
handled by a pure `decide(state, command, actor)` function in a shared TypeScript package.
The app runs it for instant optimistic updates and the server runs it for the authoritative
decision, so the two never disagree about the rules. The sync engine keeps a persisted queue
of pending commands with IDs, so retries are safe, and catches up on any gap in the event
sequence when a socket reconnects.

**Privacy enforced on the server.** Items marked "only visible to me" never reach other
members. The server filters every event by who may see it, and each new event type gets a
privacy test.

**Every pull request is treated like production.** Each pull request gets its own API
Worker, Durable Objects, D1 database and preview web app, removed when the pull request
closes. Merges deploy to staging automatically, and production deploys only from an approved
release. Browser tests run on every pull request, including one where two people sign in,
join a space, chat live, react and see a completion line appear.
