# Jivaka

Jivaka is a medical knowledge tool built around a three-tier "trinity" knowledge graph — **Chunks** (patient records / textbook passages) → **Entities** (symptoms, diagnoses, treatments) → **Definitions** (verified MeSH/UMLS-style dictionary entries) — stored in [FalkorDB](https://www.falkordb.com/).

This repository implements the **ingestion foundation** - turning one uploaded medical document (PDF, text, image, EPUB, or MOBI) into its portion of the trinity graph, with patient records de-identified first - and a **chat page** that answers Thai or English questions from the whole graph, citing the chunks it used. Document intake, OCR fallback, de-identification, chunking, entity extraction (via a local Ollama LLM), definition linking, graph retrieval and streamed, cited answers are implemented.

See [docs/user-manual.md](docs/user-manual.md) for detailed setup and usage instructions.

## Quick start

1. Copy the environment template and set your Ollama model:
   ```bash
   cp .env.example .env
   # edit .env and set OLLAMA_MODEL, e.g. OLLAMA_MODEL=llama3.1
   ```
2. Start the stack:
   ```bash
   docker compose up --build
   ```
   This starts FalkorDB (graph DB + browser UI on port 3000), Ollama (pulls `OLLAMA_MODEL` on first boot), and the Jivaka backend (API + web page on port 8000).
3. (Alternative to `docker compose` commands directly: `scripts/deploy.sh` builds and starts everything, `scripts/start.sh`/`scripts/stop.sh`/`scripts/restart.sh` manage an already-built stack — see [docs/user-manual.md](docs/user-manual.md#3-running-the-stack).)

   Open [http://localhost:8000](http://localhost:8000), upload a document, and watch it ingest, then ask questions at [http://localhost:8000/chat](http://localhost:8000/chat) — or use the CLI (`jivaka ask "..."` for questions):
   ```bash
   docker compose exec backend jivaka ingest /app/data/uploads/your-file.pdf --doc-type textbook --print-graph
   ```
4. Inspect the resulting graph:
   ```bash
   docker compose exec backend jivaka graph-show <doc_id>
   ```
   or open the FalkorDB browser UI at [http://localhost:3000](http://localhost:3000) and run Cypher directly.

## Architecture

- **backend/** — Python (FastAPI + Typer) ingestion service. See [backend/src/jivaka](backend/src/jivaka) for the pipeline: intake → OCR fallback → chunking → entity extraction → definition linking → graph write. Ingestion runs as a background job (`POST /ingest` returns a `job_id` to poll via `GET /jobs/{job_id}`) rather than blocking, since a document can take minutes. `backend/src/jivaka/web/index.html` is the upload/status/graph-view page served at `GET /`.
- **backend/src/jivaka/deid** — de-identification of patient records before chunking (rules + spaCy/PyThaiNLP NER, `[PERSON-1]`-style placeholders, `P-0001` pseudonyms). **retrieval/** matches question terms to entities and ranks chunks across the whole graph; **chat/** builds the cited prompt and streams the Ollama answer. `POST /chat` (Server-Sent Events) and `GET /chunks/{id}` back the chat page at `GET /chat` (`web/chat.html`). See [docs/user-manual.md](docs/user-manual.md#7-asking-questions-chat).
- **FalkorDB** — single shared graph; every node/edge carries a `doc_id` property, and `Definition` nodes are deduplicated/reused across documents.
- **Ollama** — serves the local LLM used for entity/relation extraction. No default model is baked in; set `OLLAMA_MODEL` in `.env`.
- **corpus/** — standalone data-acquisition tool (separate from the backend service) that downloads the medical source documents/vocabularies used to build the graph, into a license-segregated local archive. See [corpus/README.md](corpus/README.md). Populated from research in the `zoac-zeus-jivaka` git submodule.

## Project working rules

- Any code change is documented in this README and [docs/user-manual.md](docs/user-manual.md) in the same change.
- New dependencies are added to `backend/pyproject.toml` (and lockfile) in the same change that introduces them.
- Changes to system-level dependencies (new services, env vars, volumes, ports) are reflected in `docker-compose.yml` and `backend/Dockerfile` in the same change.

## Status

Ingestion, de-identification, and a v1 chat page with entity-based retrieval and cited answers. Next: full-text and embedding search for questions that don't name a known entity.

**Security:** the backend has no login. With chat, anyone who can reach port 8000 can read de-identified patient records in plain language (and the ingestion API still serves filenames and graphs). Before any deployment beyond a private test host: put a login in front of the whole backend, bind it to localhost behind a TLS reverse proxy, restrict patient documents per user, and log chat questions with the chunks they were shown.
