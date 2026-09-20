# Jivaka Corpus Data Acquisition Tool

Downloads the medical knowledge-graph source data documented in the `zoac-zeus-jivaka` submodule's research (see its `-data/` and `ai/system-design/data-sources/`) into a local, license-segregated archive. Standalone tool — not part of the running Jivaka backend service, never built into its Docker image.

## Setup

```bash
cd corpus
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Usage

```bash
# See what would happen without touching the network or disk
.venv/bin/python fetch.py run --dry-run

# Fetch everything in the registry
.venv/bin/python fetch.py run

# Fetch a subset
.venv/bin/python fetch.py run --category vocabulary --license-class green
.venv/bin/python fetch.py run --names mesh-desc-2026,mondo-owl

# Regenerate skip-report.md from the existing manifest without fetching
.venv/bin/python fetch.py report
```

Safe to re-run: anything already downloaded and verified is skipped (re-hashed and compared, not blindly trusted).

## What you get

- **`archive/`** (gitignored) — the actual downloaded files, organized `license_class/category/source-name/raw/`. Never enters git.
- **`manifest.tsv`** (gitignored) — a ledger of every attempt: status, size, sha256, timestamp.
- **`skip-report.md`** (gitignored) — regenerated every run, lists everything that needs manual action (registration-gated sources, Cloudflare-blocked sites, confirmed-dead links) with instructions.
- **`registry/sources.yaml`** (tracked in git) — the source-of-truth list of what to fetch, and how to classify it.

## License policy

Every entry is tagged `green` (confirmed safe for production), `amber` (non-commercial-only, or license unconfirmed - e.g. WHO guidelines, OpenStax despite its CC BY label, ICD-10-TM), or `red` (no redistribution at all - e.g. the HuggingFace `MedRAG/textbooks` dataset of 18 copyrighted textbooks). This is enforced by folder structure in `archive/` — `amber/` and `red/` subtrees should never be pointed at by production ingestion.

## What this tool does NOT do

- No login/CAPTCHA automation for gated sources (UMLS, THIS/สมสท., Athena, Semantic Scholar) - these are enumerated in the registry as `fetchable: false` and reported, never attempted.
- No full PubMed baseline crawl (~1,200 files) - only what's already enumerated in the registry.
- No `git clone`/git-lfs support - a couple of HuggingFace dataset entries need that instead of plain HTTP GET and are marked accordingly.
- No post-processing (OCR, PDF text extraction, Thai font remapping) - see the submodule's own `ICD-10-TM/_extract-icd10tm.py` for that, once files land in `archive/`.
