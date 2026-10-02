# corpus/

- `portfolio/`: Basel's other projects, one Markdown file per project, public. The
  ingest Job indexes them as the `portfolio` corpus and the site will show them in the
  Portfolio panel. `portfolio/_example.md` explains every field; check your files with
  `python -m services.glassbox.portfolio` (CI runs the same check). Screenshots go in
  `frontend/public/portfolio/<slug>/`. See `docs/DESIGN-003-ingestion.md` section 1.3.
- The About Basel text is not in this repository. It lives in the owner's private
  repo and is checked out here at release time as `corpus/about-me-private/`
  (git-ignored; see `docs/DESIGN-003-ingestion.md` section 1.2). The ingest scanner
  reads only `corpus/about-me-private/about-me/` for About Basel, so this README is
  never indexed.
