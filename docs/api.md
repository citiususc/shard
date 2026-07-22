# SHARD REST API

SHARD exposes a strict OpenAPI 3.1 API under `/api/v1`. It turns natural-language
data constraints into ontology-grounded, validated SHACL documents through either
complete workflows or composable lower-level operations.

The paper and architecture call the multi-constraint workflow
**Guide-to-Shapes**. Its API representation is a structured `batch` resource,
so its operations use the Batch-to-Shapes name.

## API discovery

| Resource | URL |
| --- | --- |
| API root | `GET /api/v1` |
| OpenAPI 3.1 | `GET /api/v1/openapi.json` |
| Swagger UI | `GET /api/v1/docs` |
| ReDoc | `GET /api/v1/redoc` |
| Capabilities | `GET /api/v1/capabilities` |
| Health | `GET /api/v1/health` |

The OpenAPI document is generated at runtime from the public Pydantic models.
Swagger and ReDoc therefore describe the same contract enforced by the server.

## Client access, deployment profiles and provider credentials

`local` enables remote inference and server-local Hugging Face inference.
`public` disables server-local model execution and keeps remote inference only.
The active profile and provider capabilities are returned by `/capabilities`.

SHARD does not require client authentication at the API layer in the current
deployment. OpenAPI declares no client security scheme and clients do not send a
SHARD token or API key. A `403` response means that the active deployment profile
has disabled an otherwise valid capability.

For a hosted public deployment, configure remote-provider secrets on the server
with `DATABRICKS_BASE_URL` and `DATABRICKS_TOKEN`. Local programmatic clients may
also send request-scoped credentials in `inference.databricks`. Databricks and
Hugging Face tokens are provider credentials used only for inference requests. They are
password-formatted, write-only string inputs: they are redacted from logs and
never returned in responses, errors, SSE events or provenance. The browser UI
does not expose hosted secrets.

## Quick start

Start SHARD and inspect the contract:

```bash
python run_demo.py
curl http://127.0.0.1:8768/api/v1
curl http://127.0.0.1:8768/api/v1/openapi.json > /tmp/shard-openapi.json
```

The recommended single-rule operation is:

```bash
curl -X POST http://127.0.0.1:8768/api/v1/workflows/rule-to-shape \
  -H 'Content-Type: application/json' \
  -H 'X-Request-ID: books-example' \
  -d '{
    "ontology": {
      "filename": "books.ttl",
      "content": "@prefix ex: <http://example.org/books#> . @prefix owl: <http://www.w3.org/2002/07/owl#> . @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> . ex:Book a owl:Class ; rdfs:label \"Book\" . ex:title a owl:DatatypeProperty ; rdfs:label \"title\" ; rdfs:domain ex:Book ."
    },
    "rule": {
      "number": "BR-BOOK-001",
      "title": "Book title",
      "text": "Every Book must have exactly one title."
    },
    "inference": {
      "provider": "databricks",
      "generation_model": "configured-chat-model",
      "embedding_model": "configured-embedding-model",
      "temperature": 0.2
    },
    "astrea": {"mode": "none"}
  }'
```

## Which endpoint should I use?

| Need | Endpoint | Result |
| --- | --- | --- |
| Complete authoring for one rule | `POST /workflows/rule-to-shape` | One JSON result with grounding, generation, validation and provenance. |
| Guide-to-Shapes as consolidated JSON | `POST /workflows/batch-to-shapes` | One result after all constraints finish. |
| Incremental batch progress | `POST /batches/generate` | Named JSON Server-Sent Events per rule and shape. |
| Inspect ontology terms and namespaces | `POST /ontology/parse` | Typed ontology catalog. |
| Rank terms for retrieval | `POST /ontology/search` | Ranked term candidates; no role assignment. |
| Assign rule roles without generation | `POST /rules/resolve-targets` | Focus nodes, constraint paths and related terms. |
| Generate from an already grounded rule | `POST /shapes/build` | Generated and validated SHACL document. |
| Validate edited or external SHACL | `POST /shapes/validate` | Syntax and active SHACL for SHACL results; no generation. |
| Export reviewed shapes | `POST /shapes/export` | One validated Turtle document with every distinct reviewed constraint preserved and exact redundancies removed. |
| Obtain an ontology baseline from Astrea | `POST /baselines/astrea` | Validated Astrea document. |
| Merge generated and baseline documents | `POST /shapes/merge` | Deterministic merged and validated document. |

All routes in the table are relative to `/api/v1`.

`/ontology/search` is retrieval only; `/rules/resolve-targets` interprets the
rule and assigns ontology terms to stable roles. `/shapes/build` invokes the
generation model and validates its result; `/shapes/validate` validates an
existing document. `/workflows/batch-to-shapes` returns consolidated JSON,
whereas `/batches/generate` streams intermediate events.

`/shapes/export` accepts the reviewed fragments as separate RDF documents. It
does not apply a merge strategy or choose between conflicting constraints. It
preserves every distinct constraint, collapses only structurally identical
anonymous property constraints, removes unreferenced target-only NodeShapes,
serializes one prefix block and validates the final graph. The response includes
an explicit `constraints_preserved` invariant and cleanup statistics; clients
should not publish the result unless that invariant and `valid` are both true.

## Canonical request sections

Complete workflows use closed, nested objects:

| Section | Purpose |
| --- | --- |
| `ontology` | Required `content` and a descriptive `filename`. |
| `rule` or `batch` | One `BusinessRuleInput` or one Markdown/HTML batch document. |
| `inference` | Provider, generation/embedding model ids, temperature and optional credentials. |
| `resolver` | Semantic calibration, candidate limits and constrained LLM fallback. |
| `generation` | Domain context, `generation_guidance`, prefixes and shape namespace choices. |
| `validation` | Optional domain profiles. The generic bundled profile is always active. |
| `astrea` | Baseline usage, merge strategy and failure policy. |

Public objects reject unknown properties. JSON field names are snake_case.
`domain`, `range` and target-role arrays always contain typed term references.
`OntologyTerm.annotations` contains optional ontology annotations outside the
stable catalog fields; former rule-specific catalog placeholders are accepted as
legacy input but are not emitted.

Shape generation uses a three-role model workflow by default. A generator first
authors a complete SHACL document from the grounded rule context. A semantic
critic then returns a closed JSON audit of clause coverage, cardinalities,
object-property ranges, literal alternatives and dependencies. When that audit
finds an issue, a separate corrector applies the report and returns Turtle; the
critic must audit every corrected document again, so the corrector never
self-approves. Turtle parsing, ontology grounding and active SHACL for SHACL
profiles remain deterministic gates after model output. Set
`generation.llm_review` to `false` only when reproducing the single-pass
generator baseline. `generation.review_max_attempts` limits corrector calls from
1 to 5 and defaults to 3. Responses expose only concise issue records and call
counts in `semantic_review`, never private model reasoning.

Target resolution uses a discriminated request. Set `input_type` to `rule` with
`rule`, or to `batch` with `batch`; both forms require `ontology`. There is no
free-form branch.

Resolver scores are strategy-specific diagnostics, not calibrated probabilities.
Responses expose `resolution_score` together with `score_kind` (`explicit`,
`lexical`, `semantic_similarity`, `llm_selected_candidate` or `none`). Compare
scores only when they have the same `score_kind`.

### Public limits

| Setting | Range |
| --- | --- |
| `top_k` | `1..50` in search and resolution |
| `semantic_threshold` | `(0, 1]` |
| `label_threshold`, `strong_label_threshold` | `[0, 1]`; strong must be at least label |
| `semantic_target_margin` | `[0, 1]` and lower than `semantic_threshold` |
| `semantic_max_targets` | `1..20` |
| `embedding_timeout` | `1..86400` seconds |
| `embedding_poll_seconds` | `(0, 60]` seconds |
| `temperature` | `[0, 2]` |
| `max_new_tokens` | `1..16384` |
| job and SSE progress | normalized to `[0, 1]` |

## Operational safeguards

SHARD enforces broad safeguards against accidental memory exhaustion, stalled
providers and clearly abusive request bursts. They are not intended as ordinary
workflow quotas and are configurable through the server environment.

### Request rates

| Environment variable | Default |
| --- | ---: |
| `RATE_LIMIT_REQUESTS_PER_MINUTE` | `1000` |
| `RATE_LIMIT_BURST` | `200` per second |
| `RATE_LIMIT_EXPENSIVE_REQUESTS_PER_MINUTE` | `120` |
| `RATE_LIMIT_JOB_CREATIONS_PER_MINUTE` | `60` |

Limits are process-local and grouped by client IP. Expensive inference operations
and job creation use their dedicated bucket in addition to the general bucket.
`X-Forwarded-For` is honored only when the immediate peer appears in the
comma-separated `SHARD_TRUSTED_PROXY_IPS` list. A rejected request returns `429`,
the canonical `ApiError`, and a `Retry-After` response header.

### Timeouts

| Environment variable | Default |
| --- | ---: |
| `HTTP_CONNECT_TIMEOUT_SECONDS` | `60` |
| `HTTP_READ_TIMEOUT_SECONDS` | `1800` |
| `MODEL_TIMEOUT_SECONDS` | `1800` |
| `EMBEDDING_TIMEOUT_SECONDS` | `3600` |
| `ASTREA_TIMEOUT_SECONDS` | `1800` |
| `BATCH_WORKFLOW_TIMEOUT_SECONDS` | `7200` |
| `SSE_IDLE_TIMEOUT_SECONDS` | `1800` |
| `JOB_MAX_RUNTIME_SECONDS` | `7200` |

External timeouts produce `504` where response headers have not yet been sent.
After an SSE stream opens, an idle timeout produces a terminal `failed` event.
Jobs that exceed their runtime become `failed` with `JOB_RUNTIME_TIMEOUT` and
request cooperative cancellation.

### Upload and concurrency limits

| Environment variable | Default |
| --- | ---: |
| `MAX_REQUEST_BODY_MB` | `256` |
| `MAX_ONTOLOGY_UPLOAD_MB` | `200` |
| `MAX_BATCH_UPLOAD_MB` | `50` |
| `MAX_VALIDATION_PROFILE_MB` | `20` |
| `MAX_SHAPE_DOCUMENT_MB` | `50` |
| `MAX_CONCURRENT_JOBS` | `50` |
| `MAX_CONCURRENT_BATCH_WORKFLOWS` | `20` |
| `MAX_CONCURRENT_MODEL_DOWNLOADS` | `5` |
| `MAX_QUEUED_JOBS` | `500` |

The generic request limit deliberately leaves JSON overhead above the largest
ontology resource. Oversized input returns `413`. Asynchronous work is queued;
`503 DEPLOYMENT_CAPACITY_EXHAUSTED` is returned only after both active and queued
capacity are full. A public reverse proxy must configure a request-body limit at
least as large as the SHARD limits it is expected to pass.

### CORS

`SHARD_CORS_ALLOWED_ORIGINS` is a comma-separated allowlist. Defaults permit only
`http://127.0.0.1:8768` and `http://localhost:8768`; exact same-origin requests
are also accepted. Public deployments must set the real frontend origin.
SHARD does not enable credentialed wildcard CORS and does not advertise an
`Authorization` request header.

## Validation

Every generated or explicitly validated document is checked at two levels:

1. Turtle/RDF syntax.
2. The bundled generic `shacl-shacl.ttl`, plus any opt-in domain profiles in
   `validation.profiles`.

SHARD never infers a domain profile. An ERA profile, for example, is applied
only when the caller supplies it.

```json
{
  "shape_document": "@prefix sh: <http://www.w3.org/ns/shacl#> . <urn:Shape> a sh:NodeShape .",
  "validation": {
    "profiles": [
      {"name": "domain-profile.ttl", "content": "...Turtle profile..."}
    ]
  }
}
```

## Asynchronous jobs

Ontology indexing and local-model downloads expose stable job resources:

```text
POST   /api/v1/ontology/indexes
GET    /api/v1/ontology/indexes/{job_id}
DELETE /api/v1/ontology/indexes/{job_id}

POST   /api/v1/models/local/downloads
GET    /api/v1/models/local/downloads/{job_id}
DELETE /api/v1/models/local/downloads/{job_id}
```

Creation returns `202 Accepted`. A job contains `job_id`, `status` (`queued`,
`running`, `completed`, `failed` or `cancelled`), normalized `progress`, a
message, timestamps and an optional typed error. Cancelling a terminal job
returns `409`; an unknown job returns `404`. Jobs are process-local and are not
durable across a SHARD restart.

## Server-Sent Events

`POST /api/v1/batches/generate` uses named SSE frames. Every `data:` value is
JSON conforming to one branch of the discriminated `SseEvent` union.

```text
id: 3
event: rule_resolved
data: {"event":"rule_resolved","request_id":"...","sequence":3,"timestamp":"...","rule":{"number":"BR-BOOK-001","title":"Book title","text":"Every Book must have exactly one title."},"target_roles":{"focus_nodes":[{"iri":"ex:Book"}],"constraint_paths":[{"iri":"ex:title"}],"related_terms":[]},"resolved_by":"label","resolution_score":0.91,"score_kind":"lexical","operation_metadata":{...},"provenance":{...},"extensions":{}}
```

Events are `started`, `progress`, `rule_resolved`, `shape_generated`,
`validation_completed`, `warning`, `completed`, `failed` and `heartbeat`.
Each event type has its own required fields. Progress uses `completed_items`,
`total_items` and normalized `progress`; batch events also expose rule counters.
Resolution events carry the complete constraint and target roles. Failed events
carry an `ApiError`. `completed` and `failed` are terminal.

Validation and parsing failures before response headers produce a normal JSON
`ApiError`. Failures after the stream opens produce a terminal `failed` event.
A heartbeat is emitted every 15 seconds while a canonical stream is idle.
Heartbeats preserve the connection but do not count as work progress; 30 minutes
without a non-heartbeat event triggers the configurable SSE idle timeout.
Client disconnection stops event delivery; current batch computation may finish
server-side. Streams do not replay events and `Last-Event-ID` is not supported,
so reconnecting starts a new operation. Use job endpoints when cancellable,
pollable work is required.

## Astrea and merge

Canonical Astrea modes are:

| Mode | Effect |
| --- | --- |
| `none` | Do not use Astrea. |
| `evidence` | Use relevant Astrea shapes as generation evidence. |
| `merge` | Merge the structurally matching Astrea fragment into each generated rule shape before human review. |
| `evidence-and-merge` | Use the matching fragment as evidence and merge it before review. |

`baseline` and `both` are accepted deprecated input aliases. The canonical
merge strategies are `generated-priority` and `restrictive`; `priority-llm` is
an accepted deprecated alias for `generated-priority`.

`generated-priority` retains generated constraints for covered targets and
uses baseline shapes for uncovered targets. `restrictive` combines compatible
cardinalities, lengths and numeric bounds, and handles datatype, class and
enumeration constraints deterministically. Logical-list conflicts and
inconsistent intervals produce structured warnings. Generated messages and
metadata remain authoritative where a deterministic combination would change
their meaning. Namespace bindings from both RDF graphs are retained where they
do not conflict.

Workflow merges are scoped by exact resolved `focus_nodes` and
`constraint_paths`; unrelated Astrea NodeShapes and sibling property paths are
excluded. Each merged rule shape is validated before it is returned to the UI
or consolidated into a batch. The standalone `/shapes/merge` operation remains
available for clients that intentionally need to merge two complete SHACL
documents.

Call `/baselines/astrea` to invoke the external service. Supplying
`astrea.baseline` in a workflow or `baseline` in `/shapes/merge` uses a
client-provided document and does not call Astrea.

## Errors

Every canonical error has one shape:

```json
{
  "error": "request_validation_failed",
  "code": "REQUEST_SCHEMA_VALIDATION_FAILED",
  "message": "Request body validation failed.",
  "request_id": "books-example",
  "details": {
    "issues": [
      {"location": ["rule", "text"], "message": "Field required", "type": "missing"}
    ]
  }
}
```

| Status | Meaning |
| --- | --- |
| `400` | Malformed JSON or inconsistent domain input. |
| `401` | An inference provider rejected its provider credential. SHARD itself does not authenticate API clients. |
| `403` | Capability disabled by deployment profile. |
| `404` | Resource, route or job not found. |
| `409` | Conflicting job state. |
| `413` | Request body or uploaded resource exceeds its configured size limit. |
| `422` | Request does not conform to its public schema. |
| `429` | Upstream or deployment rate limit. |
| `500` | Unexpected internal failure. |
| `502` | Invalid upstream response. |
| `503` | Provider/model unavailable or deliberately broad processing capacity exhausted. |
| `504` | Upstream timeout. |

No error response contains stack traces, provider tokens or request secrets.

## Operational metadata and authoring provenance

Every canonical JSON response carries `OperationMetadata`: request id, operation,
logical service, API version, deployment profile, creation time, duration and
operational warnings. Root, health, capabilities, ontology parsing, model checks
and jobs carry only this metadata.

Authoring responses additionally carry `AuthoringProvenance`: source constraint
when applicable, selected targets and roles, resolution strategy and score kind,
resolver evidence, model ids, non-secret generation parameters, validation
results, baseline use, merge strategy, warnings and errors. It never contains
tokens, provider base URLs, ontology documents or profile contents.

## Compatibility policy

New integrations must use canonical `/api/v1` routes and nested snake_case
payloads. No deprecated versioned operations are published. Both unified and
split layouts use `POST /api/v1/ontology/indexes` and
`POST /api/v1/models/local/downloads`, followed by the corresponding job URL.
Unversioned service paths remain internal transport adapters only for domain
operations that are actively consumed by the optional split layout.

Deprecated input aliases are normalized but never emitted. Removing a
deprecated alias or changing a canonical response incompatibly requires a new
major API version. Additive optional fields may be introduced within v1.

## Contract inspection

The OpenAPI document is generated dynamically; there is no checked-in generated
JSON file. Restart SHARD after changing public models, then refresh Swagger or
ReDoc. To retain a formatted snapshot for client generation or external
validation:

```bash
curl http://127.0.0.1:8768/api/v1/openapi.json > /tmp/shard-openapi.json
python -m json.tool /tmp/shard-openapi.json > /tmp/shard-openapi-formatted.json
```

Swagger UI at `/api/v1/docs` is intended for interactive requests. ReDoc at
`/api/v1/redoc` is the readable contract reference. The JSON document remains
the authoritative OpenAPI 3.1 artifact.
