# Jivaka User Manual — Ingestion Scaffold

This covers the current scope of the repository: turning one medical document into its portion of the trinity knowledge graph in FalkorDB. Retrieval (Q&A with citations) and the web interface are not part of this build yet.

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
| `FALKOR_HOST` / `FALKOR_PORT` | FalkorDB connection, as seen by the backend container | `falkordb` / `6379` |
| `FALKOR_GRAPH_NAME` | Name of the single shared graph all documents write into | `jivaka` |
| `OLLAMA_HOST` | Ollama base URL, as seen by the backend container | `http://ollama:11434` |
| `OLLAMA_MODEL` | **Required.** Model used for entity/relation extraction | *(none — must be set)* |
| `DEFINITION_SOURCE` | `stub` (curated offline dataset) or `umls` (not yet implemented) | `stub` |
| `UPLOAD_DIR` | Where uploaded files are written inside the backend container | `/app/data/uploads` |
| `OCR_TEXT_DENSITY_THRESHOLD` | Chars-per-pixel² below which a page is treated as scanned and routed to OCR | `0.005` (placeholder, untuned) |

## 3. Running the stack

```bash
docker compose up --build
```

This starts, in order:
1. **falkordb** — the graph database. Browser UI at [http://localhost:3000](http://localhost:3000); protocol port `6379`.
2. **ollama** — the local LLM server, port `11434`.
3. **ollama-pull** — a one-shot job that pulls `OLLAMA_MODEL` into the `ollama` service, then exits. Ignore its "exited with code 0" status; that's success.
4. **backend** — the Jivaka API, port `8000`.

Check everything is healthy:

```bash
docker compose ps
```

## 4. Ingesting a document

Place a file under `data/uploads/` (bind-mounted into the backend container), then:

```bash
docker compose exec backend jivaka ingest /app/data/uploads/your-file.pdf --doc-type textbook --print-graph
```

- `--doc-type` is required: `patient_record` or `textbook`. There is no automatic classifier yet — you tell it which chunking strategy to use.
- Supported file types: `.pdf`, `.txt`, `.png`, `.jpg`, `.jpeg`, `.tiff`.
- `.pdf` pages with a real text layer are read directly; pages that look scanned (or any raw image) are routed through local, offline OCR (EasyOCR) automatically. No flag needed.
- `--print-graph` prints the resulting Chunk → Entity → Definition structure as a tree, plus any extracted entity relations.

Output includes the `doc_id` (a UUID) — you'll need it for the next step.

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

```bash
curl -X POST http://localhost:8000/ingest \
  -F "file=@/path/to/local/file.pdf" \
  -F "doc_type=textbook"
```

Returns the same summary JSON as the CLI (`doc_id`, counts, elapsed time). There is no authentication on this API — it's for local verification only, not production/public exposure.

## 7. Graph model, in brief

One shared FalkorDB graph (`FALKOR_GRAPH_NAME`). Every `Chunk` and `Entity` node carries a `doc_id` property scoping it to its source document. `Definition` nodes are deduplicated across documents (merged on normalized term + source) — if two documents both mention "hypertension," they share one `Definition` node.

```
(:Document)-[:HAS_CHUNK]->(:Chunk)-[:MENTIONS]->(:Entity)-[:DEFINED_AS]->(:Definition)
                                                  (:Entity)-[:RELATED_TO {relation_type}]->(:Entity)
```

## 8. Known limitations (current scaffold)

- `DEFINITION_SOURCE=stub` only ships ~10 curated terms (`data/reference/definitions_stub.json`). Real MeSH/UMLS integration requires a UTS license and is not implemented (`DEFINITION_SOURCE=umls` raises `NotImplementedError`).
- Entity extraction quality depends entirely on the Ollama model you choose and its prompt-following ability — there's no dedicated clinical NER model in this version.
- Ingestion is synchronous; there's no batch/async job queue.
- Re-ingesting the same file (same content hash) is not deduplicated — it currently just creates another `Document` node.
- No retrieval/Q&A layer and no web UI yet.

## 9. Troubleshooting

- **`backend` container fails immediately on startup:** almost always a missing `OLLAMA_MODEL` in `.env` — the app fails fast rather than silently using an undefined model.
- **`jivaka ingest` hangs on first run:** the Ollama model may still be downloading (check `docker compose logs ollama-pull`), or EasyOCR is downloading its model weights on first OCR use (check `docker compose logs backend`).
- **FalkorDB browser UI shows an empty graph:** confirm you're viewing the graph named in `FALKOR_GRAPH_NAME` (default `jivaka`), not a different/default graph.
