# Dependency and model provenance

This file is an auditable inventory, not a license grant. The repository's
`LICENSE` covers only project-owned code. Third-party packages and model
artifacts remain governed by their upstream licenses and terms.

## Direct Python dependencies

Runtime requirements are declared in `requirements.txt`; the CPU-only CI subset
is declared in `requirements-ci.txt`.

| Dependency | Declared constraint / role |
|---|---|
| FastAPI | `>=0.110.0,<0.120.0` — HTTP API |
| Starlette | `>=0.36.0` — ASGI/web primitives |
| Uvicorn | `>=0.27.0` (`[standard]` locally) — ASGI server |
| Jinja2 | `>=3.1.0` — HTML templating |
| Pydantic | `>=2.6.0` — validation/settings models |
| Requests | `>=2.31.0` — HTTP clients, including Ollama/RT calls |
| python-dotenv | `>=1.0.0` — environment loading |
| HTTPX | `>=0.27.0` — HTTP/test client |
| LanceDB | `==0.37.1` — local vector index |
| PyTorch | `==2.12.1` — local reranker runtime |
| Transformers | `>=4.45.0,<5.0.0` — local reranker runtime |
| pypdf | `>=6.14.0,<7.0.0` — PDF text extraction |
| pypdfium2 | `>=4.30.0,<5.0.0` — PDF rendering |
| Pillow | `>=12.0.0,<13.0.0` — image handling |

The resolved transitive dependency graph can differ by platform. Before a
binary or packaged distribution, generate and review the licenses of the exact
resolved environment; do not infer transitive licenses from this direct list.

## External model artifacts

The repository references local model artifacts but does not grant rights to
redistribute them:

- analysis model: `qwen3.5:9b-q4_K_M` via Ollama;
- embedding model: `bge-m3` via Ollama;
- reranker: `BAAI/bge-reranker-v2-m3`;
- OCR/vision: `llama3.2-vision`.

Model files are expected to be acquired separately from their upstream source.
Their upstream model-card/license terms apply independently of this repository.

## Legal corpus

`data/laws.json` contains imported legal-source records originating from Riigi
Teataja. The application treats those records as source material, not as
project-owned prose. Corpus identity and audit provenance are recorded in
`data/corpus_manifest.json`.
