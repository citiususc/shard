# SHARD

**An Interactive Workbench for Ontology-Grounded SHACL Authoring from Data Constraints**

SHARD is a local and web application for generating reviewable SHACL shapes
from an OWL/RDF ontology and data constraints written in natural language. It
keeps a human in the loop: generated Turtle can be inspected, edited,
validated, accepted and exported.

## Workflows

### Rule to Shape

Write one data constraint and resolve its ontology context into focus nodes,
constrained property paths and related terms. The proposed roles can be
reviewed manually before SHARD generates one grounded constraint document.

### Batch to Shapes

Upload a structured Markdown or HTML batch of data constraints. SHARD parses each
rule, resolves it to role-grouped ontology terms through the `label -> semantic -> LLM`
cascade, generates one coherent constraint document per resolved rule, and consolidates
compatible property constraints under target-class NodeShapes. Progress is
streamed per data constraint.

Rules that cannot be resolved and generated outputs that fail validation remain
visible for review; SHARD does not force or silently discard them.

## Assurance

Every generated shape is checked at three complementary boundaries:

1. Turtle/RDF syntax.
2. Generic SHACL for SHACL validation with the bundled W3C `shacl-shacl.ttl`.
3. Optional user-supplied domain profiles.

Domain profiles are never inferred from an ontology. For example, the ERA
profile under `profiles/era/` is opt-in and must not be applied to unrelated
domains.

SHARD also checks key SHACL IRIs against the uploaded ontology. When enabled,
SHARD sends the ontology content to the Astrea REST API and uses the returned
SHACL document as rule-focused structural evidence, as a final merge input,
or both. Reviewed output can be exported with generated-shape priority or with
the restrictive RDF-aware merge strategy. If Astrea is unavailable, the UI
falls back to no Astrea use and keeps the SHARD workflow available.

The Astrea endpoint defaults to `https://astrea.linkeddata.es/api/shacl/document`.
Self-hosted deployments can set `SHARD_ASTREA_API_URL`; the request timeout can
be changed with `SHARD_ASTREA_TIMEOUT` (seconds).

## Run Locally

Python 3.10 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python run_demo.py
```

Open <http://127.0.0.1:8768/>.

The UI exposes neutral remote/local inference choices and model settings. A
remote deployment reads its endpoint and credentials from
`DATABRICKS_BASE_URL` and `DATABRICKS_TOKEN`; these secrets are not displayed or
stored in the browser. Programmatic API clients may still supply request-scoped
credentials. On startup, SHARD loads the first `.env` file found in the current
working directory or project root. Existing process variables take precedence,
followed by `.env` values and then built-in defaults; explicit command-line
arguments override the resulting deployment settings. Start from `.env.example`
for a public deployment and never commit the resulting `.env` file.

The package can also be installed in editable mode:

```bash
python -m pip install -e .
shard
```

### Local inference

Install the optional local dependencies:

```bash
python -m pip install -r requirements-local.txt
# equivalent after packaging:
python -m pip install -e '.[local]'
```

No model is selected or downloaded automatically. When a model is selected in
the local UI, SHARD checks the local cache and asks for confirmation before an
explicit download, whose progress is shown in the model panel. A small backend
smoke test is available at:

```bash
python scripts/smoke_huggingface.py
```

### Public deployment policy

The public profile prevents server-side local-model execution while retaining
remote inference:

```bash
python run_demo.py --deployment-profile public --host 0.0.0.0
```

This is an application policy, not a complete production perimeter. Put public
deployments behind HTTPS, explicit network policy and a reverse proxy. SHARD does
not authenticate API clients; provider credentials are used only for inference.
Expose the unified web port only.

## Examples

Two domains are included:

- `examples/asset-maintenance/`: general non-ERA ontology, rules, context and a
  sample validation profile.
- `examples/era-rinf/`: compact ERA/RINF ontology and Data Constraints fixtures.

The generic SHACL for SHACL profile is packaged at
`src/shard/resources/validation/shacl-shacl.ttl`. The ERA-specific profile is
kept separately at `profiles/era/era-shacl-shacl.ttl`.

## Architecture

SHARD exposes one versioned application API and models five logical capability
boundaries: ontology catalog and retrieval, data constraint grounding, shape
generation, shape assurance and baseline integration, and authoring workflow
orchestration. These are scientific and API responsibilities, not a requirement
to deploy five operating-system services.

The default runtime uses one process and one same-origin API. Loopback listeners
on ports `9100` through `9104` preserve the former endpoint paths for local
compatibility. The optional `split` layout runs those adapters as separate
processes for comparative experiments; all routes still call the same
application functions.

See [architecture](docs/architecture.md), [HTTP API](docs/api.md), and
[deployment](docs/deployment.md).

## Repository Layout

```text
src/shard/
  domain/          data-constraint and ontology concepts
  application/     resolution, generation, validation and orchestration
  inference/       Databricks and local Hugging Face adapters
  baselines/       Astrea parsing, evidence selection and merge strategies
  api/             versioned contract and HTTP/SSE adapters
  deployment/      hosted/local capability policy
  observability/   request-scoped execution logging
  resources/       generic prompts and validation resources
frontend/          static HTML, modular JavaScript and CSS
profiles/          opt-in domain validation profiles
examples/          runnable domain fixtures
experiments/       reproducible research diagnostics
scripts/           operational and smoke-test scripts
tests/             unit and integration tests
docs/              architecture, API and deployment documentation
```

The Python package uses a `src/` layout so imports always resolve through the
installed `shard` package instead of depending on the current directory.

## API

The canonical API is under `/api/v1`. Complete workflows for external clients
are available as single JSON requests:

- `POST /api/v1/workflows/rule-to-shape`
- `POST /api/v1/workflows/batch-to-shapes`
- `GET /api/v1/redoc` (ReDoc)

The batch resource implements the Guide-to-Shapes workflow described in the
system architecture and paper.

The existing SSE and fine-grained operations remain available:

- `POST /api/v1/ontology/parse`
- `POST /api/v1/ontology/search`
- `POST /api/v1/rules/resolve-targets`
- `POST /api/v1/shapes/build`
- `POST /api/v1/shapes/validate`
- `POST /api/v1/baselines/astrea`
- `POST /api/v1/shapes/merge`
- `POST /api/v1/models/local/status`
- `POST /api/v1/models/local/downloads` (create a pollable job)
- `POST /api/v1/batches/generate` (SSE)
- `GET /api/v1`
- `GET /api/v1/docs` (Swagger UI)
- `GET /api/v1/openapi.json`
- `GET /api/v1/capabilities`
- `GET /api/v1/health`

SHARD does not require a client token or API key. Ontology indexing and local
model downloads use the canonical asynchronous job resources shown above in
both unified and split service layouts.

Canonical responses expose typed operation metadata; authoring operations also
expose secret-free authoring provenance. `X-SHARD-*` headers identify the API
operation. Pre-rename `X-BR2SHACL-*` headers and environment aliases remain
accepted during API v1 for compatibility.

See the [API documentation](docs/api.md), the live Swagger UI and OpenAPI 3.1
document, and the standard-library clients in [`examples/api`](examples/api).

## Target-Resolution Experiment

The deterministic diagnostic requires no external credentials:

```bash
python experiments/target_resolution/evaluate.py --mode injected --case all
```

For real Databricks measurement, export explicit variables and select real
mode. Canonical names are `SHARD_PROVIDER`, `SHARD_EMBEDDING_MODEL`, and
`SHARD_LLM_MODEL`; the former `BR2SHACL_*` names remain aliases. Databricks
credentials use `DATABRICKS_BASE_URL` and `DATABRICKS_TOKEN`. Tokens are never
printed by the diagnostic.

## Tests

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
for file in frontend/js/*.js; do node --check "$file"; done
git diff --check
```

## Citation and License

Citation metadata is provided in [`CITATION.cff`](CITATION.cff). SHARD is
released under the [MIT License](LICENSE).
