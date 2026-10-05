# Resync the About Basel corpus from the bullet bank

Repeated owner request: "I updated my bullet bank; pick it up." Done 2026-10-03 (docs PR #4) and 2026-10-04 (docs PR #6). Follow this instead of re-deriving it.

**Sources.** The bullet bank is the Google Doc "Resume Bullet Bank: Basel Abdel-Rahman" in the owner's Drive (find it with the Drive connector: `title contains 'bullet'`). The corpus is the private repo `hacka-tron/basel.engineering-docs`, under `about-me/` (`bio`, `google`, `microsoft`, `projects`, `skills`, `personal`). No About Basel text ever goes into this public repo.

**Steps.**
1. Compare the doc's `modifiedTime` with the docs repo's last commit (`gh api 'repos/hacka-tron/basel.engineering-docs/commits?per_page=3'`). If the doc is older, there is nothing to do.
2. Read the doc once with `read_file_content` (plain text, about 15 KB; not `read_doc`, whose JSON is far larger). Clone the docs repo into the scratchpad with https: `git clone -c credential.helper='!gh auth git-credential' https://github.com/hacka-tron/basel.engineering-docs.git`.
3. Diff fact by fact: dates, numbers, technologies, names. Apply them as targeted replacements in the existing prose. Keep the corpus voice: third person ("Basel…"), H2 sections whose first sentence names the company or project, front-matter tags.
4. **Never copy the doc's "Profile and application answers"** (salary, home address, application rules) or the resume formatting instructions. The corpus feeds a public chatbot. Work authorization, availability and location go in only when the owner decides (the example-answer sign-off page lists them as corpus gaps).
5. Check that every H2 section stays under 300 words. Commit in the docs repo with a repo-local identity (`git config user.name/user.email` from this repo's recent commits; the scratch clone has none). Open a PR listing the changes per file, then merge it (the owner asked for the sync).
6. It goes live with the next Release (any merge to `main` here, or Actions → Release, which needs the owner's click). Tell the owner which applies.
7. Follow-ups: update golden cases whose facts changed, tell any running eval agent (keep one index per comparison), and refresh draft example answers that cite the changed facts.
