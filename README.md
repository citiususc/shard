# br2shacl-ui

Self-contained web UI for **text2shacl** — co-construct SHACL validation shapes
from an OWL ontology and business rules, with a human in the loop.

It wraps the real text2shacl pipeline (vendored under `text2shacl_core/`) behind
four small local services and a two-page frontend. Inference runs through the
text2shacl `model_loader` and is configured from the **Models** panel in the UI
(provider, credentials, model ids and temperature).

---

## Two workflows

The UI is split into two pages, both sharing the same ontology, model
configuration, prefixes and accepted-shape list (persisted in the browser):

1. **Rule → Shape** (`rule.html`) — write a single business rule, pick the
   ontology target (or rank related classes/properties directly inside Search
   ontology), and generate one SHACL shape to edit, validate and accept. Ontology
   term embeddings are prepared in the background after upload, once model
   settings are configured, and cached in memory by ontology content hash,
   embedding model and inference configuration for the service session. Uses
   the real generator prompts and the rdflib parse-and-retry loop.

2. **Guide → Shapes** (`guide.html`) — upload the full application guide (HTML or
   PDF). The multi-agent pipeline generates a shape for every ontology property
   and **streams them in one by one** over Server-Sent-Events, with a
   `X / Y SHACL shapes generated` progress bar. Shapes that still fail to parse
   after 10 attempts are surfaced anyway, marked invalid with the parser error,
   so you can fix them by hand.

---

## Layout

```
br2shacl-ui/
├── run_demo.py                 # starts the 4 services + static web server
├── requirements.txt
├── README.md
├── demo/                       # frontend (static)
│   ├── index.html              # landing
│   ├── rule.html  / rule.js    # Workflow 1
│   ├── guide.html / guide.js   # Workflow 2
│   ├── common.js               # shared state, models, ontology, prefixes, export
│   └── styles.css
├── services/
│   ├── parse_ontology.py       # :9100  parse + derive base namespace + prefixes
│   ├── find_relevant_terms.py  # :9101  semantic ranking (embeddings) + lexical fallback
│   ├── build_shacl_shapes.py   # :9102  single-rule generation + /validate-shape
│   └── generate_from_guide.py  # :9103  full-guide generation, streamed (SSE)
└── text2shacl_core/            # vendored text2shacl (the real pipeline)
    ├── model_loader*.py · runtime_config.py · utils.py · prompts.py · Logger.py
    ├── preprocess_html*.py · multiagent.py · rag.py
    ├── ns_utils.py             # NEW: generic namespace/prefix derivation
    ├── rag_inmemory.py         # NEW: in-memory RAG (no Redis) for Mode B
    ├── multiagent_stream.py    # NEW: streaming, generic generation
    └── prompts/ (multiagent.json · rag.json)
```

### Relation to text2shacl

`text2shacl_core/` is a vendored copy of the original source, kept intact except
for three surgical edits so the demo runs self-contained without a GPU or Redis:

* `utils.py` — removed the dead `from enrich_sparql_constraints import enrich`
  import (`enrich()` is defined locally).
* `model_loader.py` — routes by the provider selected in the UI, with the old
  HuggingFace-by-slash heuristic as fallback.
* `model_loader_databricks.py` — accepts UI-supplied Databricks credentials and
  normalizes older shortened aliases to the actual `databricks-*` endpoint
  names sent to the Databricks Serving API.
* `multiagent.py` — `torch.cuda.empty_cache()` is guarded by
  `torch.cuda.is_available()`.

Genericity (any ontology, not just ERA) is added in `ns_utils.py`: the base
namespace is derived from the uploaded ontology and the ERA-specific prefix block
is replaced by one built from the ontology's own prefixes, with `era:`/`era-sh:`
aliased to the base/shapes namespaces so the generator prompts keep working.

Mode B avoids Redis by using an in-memory docstore + ephemeral Chroma
(`rag_inmemory.py`), and streams per-property results via `multiagent_stream.py`.

---

## Setup

```bash
python3 -m pip install -r requirements.txt
```

No `.env` file is required or read by `run_demo.py`. Configure inference from the
**Models** panel in either workflow page:

1. Choose **Databricks** or **Hugging Face**.
2. For Databricks, paste the AI Gateway / Serving base URL and token.
3. For Hugging Face, paste a token only if the selected model is gated/private.
4. Select model ids per role or add a custom model manually.
5. Adjust temperature.

The Databricks catalog uses the endpoint names that are sent to the Serving API
(`databricks-qwen3-embedding-0-6b`, `databricks-gemini-3-5-flash`, etc.).
Custom Databricks model ids should therefore match the endpoint name deployed in
your workspace. Hugging Face keeps its repository-style ids (`org/model`). The UI
validates a custom model before adding it.

> Selecting the **HuggingFace** backend runs inference locally and additionally
> requires `torch` and `transformers` (and, realistically, a GPU for the large
> models). These are intentionally **not** in `requirements.txt`; the Databricks
> backend needs neither.

---

## Run

```bash
python3 run_demo.py
```

Then open:

* Landing:        http://127.0.0.1:8768/index.html
* Rule → Shape:   http://127.0.0.1:8768/rule.html
* Guide → Shapes: http://127.0.0.1:8768/guide.html

`Ctrl+C` stops the web server and all four services.

---

## Notes

* **Backends & models.** The Inference backend toggle (Databricks / HuggingFace)
  drives which models appear in each dropdown, split by role: chat (generation and
  guide summaries), multimodal/vision (image description in the guide workflow) and
  embedding (term ranking and RAG indexing). Databricks endpoint names must match
  those deployed in your workspace; if validation fails, pick a model that is
  **Ready** in your Serving tab.
* **First call latency.** The first embedding/generation call is slow (model
  warm-up + Databricks rate throttling). Subsequent calls reuse cached objects;
  the term-ranking service caches the entity embedding matrix per ontology and
  inference configuration. If Databricks token/base URL are missing, semantic
  ranking is not started and the UI shows that model settings must be configured.
* **PDF guides.** PDFs are converted to text best-effort (`pdfminer.six`) and
  treated as the v1.6.1 (from-PDF) format; images are not extracted from PDFs.
* **Export.** "Export accepted shapes" writes a single `.ttl` combining the
  editable prefix block, the aggregated `sh:NodeShape` block (guide workflow) and
  every accepted shape body.
* **Session import/export.** "Export session" saves the loaded ontology, editable
  prefixes, accepted shapes and non-sensitive model settings. It intentionally
  excludes tokens and workspace URLs.
* The browser-side validation "Check" button calls a real rdflib parse on
  `:9102/validate-shape` — it is not a heuristic.
```
