# Architecture

SHARD is described as five logical services. These boundaries represent
user-visible responsibilities; they do not require one operating-system process
or one TCP port per service.

1. **Ontology Catalog and Retrieval Service** parses the uploaded ontology,
   produces the shared term catalog, retrieves relevant terms and manages the
   optional semantic index used for ranking.
2. **Business Rule Grounding Service** maps each business rule to focus nodes,
   constrained property paths and related ontology terms through the auditable
   label, semantic and constrained-LLM cascade. It does not generate SHACL.
3. **Shape Generation Service** generates one grounded constraint document
   from a business rule and its reviewed ontology context.
4. **Shape Assurance and Baseline Integration Service** applies syntax and
   SHACL for SHACL validation, obtains optional ontology baselines and performs
   user-selected merge strategies.
5. **Authoring Workflow Service** orchestrates both Rule to Shape and Batch to
   Rules across parsing, grounding, generation, validation and consolidation.

The model availability check and ontology-index lifecycle operations are
auxiliary endpoints. They support the five capabilities but are not presented
as independent scientific contributions or standalone services. Deployment
capabilities and health are platform endpoints.

## Dependency direction

The authoring workflows compose the parser, resolver, shared rule-context
builder and validation functions in process. Core modules do not call the
browser or make HTTP requests to sibling services. The HTTP layer is therefore
a transport adapter around reusable Python functions rather than the owner of
domain logic.

```text
Authoring Workflow Service
  -> Ontology Catalog and Retrieval Service
  -> Business Rule Grounding Service
       -> Ontology semantic index (optional)
  -> Shape Generation Service
  -> Shape Assurance and Baseline Integration Service
```

Rule to Shape and Batch to Rules call the same ontology, grounding,
generation and assurance capabilities. The batch workflow additionally
consolidates per-rule results and can expose progress through SSE.

`src/shard/application/workflows.py` exposes the same compositions to
programmatic clients as one-call Rule to Shape and Batch to Rules use cases.
It normalizes the public nested request contract and delegates every domain
step to the existing application functions; it is not a parallel generator.

## API contract

The versioned contract is defined in `src/shard/api/contract.py`. It records
the canonical route, compatibility alias, owning logical service, transport and
whether an operation is primary, auxiliary or system-level. Keeping this catalog
in code makes it possible to test API documentation and routing for drift.
`src/shard/api/openapi.py` derives the canonical path catalog from that metadata
and adds the stable OpenAPI 3.1 schemas used by external workflow clients.
`src/shard/api/swagger_ui.py` renders those schemas as interactive Swagger
documentation without maintaining a second route description.

Compatibility endpoint names remain aliases implemented by
`src/shard/api/compat.py`. Port allocation and process layout are deployment
details and are intentionally absent from the logical service model.

## Source layers

The `shard.domain` package owns business-rule and ontology concepts.
`shard.application` owns use cases and depends on domain, inference, baselines
and observability. `shard.api` translates HTTP and SSE requests into those use
cases. Application modules never import request handlers or sibling network
services.
