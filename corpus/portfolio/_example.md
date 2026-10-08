---
# Example portfolio project. It is a draft, so the site and the chatbot ignore it.
# To add a project: copy this file to corpus/portfolio/<slug>.md (lowercase letters,
# digits and hyphens, e.g. jobpilot.md), fill it in, and set draft: false.
# Check your files with: python -m services.glassbox.portfolio
# Required fields: title, one_liner, kind, year, stack. Everything else is optional.
title: Example Project          # project name, on the card and the details sheet
one_liner: One sentence on what it does and for whom.   # card and sheet subtitle
kind: personal                  # personal | freelance
year: 2026                      # the year it shipped (a plain number)
order: 99                       # grid position, ascending; projects without one go last
stack: [Python, FastAPI, React] # tags; the card shows the first three, then "+N"
links:                          # optional; https only; leave a key out to hide its button
  live: https://example.com     # the "Live site" button
  # code: https://github.com/hacka-tron/example   # the "Code" button
# cover: jobpilot/cover.png     # optional card picture (a thumbnail; never in the gallery); without it the card shows the first visual
visuals: []                     # optional screenshots, in display order; replace [] with:
#  - src: jobpilot/board.png    # a file in frontend/public/portfolio/, starting with the slug
#    alt: Pipeline board with applications grouped by stage   # required, for screen readers
#    caption: Pipeline board, from saved to offer.   # optional, shown under the image
#    aspect: 16/10              # 16/10 (desktop) | 4/3 | 9/19.5 (phone screenshot)
draft: true                     # true: not on the site and not indexed by the chatbot
---

## The problem

Who it was for and what was hard before it existed.

## What I built

The main pieces and how they fit together. Keep it to a few short paragraphs: the
site shows this text in the project's details sheet, and the chatbot answers from it.

## What was interesting

A decision, a trade-off or a number worth telling a recruiter or a client about.
Never paste a client's contact details here; the CI check fails if it finds any.
