# Jivaka User Manual — Ingestion Scaffold

This covers the current scope of the repository: turning one medical document into its portion of the trinity knowledge graph in FalkorDB, plus a small web page for doing that by hand. Retrieval (Q&A with citations) and a real chat interface are not part of this build yet.

## 1. Prerequisites

- Docker and Docker Compose.
- An Ollama-compatible model name you're willing to pull (e.g. `llama3.1`). No model is picked for you.

## 2. Setup

```bash
cp .env.example .env
```

Edit `.env`:

| Variable | Purpose | Default |
|---|---|---|
| `COMPOSE_PROFILES` | `ollama-managed` starts the bundled `ollama`/`ollama-pull` services; clear it to reuse an Ollama already running on the host (see below) | `ollama-managed` |
| `FALKOR_PORT` / `FALKOR_UI_PORT` | Host ports published for FalkorDB's protocol / browser UI | `6379` / `3000` |
| `BACKEND_PORT` | Host port published for the Jivaka API | `8000` |
| `FALKOR_HOST` | FalkorDB hostname, as seen by the backend container | `falkordb` |
| `FALKOR_GRAPH_NAME` | Name of the single shared graph all documents write into | `jivaka` |
| `OLLAMA_HOST` | Ollama base URL, as seen by the backend container | `http://ollama:11434` |
| `OLLAMA_MODEL` | **Required.** Model used for entity/relation extraction | *(none — must be set)* |
| `OLLAMA_REQUEST_TIMEOUT_SECONDS` | How long to wait for one chunk's extraction call before giving up (raise this if Ollama is shared/contended) | `300` |
| `DEFINITION_SOURCE` | `stub` (curated offline dataset) or `umls` (not yet implemented) | `stub` |
| `UPLOAD_DIR` | Where uploaded files are written inside the backend container | `/app/data/uploads` |
| `OCR_TEXT_DENSITY_THRESHOLD` | Chars-per-pixel² below which a page is treated as scanned and routed to OCR | `0.005` (placeholder, untuned) |

### Reusing an existing Ollama instead of the bundled one

If the host already runs Ollama (natively or in another stack) with your model already pulled, don't run a second one:
```bash
COMPOSE_PROFILES=
OLLAMA_HOST=http://host.docker.internal:11434
OLLAMA_MODEL=<a model already pulled there>
```
`host.docker.internal` resolves to the host from inside the backend container on both Docker Desktop and Linux (the compose file adds the needed `extra_hosts` entry for Linux).

### Avoiding port collisions

If `3000` or `8000` are already taken by something else on the host, set `FALKOR_UI_PORT` / `BACKEND_PORT` (and `FALKOR_PORT` if `6379` collides too) to free ports instead — everything else about the stack is unaffected.

## 3. Running the stack

```bash
docker compose up --build
```

or the equivalent wrapper script:

```bash
scripts/deploy.sh
```

This starts (with default `.env`):
1. **falkordb** — the graph database. Browser UI at `http://localhost:${FALKOR_UI_PORT}` (default [http://localhost:3000](http://localhost:3000)); protocol port `${FALKOR_PORT}` (default `6379`).
2. **ollama** — the local LLM server, port `11434`. Skipped if `COMPOSE_PROFILES` is cleared (reusing an existing Ollama instead).
3. **ollama-pull** — a one-shot job that pulls `OLLAMA_MODEL` into the `ollama` service, then exits. Ignore its "exited with code 0" status; that's success. Also skipped when `COMPOSE_PROFILES` is cleared.
4. **backend** — the Jivaka API, port `${BACKEND_PORT}` (default `8000`).

Check everything is healthy:

```bash
docker compose ps
```

### Managing an already-built stack

Four scripts under `scripts/` wrap the equivalent `docker compose` commands (all resolve paths relative to the repo root, so they can be run from anywhere):

| Script | Equivalent | When to use |
|---|---|---|
| `scripts/deploy.sh` | `docker compose build && docker compose up -d` | First run, or after source/dependency changes |
| `scripts/start.sh` | `docker compose up -d` | Start using images already built - no rebuild |
| `scripts/stop.sh` | `docker compose down` | Stop and remove containers - volumes/data untouched |
| `scripts/restart.sh` | `docker compose restart` | Quick in-place restart, e.g. after an `.env` change |

None of these touch volumes or bind-mounted data (FalkorDB's graph, `data/uploads/`) - only `docker ... -v`/`--volumes` flags would, and none of these scripts pass them.

## 4. Ingesting a document

**Easiest: the web page.** Open `http://localhost:${BACKEND_PORT}` (default [http://localhost:8000](http://localhost:8000)) in a browser — upload a file, pick `patient_record`/`textbook`, and watch the status update (queued → running, with a `chunk N/M` progress marker → succeeded/failed) until the resulting graph renders below. A "recent documents" list lets you revisit past ingests without remembering their `doc_id`. See §6 for what's happening under the hood.

**Or the CLI**, from within the `data/uploads/` folder (bind-mounted into the backend container):

```bash
docker compose exec backend jivaka ingest /app/data/uploads/your-file.pdf --doc-type textbook --print-graph
```

- `--doc-type` is required: `patient_record` or `textbook`. There is no automatic classifier yet — you tell it which chunking strategy to use.
- Supported file types: `.pdf`, `.txt`, `.png`, `.jpg`, `.jpeg`, `.tiff`, `.epub`, `.mobi`.
- `.pdf` pages with a real text layer are read directly; pages that look scanned (or any raw image) are routed through local, offline OCR (EasyOCR) automatically. No flag needed. `.epub`/`.mobi` are always clean text (one "page" per chapter) and never go through OCR.
- `--print-graph` prints the resulting Chunk → Entity → Definition structure as a tree, plus any extracted entity relations.

Output includes the `doc_id` (a UUID) — you'll need it for the next step. Note: the CLI's `jivaka ingest` runs synchronously and blocks until done (unlike the web page/API, which run it as a background job — see §6).

## 5. Inspecting the resulting graph

Three ways, in order of convenience:

**CLI:**
```bash
docker compose exec backend jivaka graph-show <doc_id>
```

**FalkorDB browser UI:** open [http://localhost:3000](http://localhost:3000), select the `jivaka` graph, and run Cypher directly, e.g.:
```cypher
MATCH (d:Document {id: "<doc_id>"})-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(e:Entity)
OPTIONAL MATCH (e)-[:DEFINED_AS]->(def:Definition)
RETURN c, e, def
```

**HTTP API:**
```bash
curl http://localhost:8000/documents/<doc_id>/graph
```

## 6. Using the HTTP API directly

`POST /ingest` no longer blocks until ingestion finishes (it used to) — since a document can take 30s–3min (LLM calls per chunk), it now saves the upload, starts ingestion as a background job, and returns immediately:

```bash
curl -X POST http://localhost:8000/ingest \
  -F "file=@/path/to/local/file.pdf" \
  -F "doc_type=textbook"
# => {"job_id": "...", "status": "queued"}
```

Poll the job until it reaches a terminal state:

```bash
curl http://localhost:8000/jobs/<job_id>
# => {"id": "...", "status": "running", "progress": "chunk 2/5", "result": null, "error": null}
# ... later ...
# => {"id": "...", "status": "succeeded", "progress": "chunk 5/5", "result": {...same shape the old blocking response had...}, "error": null}
```

`status` is one of `queued`, `running`, `succeeded`, `failed`. On `failed`, `error` holds the exception message instead of `result` — this is what lets you see a mid-process failure (a bad file, an LLM error, etc.) rather than it disappearing into a dropped/timed-out request. Job state is in-memory only: it's lost on a backend restart, and isn't shared across replicas (this stack only runs one `backend` instance, so that's not a problem today — see the deferred-decisions notes if that changes).

`GET /documents` lists previously ingested documents (`id`, `filename`, `doc_type`, `ingested_at`, newest first) — what the web page's "recent documents" panel uses.

There is no authentication on this API — it's for local verification only, not production/public exposure.

## 7. Graph model, in brief

One shared FalkorDB graph (`FALKOR_GRAPH_NAME`). Every `Chunk` and `Entity` node carries a `doc_id` property scoping it to its source document. `Definition` nodes are deduplicated across documents (merged on normalized term + source) — if two documents both mention "hypertension," they share one `Definition` node.

```
(:Document)-[:HAS_CHUNK]->(:Chunk)-[:MENTIONS]->(:Entity)-[:DEFINED_AS]->(:Definition)
                                                  (:Entity)-[:RELATED_TO {relation_type}]->(:Entity)
```

## 8. Known limitations (current scaffold)

- `DEFINITION_SOURCE=stub` only ships ~10 curated terms (`data/reference/definitions_stub.json`). Real MeSH/UMLS integration requires a UTS license and is not implemented (`DEFINITION_SOURCE=umls` raises `NotImplementedError`).
- Entity extraction quality depends entirely on the Ollama model you choose and its prompt-following ability — there's no dedicated clinical NER model in this version.
- One document ingests at a time per request, and each runs its own background job — there's no batch/queue-based ingestion of many documents at once, and job status is in-memory only (lost on a backend restart).
- `.mobi` parsing quality varies by file — the unpacking library's output isn't perfectly uniform across older MOBI vs. newer hybrid (KF8/AZW3) files; treat it as best-effort until tested against real files.
- Re-ingesting the same file (same content hash) is not deduplicated — it currently just creates another `Document` node.
- No retrieval/Q&A layer or chat interface yet. The web page only covers ingestion + viewing one document's graph at a time (never a merged, cross-document view).

## 9. Troubleshooting

- **`backend` container fails immediately on startup:** almost always a missing `OLLAMA_MODEL` in `.env` — the app fails fast rather than silently using an undefined model.
- **`jivaka ingest` hangs on first run:** the Ollama model may still be downloading (check `docker compose logs ollama-pull`), or EasyOCR is downloading its model weights on first OCR use (check `docker compose logs backend`).
- **FalkorDB browser UI shows an empty graph:** confirm you're viewing the graph named in `FALKOR_GRAPH_NAME` (default `jivaka`), not a different/default graph.
