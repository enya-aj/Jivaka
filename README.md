# Jivaka

Jivaka is a medical knowledge tool built around a three-tier "trinity" knowledge graph — **Chunks** (patient records / textbook passages) → **Entities** (symptoms, diagnoses, treatments) → **Definitions** (verified MeSH/UMLS-style dictionary entries) — stored in [FalkorDB](https://www.falkordb.com/).

This repository currently implements the **ingestion foundation** plus a small web page for driving it: turning one uploaded medical document (PDF, text, image, EPUB, or MOBI) into its portion of the trinity graph. Document intake, OCR fallback, chunking, entity extraction (via a local Ollama LLM), and definition linking are all implemented; question-answering retrieval and a real chat interface are planned separately and not yet built.

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

   Open [http://localhost:8000](http://localhost:8000), upload a document, and watch it ingest — or use the CLI:
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
- **FalkorDB** — single shared graph; every node/edge carries a `doc_id` property, and `Definition` nodes are deduplicated/reused across documents.
- **Ollama** — serves the local LLM used for entity/relation extraction. No default model is baked in; set `OLLAMA_MODEL` in `.env`.
- **corpus/** — standalone data-acquisition tool (separate from the backend service) that downloads the medical source documents/vocabularies used to build the graph, into a license-segregated local archive. See [corpus/README.md](corpus/README.md). Populated from research in the `zoac-zeus-jivaka` git submodule.

## Project working rules

- Any code change is documented in this README and [docs/user-manual.md](docs/user-manual.md) in the same change.
- New dependencies are added to `backend/pyproject.toml` (and lockfile) in the same change that introduces them.
- Changes to system-level dependencies (new services, env vars, volumes, ports) are reflected in `docker-compose.yml` and `backend/Dockerfile` in the same change.

## Status

Ingestion scaffold + a minimal ingestion web page. Retrieval (U-retrieval / Q&A with citations) and a real chat interface are future work — not part of this repository yet.
