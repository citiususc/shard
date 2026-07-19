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

2. **Guide → Shapes** (`guide.html`) — upload a filled Business Rules template
   (`.html` or `.md`) based on the templates available in the UI. The service
   validates the template structure, extracts the non-empty business rules,
   resolves each rule to ontology targets, then generates and **streams shapes by
   rule** over Server-Sent Events. Progress includes target resolution, generation,
   validation and unresolved rules. The legacy property-first iteration mode is
   still available in the backend as a comparative baseline.

Both workflows can load an Astrea-generated SHACL document. Matching shapes are
focused to the selected ontology target and supplied to BR2SHACL as structural
evidence during generation. Loading a baseline does not silently alter the final
export: the user explicitly chooses one of the output strategies described below.

---

## Layout

```
br2shacl-ui/
├── run_demo.py                 # starts the 5 services + static web server
├── requirements.txt
├── README.md
├── demo/                       # frontend (static)
│   ├── index.html              # landing
│   ├── rule.html  / rule.js    # Workflow 1
│   ├── guide.html / guide.js   # Workflow 2
│   ├── templates/              # Business Rules .html/.md upload templates
│   ├── common.js               # shared state, models, ontology, prefixes, export
│   └── styles.css
├── services/
│   ├── parse_ontology.py       # :9100  parse + detect ontology/shape namespaces + prefixes
│   ├── find_relevant_terms.py  # :9101  semantic ranking (embeddings) + lexical fallback
│   ├── build_shacl_shapes.py   # :9102  single-rule generation + /validate-shape
│   └── generate_from_guide.py  # :9103  template-guided generation, streamed (SSE)
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
  converts visible AI Gateway model names such as `gemma-3-12b` into the
  `system.ai.*` identifiers expected by the Databricks OpenAI-compatible API.
* `multiagent.py` — `torch.cuda.empty_cache()` is guarded by
  `torch.cuda.is_available()`.

Genericity (any ontology, not just ERA) is added in `ns_utils.py`: the primary
ontology namespace is selected by class/property coverage, while the generated
shape namespace is tracked separately. Generic prefix blocks preserve the
ontology's declarations, infer conventional aliases only for known vocabularies
that are actually used, and add `onto:` only when the primary namespace has no
named source prefix. Shape subjects use one configurable preferred prefix,
defaulting to `shape:`. The ERA aliases required by the historical prompts are
confined to the optional property-first legacy pipeline.

The Guide rule-first workflow avoids the old property-driven RAG loop: each
business rule is the primary evidence passed to the shared shape builder after
target resolution. The legacy property-first mode continues to use the in-memory
docstore and ephemeral Chroma implementation in `rag_inmemory.py`.

---

## Setup

```bash
python3 -m pip install -r requirements.txt
```

No `.env` file is required or read by `run_demo.py`. Configure inference from the
**Models** panel in either workflow page:

1. Choose **Databricks** or **Hugging Face (local)**.
2. For Databricks, paste the AI Gateway / Serving base URL and token.
3. For Hugging Face, paste a token only if the selected model is gated/private.
4. Select model ids per role or add a custom model manually.
5. Adjust temperature.

The Databricks catalog shows the short AI Gateway names visible in the Databricks
UI (`gemma-3-12b`, `qwen3-embedding-0-6b`, etc.). Requests are sent internally as
`system.ai.*` model ids. Hugging Face keeps its repository-style ids (`org/model`).
The UI validates a custom model before adding it.

> Selecting the **Hugging Face (local)** backend runs inference locally and additionally
> requires `torch`, `transformers`, and `sentence-transformers` (and,
> realistically, a GPU for the large models). These are intentionally **not** in
> `requirements.txt`; the Databricks backend needs neither.

Install the optional local inference dependencies with:

```bash
python3 -m pip install -r requirements-local.txt
```

Run the lightweight local-inference smoke test with public tiny models:

```bash
python3 test/smoke_huggingface_local.py
python3 test/smoke_huggingface_local.py --offline
```

The first run downloads less than 20 MB across the chat, embedding, and vision
test models. The test checks plumbing, cache reuse, and CPU execution; these
tiny models are not suitable for judging generation, ranking, or image
understanding quality.

---

## Run

```bash
python3 run_demo.py
```

The default `local` deployment profile enables both backends. For a hosted
deployment, start the same codebase with:

```bash
python3 run_demo.py --deployment-profile public
```

The `public` profile keeps Databricks remote inference available and disables
local Hugging Face execution. The UI shows a link to the
[`citiususc/br2shacl-ui`](https://github.com/citiususc/br2shacl-ui) repository,
and backend services reject forged Hugging Face requests with HTTP `403`.
This flag controls application capabilities; exposing the UI over HTTPS and
routing its local service ports remain deployment/reverse-proxy concerns.

Then open:

* Landing:        http://127.0.0.1:8768/index.html
* Rule → Shape:   http://127.0.0.1:8768/rule.html
* Guide → Shapes: http://127.0.0.1:8768/guide.html

`Ctrl+C` stops the web server and all five services.

---

## Notes

* **Backends & models.** The Inference backend toggle (Databricks / Hugging Face local)
  drives which models appear in each dropdown, split by role: chat/generation and
  embedding (term ranking and RAG indexing). Databricks endpoint names must match
  those deployed in your workspace; if validation fails, pick a model that is
  **Ready** in your Serving tab.
* **First call latency.** The first embedding/generation call is slow (model
  warm-up + Databricks rate throttling). Subsequent calls reuse cached objects;
  the term-ranking service caches the entity embedding matrix per ontology and
  inference configuration. If Databricks token/base URL are missing, semantic
  ranking is not started and the UI shows that model settings must be configured.
* **Guide templates.** The Guide workflow only accepts `.html`, `.htm`, `.md` or
  `.markdown` files that keep the required Business Rules rule-section
  structure. Metadata fields are optional and are not used to decide whether the
  template is valid. PDFs and arbitrary HTML/Markdown guides are rejected before
  generation.
* **Export.** "Export accepted shapes" writes a single `.ttl` combining the
   editable prefix block, the aggregated `sh:NodeShape` block (guide workflow) and
   every accepted shape body. With an Astrea baseline loaded, the final output
   strategy can be selected explicitly:
   * **No merge** exports only the reviewed BR2SHACL shapes. Astrea may still have
     been used as generation evidence.
   * **Priority LLM** keeps BR2SHACL for covered `sh:path` / `sh:targetClass`
     targets and uses Astrea only for targets absent from the generated output.
   * **Restrictive** combines matching shapes and keeps the strongest compatible
     constraints from both sources; incompatible datatype/class choices keep the
     generated value and are reported as merge warnings.
   Merged output is parsed and validated against the generic SHACL2SHACL profile
   plus any domain profiles loaded by the user before it is downloaded.
* **Session import/export.** "Export session" saves the loaded ontology, editable
   prefixes, accepted shapes, Astrea baseline/strategy and non-sensitive model
   settings. It intentionally excludes tokens and workspace URLs.
* The browser-side validation "Check" button calls a real rdflib parse on
  `:9102/validate-shape` — it is not a heuristic.
```
