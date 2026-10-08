---
title: GoalBuddy
one_liner: A mobile app that pairs you with a real person working toward the same kind of goal, for streaks, nudges and accountability.
kind: personal
year: 2026
order: 1
stack: [React Native, Expo, TypeScript, Node.js, Express, PostgreSQL, TanStack Query, Zustand]
links:
  code: https://github.com/hacka-tron/goalbuddy
cover: goalbuddy/cover.png
visuals:
  - src: goalbuddy/milestones.png
    alt: Goals screen listing today's pending milestones, each with its goal, most with a Daily badge and a due date
    caption: Today's milestones across every goal, logged with one tap.
    aspect: "9/19.5"
  - src: goalbuddy/my-goals.png
    alt: My Goals list showing goal categories, titles and each goal's buddy or a No buddy yet chip
    caption: Each goal shows its category and its accountability partner.
    aspect: "9/19.5"
  - src: goalbuddy/buddy-chat.png
    alt: One-on-one chat between two buddies about a shared Visit Cafes goal
    caption: Check-in chat with a buddy, tied to the goal you share.
    aspect: "9/19.5"
  - src: goalbuddy/sign-in.png
    alt: GoalBuddy sign-in screen with Google sign-in and email and password fields
    caption: Sign in with Google or email.
    aspect: "9/19.5"
draft: false
---

## GoalBuddy: the problem it solves

Most goal trackers are solo: you set a goal, log progress for a week or two, and quietly
stop. GoalBuddy adds a real person to the loop. It pairs you with someone working toward
the same kind of goal, such as working out, studying or learning an instrument, so that
skipping a day means letting someone down. The app is meant to take seconds per visit:
mark progress, send a nudge, check a streak, and get back to your day.

GoalBuddy is a work in progress. It runs locally on Android and is not on the app stores. The code is public on GitHub.

## GoalBuddy: what I built

GoalBuddy is an Expo (React Native) app with an Express and TypeScript API on PostgreSQL.

- **Goals and milestones:** a goal belongs to one of eight categories and breaks into
  milestones that repeat once, daily, weekly, monthly, a set number of days per week, or
  on chosen weekdays. A milestone can be open-ended or run between a start and end date,
  and the app tracks streaks, sessions completed and percent complete.
- **Buddy mode:** two people pursue their own goals in the same category and coach each
  other, seeing each other's streaks and completion rates.
- **Co-op mode:** one person publishes a goal with milestones, and a partner joins and
  gets their own copy of the goal and its milestones, so both work toward the same
  objective together.
- **Finding a partner:** a swipe deck suggests people with goals in the same category, and
  a mutual swipe creates the connection. A search lists public goals.
- **Check-in chat:** each partnership has a chat that shows a deadline warning when a
  partner's milestone is close to its due time.

The mobile app keeps server data in TanStack Query with query-key factories and keeps session
and UI state (sign-in, theme, notifications) and an outbox of unsent check-ins in Zustand. Every request goes
through one API client, and screens handle loading, empty and error states.

## GoalBuddy: what was interesting

The hard part of a social goal app is keeping two people's progress comparable. Streaks,
completion rates and expected sessions have to mean the same thing for a daily habit, a
three-days-a-week routine and a ten-week program with fixed dates, so milestone stats are
computed on the server from each milestone's frequency and schedule rather than counted
in the app.

The API uses raw SQL through a small query helper instead of an ORM, with Zod validation
on routes that take input and integration tests that run against a real PostgreSQL test
database.
