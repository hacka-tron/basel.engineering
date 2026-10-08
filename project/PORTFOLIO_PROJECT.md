# Adding a portfolio project

The playbook for the most common owner request: "add my new project to the portfolio". Read this instead of exploring the portfolio code. Field-by-field reference: `corpus/portfolio/_example.md` (its comments are the spec). Design background, only if something here is unclear: `docs/DESIGN-003-ingestion.md` §1.3.

## What a project is

One Markdown file, `corpus/portfolio/<slug>.md`, plus optional screenshots in `frontend/public/portfolio/<slug>/`. The same file feeds two things:

- **The site:** the Portfolio topic, project cards and the details sheet are built from the front matter and body at frontend build time.
- **The chatbot:** the release's ingest Job indexes the body as the `portfolio` corpus, so visitors can ask about the project.

The Portfolio topic, the phone "Portfolio" segment and "See portfolio →" stay hidden until at least one project has `draft: false`.

## First real project only: prerequisites

Check these once, before the first non-draft project merges. Each is a BACKLOG item under "Open items from the portfolio backend". Delete this section once they are done.

1. **Planned-marker exemption (blocking; needs the owner's go-ahead).** `_mark_planned` in `services/glassbox/api/ask.py` runs over every non-code source. A write-up that says "planned", "deferred", "SQS" or "ASG" gets labelled `[PLANNED, not built yet]`, and the chatbot then says the project isn't built. Exempt `corpus/portfolio/` (a prompt change, portfolio spec §6.3). Ask the owner, then ship it as its own reviewed PR before the project PR.
2. **Eval support.** `eval/run_eval.py` and `eval/schema.py` know only `about_me` and `about_system`. Add `portfolio` and 2–3 golden cases per project (owner rule: only once real projects exist). Can follow the project PR.
3. **Ops · Diagnose** prints corpus versions for two corpora only. Add `portfolio` with the next `ops` Terraform apply. Not blocking.

## Steps

1. **Collect from the owner** (ask once, all together): project name; one sentence on what it does and for whom; personal or freelance; year shipped; tech stack; live and code links (https, public only); screenshots (files, plus what each shows); and the three body sections from `_example.md`: the problem, what was built, what was interesting. Offer to draft the body from a repo, README or Drive doc they point to, then have them confirm it.
2. **Pick the slug:** lowercase letters, digits and hyphens (`jobpilot`). The file is `corpus/portfolio/<slug>.md`; images go in `frontend/public/portfolio/<slug>/`, and each `src` starts with `<slug>/`.
3. **Write the file:** copy `_example.md` and remove its comments. Required: `title`, `one_liner`, `kind`, `year`, `stack`. Set `draft: false`. Rules the validator enforces: only literal `true`/`false`; no repeated fields; `https` links only; every visual has `alt`; `aspect` is a quoted ratio (`"16/10"`, `"4/3"` or `"9/19.5"`); `order` sets grid position (ascending); `cover` (optional, `<slug>/<file>`) is the card picture, like a video thumbnail, and stays out of the details gallery. Without it the card shows the first visual.
4. **Write the body for retrieval:** one `##` section per topic. Each heading names the project (`## JobPilot: the problem it solves`, not `## The problem`), because each heading section becomes one chatbot chunk and the chunk text is all that is embedded. Keep each section under about 450 words. Say what is built in plain past tense, and avoid "planned" wording until prerequisite 1 is done.
5. **Privacy:** no client names, emails or phone numbers unless the owner says they are public. Ingest redacts personal data in portfolio files and CI fails on client contact details.
6. **Check locally:**
   - `python -m services.glassbox.portfolio` validates every project and fails with a message naming the field.
   - `cd frontend && npm run build` builds the site with the project in it.
   - Optional: `cd frontend && npm run phone` to see the card and details sheet at phone size (`project/MOBILE_DESIGN.md`).
7. **PR:** branch `content/portfolio-<slug>` in `.worktrees/`. Corpus changes run full CI. Get the standard review gate (`orchestration/reviewer-brief.md`), with the reviewer checking facts against what the owner provided, privacy, and links. Merge once approved and green (`project/CLAUDE.md` "Merge").
8. **After release:** the Release workflow builds the image and the ingest Job indexes the project. On the live site, check that the card shows, the details sheet opens, screenshots load, and that asking "Tell me about <project>" cites `corpus/portfolio/<slug>.md`.

## Changing or removing a project

- **Edit:** change the file and merge; the next release re-indexes it.
- **Hide:** set `draft: true`. The site drops it on the next release, and the stale sweep (apply mode since 2026-10-03, #158) removes it from the chatbot index.
- **Delete:** remove the file and its image folder; the sweep removes it from the index on the next release.

## Token tips for agents

Don't read the portfolio frontend or backend code for this task; this file and `_example.md` cover it. If the validator or build fails, read only the file and line it names.
