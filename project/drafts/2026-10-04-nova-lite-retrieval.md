# Retrieval design for a small model (Nova Lite)

Draft from the 2026-10-04 session (BACKLOG RESUME HERE items 5 and 6). Applies to the About Basel corpus, which lives in the private repo; no About Basel text belongs in this public repo, so the synthetic cheat-sheet examples from that session are not included here.

## 1. Chunking strategy (under 300 words, no lost context)

A small model can't recover context the chunk doesn't carry, so **every chunk must stand alone**.

1. **One H2 section = one chunk.** Your files are already organized this way, and most H2 sections run 80–200 words. Never merge two H2s into one chunk: a "Kafka + rate limiting" blend dilutes both embeddings.
2. **Split only above 300 words.** Split at paragraph boundaries (never mid-sentence) and repeat the header (rule 3) on every piece. No sliding-window overlap is needed once each piece carries its header.
3. **Prepend a context header to every chunk**, generated from the file front matter and H1/H2, not written by hand:
   `[<Company> · <Role>, <team> · <period as written>] <H2 heading>`
   `[Personal project · basel.engineering (Glassbox)] Retrieval pipeline and caching`
   The header goes into both the embedded text and the prompt, so an orphaned paragraph ("cut outages from 2/month to 0") still says where and when it happened.
4. **The first sentence names the subject.** Your files already do this ("At <Company>, Basel…", "On <team>, Basel…"). Keep it as a lint rule: a chunk whose first sentence has no company or project name fails ingest.
5. **One employer or project per chunk.** Cross-cutting views (one skill across jobs and projects) would live only in synthetic cheat-sheet chunks (deferred; see BACKLOG "Decided, don't build"), so the raw chunks stay attributable.
6. **Skills and bio stay as their own chunk types** (`skills_summary`, `bio`), each H2 its own chunk. They are the natural backup for "do you know X?" questions.
7. **Tags come from a controlled vocabulary**: an alias map (`k8s`/`k3s` → `kubernetes`, `ARM templates` → `arm`, `Azure Data Explorer` → `kusto`) applied at ingest, so a filter on `kubernetes` matches every spelling.

This matches RAG phase 7 (section-aware chunks); the header and lint rules are the additions.

## 2. Metadata schema

Store these as RediSearch TAG/NUMERIC fields next to the vector, so filtering happens in the same query as KNN, e.g.
`(@experience_type:{professional} @tech_stack:{redis})=>[KNN 4 @embedding $vec]`.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://basel.engineering/schemas/chunk-metadata.json",
  "title": "ChunkMetadata",
  "type": "object",
  "additionalProperties": false,
  "required": ["chunk_id", "corpus", "source", "section", "experience_type", "tech_stack", "skill_areas", "is_synthetic", "content_sha"],
  "properties": {
    "chunk_id":   { "type": "string", "pattern": "^[a-z0-9-]+#[0-9]+$", "description": "source slug + ordinal, e.g. microsoft-rate-limiting#0" },
    "corpus":     { "enum": ["about_basel", "about_system", "portfolio"] },
    "source":     { "type": "string", "description": "file path, e.g. private/microsoft.md" },
    "section":    { "type": "string", "description": "H2 heading the chunk came from" },
    "experience_type": {
      "enum": ["professional", "personal_project", "education", "bio", "skills_summary", "fun_facts", "cheat_sheet"],
      "description": "Drives dual-experience retrieval: one professional + one personal_project slot."
    },
    "organization": { "type": ["string", "null"], "description": "employer or school, from the file front matter" },
    "team":         { "type": ["string", "null"], "description": "team within the organization, if any" },
    "role":         { "type": ["string", "null"], "description": "e.g. Software Engineer, IT Application Development Intern" },
    "project_name": { "type": ["string", "null"], "description": "personal project name, from the file front matter" },
    "period": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "label":   { "type": "string", "description": "as written in the source: 'ongoing, approaching 3 years', 'June–August 2018'" },
        "current": { "type": "boolean" }
      }
    },
    "tech_stack": {
      "type": "array", "uniqueItems": true,
      "items": { "type": "string", "pattern": "^[a-z0-9.+#-]+$" },
      "description": "Canonical lowercase names after the alias map: redis, kafka, kubernetes, terraform, csharp, java, typescript, service-fabric, ev2, arm, kusto, ..."
    },
    "skill_areas": {
      "type": "array", "uniqueItems": true,
      "items": { "enum": ["backend", "distributed-systems", "messaging", "data-stores", "caching", "infrastructure-as-code", "cloud", "release-engineering", "ci-cd", "testing", "frontend", "mobile", "observability", "security", "ai", "leadership"] }
    },
    "has_metrics":  { "type": "boolean", "description": "chunk contains a quantified outcome; boosts 'impact' questions" },
    "is_synthetic": { "type": "boolean", "description": "true for cheat sheets; never quoted as a primary source" },
    "derived_from": { "type": "array", "items": { "type": "string" }, "description": "cheat sheets only: chunk_ids they summarize, so a source edit flags the sheet stale" },
    "priority":     { "enum": ["high", "normal", "low"] },
    "word_count":   { "type": "integer", "maximum": 300 },
    "content_sha":  { "type": "string", "pattern": "^[a-f0-9]{64}$" }
  }
}
```

Filter recipes:

| Question shape | Filter |
|---|---|
| "Have you used X?" | slot 1 `experience_type:professional tech_stack:x`; slot 2 `experience_type:personal_project tech_stack:x`; back-fill from `cheat_sheet`/`skills_summary` |
| "What did you do at <Company>?" | `organization:<Company>` |
| "Tell me about <Project>?" | `project_name:"<Project>"` |
| "Biggest impact?" | `has_metrics:true`, `priority:high` |
| Casual / fun facts | `experience_type:fun_facts` (and route to the light-tone prompt) |
