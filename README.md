# SHARD

**Interactive Ontology-Grounded SHACL Authoring from Natural-Language Data Constraints**

SHARD is an interactive workbench for generating reviewable SHACL shapes from
an OWL/RDF ontology and natural-language data constraints. It keeps a human in
the loop: ontology grounding can be reviewed, generated Turtle can be edited,
and every accepted shape can be validated and exported.

Repository: <https://github.com/citiususc/shard>

## Authoring Workflows

### Rule to Shape

Write one data constraint and resolve its ontology context into focus nodes,
constrained property paths and related terms. The proposed roles remain
editable before SHARD generates one grounded SHACL document.

### Batch to Shapes

Upload a structured Markdown or HTML batch. SHARD parses each data constraint,
resolves it through the `label -> semantic -> LLM` cascade, generates one
document per resolved constraint and consolidates compatible property
constraints under their target-class NodeShapes. Progress is streamed per data
constraint. Unresolved constraints and discarded candidates remain visible for
human review.

## Validation and Baselines

Generated and imported shapes pass through:

1. Turtle/RDF syntax validation.
2. Generic SHACL for SHACL validation using the bundled `shacl-shacl.ttl`.
3. Optional user-supplied domain validation profiles.
4. Ontology-grounding checks for SHACL target, path and class IRIs.

Domain profiles are always opt-in and are never inferred from the ontology.

SHARD can send the current ontology to the Astrea REST API and use its response
as structural generation evidence, as a rule-focused merge input, or both.
Merging takes place before human review and is limited to the resolved focus
nodes and constrained paths. Export serializes the accepted shapes exactly as
reviewed, removes only structurally identical anonymous constraints and checks
that no distinct constraint was lost.

The Astrea endpoint defaults to
`https://astrea.linkeddata.es/api/shacl/document`. Set
`SHARD_ASTREA_API_URL` to use another deployment.

## Requirements

- Python 3.10 or newer.
- A remote inference endpoint, or enough local resources to run a compatible
  Hugging Face model.
- Node.js is not required at runtime; the frontend is served as static assets.

## Installation

Clone the repository and install every production backend:

```bash
git clone https://github.com/citiususc/shard.git
cd shard
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

`requirements.txt` installs the project with its `local` extra. A remote-only
installation can omit the large local-model dependencies:

```bash
python -m pip install .
```

## Running SHARD

From a source checkout:

```bash
python run_demo.py
```

The installed command is equivalent:

```bash
shard
```

Open <http://127.0.0.1:8768/>. The default `unified` layout serves the UI, REST
API and SSE endpoint from the same process. Loopback listeners on ports
`9100`-`9104` preserve API v1 compatibility and are not intended to be exposed.

SHARD loads the first `.env` found in the current directory or project root.
Existing process variables take precedence. Copy `.env.example` when a local
configuration file is useful, never commit `.env`, and never place provider
credentials in frontend files.

### Remote inference

Configure the remote endpoint in the process environment or `.env`:

```bash
DATABRICKS_BASE_URL=https://workspace.example/ai-gateway/mlflow/v1
DATABRICKS_TOKEN=replace-with-a-secret
```

The public deployment profile obtains these values from the server and never
returns them to the browser. Local API clients may supply request-scoped
provider credentials where the deployment permits it. Tokens are write-only,
redacted from logs and excluded from sessions and provenance.

### Local inference

The local profile permits Hugging Face models running on the same machine:

```bash
shard --deployment-profile local
```

No model is selected or downloaded automatically. SHARD checks the local cache
when the user chooses a model and asks for confirmation before downloading a
missing snapshot.

## Public Deployment

Use the public profile to disable server-side local-model execution:

```bash
shard --deployment-profile public --host 127.0.0.1 --port 8000
```

Place this listener behind HTTPS and a reverse proxy. Only the unified listener
should be exposed. The frontend uses relative assets and the relative API base
`api/v1/`, so a proxy can publish the application below `/shard/` while
stripping that prefix before forwarding requests. See
[Deployment Profiles](docs/deployment.md) for environment settings, proxy and
SSE requirements.

## Preloaded ePO Examples

The Import session menu exposes two complete examples based on the public
eProcurement Ontology dataset:

- One data constraint for Rule to Shape.
- A batch of ten representative data constraints for Batch to Shapes.

The sessions, extensible manifest and license attribution live in
[`frontend/examples/`](frontend/examples/README.md). ePO is demonstration data;
no ePO-specific behavior exists in the SHARD core.

## Architecture

SHARD exposes one versioned application API and five logical capability
boundaries:

1. Ontology Catalog and Retrieval Service.
2. Data Constraint Grounding Service.
3. Shape Generation Service.
4. Shape Assurance and Baseline Integration Service.
5. Authoring Workflow Service.

These are scientific and API responsibilities, not a requirement to deploy
five operating-system services. See [Architecture](docs/architecture.md).

```text
src/shard/
  domain/          data-constraint and ontology concepts
  application/     grounding, generation, validation and orchestration
  inference/       remote and local inference adapters
  baselines/       Astrea evidence and RDF-aware merge strategies
  api/             OpenAPI, JSON, job and SSE transport adapters
  deployment/      deployment policy and operational safeguards
  observability/   request-scoped, secret-safe logging
  resources/       generic prompts and validation resources
frontend/          static application and preloaded ePO sessions
docs/              architecture, API and deployment documentation
```

## REST API

The canonical API is available under `/api/v1`. Recommended complete workflows:

- `POST /api/v1/workflows/rule-to-shape`
- `POST /api/v1/workflows/batch-to-shapes`
- `POST /api/v1/batches/generate` for incremental SSE progress

Composable operations cover ontology parsing and search, target resolution,
shape generation, validation, lossless export, Astrea and merge. Discovery and
documentation are available at:

- `GET /api/v1`
- `GET /api/v1/openapi.json`
- `GET /api/v1/docs` for Swagger UI
- `GET /api/v1/redoc` for ReDoc
- `GET /api/v1/capabilities`
- `GET /api/v1/health`

SHARD does not authenticate API clients itself. Provider credentials authorize
inference providers, not the SHARD API. Deployments that require client access
control must enforce it at the reverse proxy or platform boundary.

Canonical routes use typed snake_case payloads and secret-free provenance.
Unversioned compatibility routes, the `BR2SHACL_*` environment aliases and
`X-BR2SHACL-*` response headers remain available during API v1 for existing
clients. New integrations should use `/api/v1`, `SHARD_*` and `X-SHARD-*`.

See [REST API](docs/api.md) for endpoint selection, schemas, jobs, SSE, errors
and executable `curl` examples.

## Citation and License

The software citation is provided in [`CITATION.cff`](CITATION.cff). SHARD is
released under the [MIT License](LICENSE).
