# SHARD HTTP API

SHARD exposes a versioned REST API under `/api/v1`. Programmatic clients can
run a complete Rule to Shape or Batch to Rules workflow in one JSON request,
or call the lower-level operations used by the interactive interface.

## Discovery

Start SHARD and inspect the API root:

```bash
python run_demo.py
curl http://127.0.0.1:8768/api/v1
```

Interactive Swagger documentation and the machine-readable contract are
available at:

```text
GET /api/v1/docs
GET /api/v1/openapi.json
```

Swagger UI reads the OpenAPI document from the current SHARD deployment and
supports schema exploration and direct requests through `Try it out`. The
contract follows OpenAPI 3.1 and describes request fields, responses and
examples. Its external validator is disabled, so Swagger does not send the API
contract to a third-party validation service. `GET /api/v1/capabilities`
reports deployment policy and available providers without exposing
credentials. `GET /api/v1/health` reports API health.

The page loads the pinned Swagger UI distribution from unpkg with Subresource
Integrity hashes. If those presentation assets are unavailable, the OpenAPI
JSON endpoint remains fully self-hosted and usable by clients and tooling.

## Complete Workflows

These are the recommended endpoints for scripts, notebooks and external
applications:

| Method and route | Response | Purpose |
| --- | --- | --- |
| `POST /api/v1/workflows/rule-to-shape` | JSON | Resolve one rule, generate a grounded shape, validate it, and optionally use Astrea. |
| `POST /api/v1/workflows/guide-to-shapes` | JSON | Process a business-rule batch, consolidate shapes, and optionally use Astrea. The route name is retained for API compatibility. |
| `POST /api/v1/guides/generate` | SSE | Stream Batch to Rules progress events by rule. The route name is retained for API compatibility. |

Both JSON workflows use the same parser, resolver, builder, generic SHACL for
SHACL validation, optional domain profiles, consolidation and merge functions
as the web interface. They do not implement a separate generation path.

### Common request sections

| Section | Required | Description |
| --- | --- | --- |
| `ontology` | Yes | `filename` and complete RDF/OWL `content`. |
| `rule` | Rule workflow | `number`, `title` and required rule `text`. |
| `guide` | Batch workflow | `.md` or `.html` `filename` and complete batch `content`; the field name is retained for API compatibility. |
| `inference` | Yes for real inference | Provider, model ids, temperature and request-scoped credentials. |
| `generation` | No | Domain context, guidance and optional namespace overrides. |
| `resolver` | No | Semantic thresholds, candidate limits, embedding wait behavior and LLM fallback. |
| `validation_profiles` | No | Opt-in domain SHACL for SHACL profiles. The generic profile is always active. |
| `astrea` | No | Astrea use, merge strategy, failure policy or a precomputed baseline. |

The workflow contract is nested for readability. Existing flat application
fields remain accepted for compatibility, but new clients should use the
nested form shown below.

### Rule to Shape

```json
{
  "ontology": {
    "filename": "ontology.ttl",
    "content": "@prefix ex: <http://example.org/domain#> . ..."
  },
  "rule": {
    "number": "BR-001",
    "title": "Asset identifier",
    "text": "Every asset must have exactly one identifier."
  },
  "inference": {
    "provider": "databricks",
    "generation_model": "gemma-3-12b",
    "embedding_model": "qwen3-embedding-0-6b",
    "temperature": 0.2,
    "databricks": {
      "base_url": "https://workspace.example/ai-gateway/mlflow/v1",
      "token": "<databricks-token>"
    }
  },
  "generation": {
    "domain_context": "Assets are maintained at industrial sites.",
    "guidance": "Add a concise sh:message to each constraint."
  },
  "resolver": {
    "semantic_threshold": 0.6,
    "semantic_target_margin": 0.16,
    "semantic_max_targets": 4,
    "top_k": 10,
    "llm_fallback": true,
    "wait_embeddings": true,
    "embedding_timeout": 900
  },
  "validation_profiles": [],
  "astrea": {
    "mode": "none",
    "failure_policy": "continue"
  }
}
```

The response includes the normalized rule, its auditable resolution, the shape
result, unresolved status, namespace decisions, validation summary, Astrea and
merge status, logs, provenance, and `final_shape_document`.

### Batch to Rules

The batch workflow replaces `rule` with the compatibility field `guide`:

```json
{
  "guide": {
    "filename": "business-rules.md",
    "content": "# Business Rules\n\n## Rule\n..."
  }
}
```

Its `generation` response contains every parsed rule, resolution, generated
shape, unresolved rule, consolidation decision and validation summary. The
top-level `final_shape_document` is the consolidated generated document, or
the merged document when Astrea merge is active.

Runnable standard-library clients are provided in:

```text
examples/api/generate_rule.py
examples/api/generate_guide.py
```

For example:

```bash
export DATABRICKS_BASE_URL="https://workspace.example/ai-gateway/mlflow/v1"
export DATABRICKS_TOKEN="..."
export SHARD_LLM_MODEL="gemma-3-12b"
export SHARD_EMBEDDING_MODEL="qwen3-embedding-0-6b"

python examples/api/generate_rule.py \
  --rule "Every asset must have exactly one asset identifier." \
  --output /tmp/asset-shape.ttl

python examples/api/generate_guide.py \
  --output /tmp/asset-shapes.ttl
```

Tokens are read from the environment by these client examples and sent only in
the HTTPS or loopback request body. They are never printed.

## Resolver Options

The calibrated defaults are:

| Field | Default | Meaning |
| --- | --- | --- |
| `semantic_threshold` | `0.60` | Minimum top semantic score. |
| `semantic_target_margin` | `0.16` | Maximum score distance from top-1 for additional semantic targets. |
| `semantic_max_targets` | `4` | Hard limit for semantic targets retained for one rule. |
| `top_k` | `10` | Candidate pool retained for diagnostics and fallback. |
| `llm_fallback` | `true` | Use the generation model when deterministic and semantic signals do not resolve the rule. |
| `wait_embeddings` | `true` | Wait for ontology-term embeddings before resolution. |
| `embedding_timeout` | `900` | Maximum wait in seconds. |

Unresolved rules are returned for review. The workflow never forces a target.

## Validation Profiles

Turtle syntax and the repository's generic SHACL for SHACL profile are always
applied to generated shapes. Domain profiles are opt-in and use this form:

```json
{
  "validation_profiles": [
    {
      "name": "domain-profile.ttl",
      "content": "@prefix sh: <http://www.w3.org/ns/shacl#> . ..."
    }
  ]
}
```

SHARD never infers or automatically selects a domain profile. For example, an
ERA-specific profile is applied only when the client explicitly supplies it.

## Astrea

`astrea.mode` accepts:

| Value | Behavior |
| --- | --- |
| `none` | Do not call or use Astrea. |
| `baseline` | Use Astrea shapes as evidence during generation. |
| `merge` | Merge the final SHARD document with Astrea output. |
| `both` | Use Astrea as evidence and merge the final result. |

Merge techniques are `priority-llm` and `restrictive`. By default,
`failure_policy: continue` makes an unavailable Astrea service explicit in the
response and continues with effective mode `none`. Set `failure_policy: fail`
to abort with `502` or `503`. A caller may avoid the external Astrea call by
supplying `astrea.baseline` with `name` and `content`.

## Operational Endpoints

Fine-grained clients can reproduce individual interactive steps:

| Logical service | Method and route | Transport | Purpose |
| --- | --- | --- | --- |
| Ontology catalog and retrieval | `POST /api/v1/ontology/parse` | JSON | Parse ontology terms, namespaces and prefixes. |
| Ontology catalog and retrieval | `POST /api/v1/ontology/search` | JSON | Rank ontology terms for a rule. |
| Ontology catalog and retrieval | `POST /api/v1/ontology/index` | JSON | Start ontology-term embedding preparation. |
| Ontology catalog and retrieval | `POST /api/v1/ontology/index/status` | JSON | Read embedding preparation state. |
| Ontology catalog and retrieval | `POST /api/v1/ontology/index/cancel` | JSON | Cancel matching embedding jobs. |
| Business rule grounding | `POST /api/v1/rules/resolve-targets` | JSON | Resolve rules without generation. |
| Shape generation | `POST /api/v1/shapes/build` | JSON | Build one shape from an already resolved rule context. |
| Shape assurance and baseline integration | `POST /api/v1/shapes/validate` | JSON | Apply syntax and active profiles. |
| Shape assurance and baseline integration | `POST /api/v1/baselines/astrea` | JSON | Generate an Astrea baseline. |
| Shape assurance and baseline integration | `POST /api/v1/shapes/merge` | JSON | Apply an explicit merge strategy. |
| Model support | `POST /api/v1/models/check` | JSON | Check a configured model endpoint. |
| Model support | `POST /api/v1/models/local/status` | JSON | Check the local snapshot cache without network access. |
| Model support | `POST /api/v1/models/local/download` | SSE | Explicitly download a local snapshot and stream file progress. |

Local model downloads are never triggered by status checks or inference model
factories. The interactive local profile asks for confirmation before calling
the download endpoint. The public profile rejects both local model operations.

The two complete JSON workflows and the streaming batch endpoint belong to the
**Authoring Workflow Service**. They orchestrate the catalog, grounding,
generation and assurance services instead of duplicating their logic. Model
checks and semantic-index lifecycle operations are supporting platform
operations, not additional domain services.

The exact operational schemas are included in OpenAPI where they form a stable
public contract. Internal UI payloads with `additionalProperties` should be
treated as advanced compatibility operations; complete workflows are the
preferred integration surface.

## Errors and Request IDs

Workflow failures use machine-readable `code` values:

| HTTP status | Typical code | Meaning |
| --- | --- | --- |
| `400` | `invalid_request` | Required content or an option is invalid. |
| `403` | `provider_disabled` | The deployment profile forbids the selected provider. |
| `502` | `astrea_response` | Astrea returned unusable data and fail policy is active. |
| `503` | `astrea_unavailable` | Astrea is unavailable and fail policy is active. |
| `504` | `workflow_timeout` | Embedding preparation exceeded its timeout. |
| `500` | `workflow_failed` | Unexpected application failure. |

Clients may send `X-Request-ID`; otherwise SHARD creates one. JSON responses
return it in the `X-Request-ID` header and `request_id` field. SSE events carry
the same value. Canonical responses also send `X-SHARD-API-Version` and
`X-SHARD-Operation`.

## Provenance and Credentials

Canonical JSON responses and SSE events include non-secret `provenance`: API
version, logical service, operation, route, deployment profile, selected model
ids, filenames, profile names and baseline strategy where applicable.

Provenance never copies credentials, inference base URLs, ontology content,
business-rule text or uploaded profile content. Request-scoped workflow logs
returned by the JSON API defensively redact supplied token and API-key values.
Use HTTPS outside loopback. A public deployment should enforce authentication,
request-size limits, rate limiting and TLS at its reverse proxy or API gateway;
SHARD's inference-provider policy is not API-client authentication.

## Compatibility

The original unversioned routes remain API v1 aliases, including
`/parse-ontology`, `/find-relevant-terms`, `/build-shacl-shape`,
`/validate-shape`, `/merge-shapes`, `/generate-from-guide`,
`/resolve-rule-targets`, `/local-model-status` and `/download-local-model`. The
optional split compatibility layout retains the
historical loopback ports. New integrations should use canonical `/api/v1`
routes.
