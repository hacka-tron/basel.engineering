# corpus/

The About Basel text is not in this repository. It lives in the owner's private
repo and is checked out here at release time as `corpus/about-me-private/`
(git-ignored; see `docs/DESIGN-003-ingestion.md` section 1.2). This file exists so
the Dockerfile's `COPY corpus/ corpus/` has a tracked source when a build runs
without that checkout. The ingest scanner reads only `corpus/about-me-private/about-me/`
(and a legacy `corpus/about-me/` if present), so this README is never indexed.
