"""Generate the strict OpenAPI 3.1 contract for the versioned SHARD API."""

from __future__ import annotations

from copy import deepcopy
from http import HTTPStatus
import json
from typing import Any, Dict, Optional

from pydantic.json_schema import models_json_schema

from shard import __description__, __title__, __version__
from shard.api.contract import (
    API_PREFIX,
    API_VERSION,
    ENDPOINTS,
    LOGICAL_SERVICES,
    api_catalog,
    frontend_endpoint_map,
)
from shard.api.models import PUBLIC_MODELS, REQUEST_MODELS, RESPONSE_MODELS
from shard.api.provenance import is_authoring_operation
from shard.api.operational import RATE_LIMITED_OPERATIONS, operational_settings
from shard.deployment.policy import PROJECT_REPOSITORY_URL, capabilities


SCHEMA_REF = "#/components/schemas/"

BOOK_ONTOLOGY = """@prefix ex: <http://example.org/books#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

ex:Book a owl:Class ; rdfs:label "Book" .
ex:title a owl:DatatypeProperty ;
    rdfs:label "title" ;
    rdfs:domain ex:Book ;
    rdfs:range xsd:string .
"""

BOOK_SHAPE = """@prefix ex: <http://example.org/books#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix shape: <http://example.org/books/shapes/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

shape:BookShape a sh:NodeShape ;
    sh:targetClass ex:Book ;
    sh:property [
        sh:path ex:title ;
        sh:minCount 1 ;
        sh:maxCount 1 ;
        sh:datatype xsd:string
    ] .
"""

BOOK_RULE = {
    "number": "BR-BOOK-001",
    "title": "Book title",
    "text": "Every Book must have exactly one title.",
}

BOOK_BATCH = """# Data Constraints

## Rule

- Number: BR-BOOK-001
- Title: Book title

### Data constraint

Every Book must have exactly one title.
"""


def _schema_ref(name: str) -> Dict[str, str]:
    return {"$ref": f"{SCHEMA_REF}{name}"}


def _component_schemas() -> Dict[str, Any]:
    """Generate shared schemas from the runtime Pydantic models."""
    _, document = models_json_schema(
        [(model, "validation") for model in PUBLIC_MODELS],
        ref_template=f"{SCHEMA_REF}{{model}}",
    )
    schemas = deepcopy(document.get("$defs", {}))
    if "JsonValue" in schemas:
        schemas["JsonValue"] = {
            "title": "JsonValue",
            "description": "Recursive JSON value permitted only inside metadata or extensions.",
            "oneOf": [
                {"type": "string"},
                {"type": "number"},
                {"type": "boolean"},
                {"type": "null"},
                {"type": "array", "items": _schema_ref("JsonValue")},
                {
                    "type": "object",
                    "additionalProperties": _schema_ref("JsonValue"),
                },
            ],
        }
    schemas["OpenApiDocument"] = {
        "type": "object",
        "required": ["openapi", "info", "paths", "components"],
        "properties": {
            "openapi": {"const": "3.1.0"},
            "info": {"type": "object"},
            "jsonSchemaDialect": {"type": "string", "format": "uri"},
            "servers": {"type": "array", "items": {"type": "object"}},
            "externalDocs": {"type": "object"},
            "tags": {"type": "array", "items": {"type": "object"}},
            "paths": {"type": "object"},
            "components": {"type": "object"},
            "x-shard-api-version": {"type": "string"},
            "x-shard-api-prefix": {"type": "string"},
        },
        "additionalProperties": False,
    }
    return schemas


def _book_common() -> Dict[str, Any]:
    return {
        "ontology": {"filename": "books.ttl", "content": BOOK_ONTOLOGY},
        "inference": {
            "provider": "databricks",
            "generation_model": "configured-chat-model",
            "embedding_model": "configured-embedding-model",
            "temperature": 0.2,
        },
        "resolver": {"semantic_threshold": 0.60, "llm_fallback": True},
        "astrea": {"mode": "none", "merge_strategy": "generated-priority"},
    }


def request_example(operation: str) -> Optional[Dict[str, Any]]:
    """Return an executable books-domain request example for one operation."""
    common = _book_common()
    term_class = {
        "id": "class-0", "iri": "ex:Book", "full_iri": "http://example.org/books#Book",
        "label": "Book", "type": "class", "kind": "Class",
        "domain": [{"iri": "ex:Book", "label": "Book"}], "range": [],
        "superclasses": [], "comment": "", "ontology_note": "",
        "annotations": {},
    }
    term_title = {
        "id": "property-0", "iri": "ex:title", "full_iri": "http://example.org/books#title",
        "label": "title", "type": "property", "kind": "DatatypeProperty",
        "domain": [{"iri": "ex:Book", "label": "Book"}],
        "range": [{"iri": "xsd:string", "label": "string"}],
        "superclasses": [], "comment": "", "ontology_note": "",
        "annotations": {},
    }
    examples = {
        "workflows.rule.generate": {**common, "rule": BOOK_RULE},
        "workflows.batch.generate": {
            **common,
            "batch": {"filename": "book-rules.md", "content": BOOK_BATCH, "format": "md"},
        },
        "ontology.parse": {"ontology": common["ontology"]},
        "ontology.search": {
            "rule": BOOK_RULE,
            "ontology_terms": [term_class, term_title],
            "top_k": 5,
            "inference": common["inference"],
        },
        "ontology.index.create": {
            "ontology_terms": [term_class, term_title],
            "inference": common["inference"],
        },
        "rules.resolve-targets": {
            "input_type": "rule",
            "ontology": common["ontology"],
            "rule": BOOK_RULE,
            "inference": common["inference"],
            "resolver": common["resolver"],
        },
        "shapes.build": {
            "ontology": common["ontology"],
            "rule": BOOK_RULE,
            "target_roles": {
                "focus_nodes": [{"iri": "ex:Book", "label": "Book"}],
                "constraint_paths": [{"iri": "ex:title", "label": "title"}],
                "related_terms": [{"iri": "xsd:string", "label": "string"}],
            },
            "inference": common["inference"],
        },
        "shapes.validate": {"shape_document": BOOK_SHAPE},
        "baselines.astrea.generate": {"ontology": common["ontology"]},
        "shapes.merge": {
            "generated": {"name": "generated.ttl", "content": BOOK_SHAPE},
            "baseline": {"name": "astrea.ttl", "content": BOOK_SHAPE},
            "merge_strategy": "generated-priority",
        },
        "batches.generate": {
            **common,
            "batch": {"filename": "book-rules.md", "content": BOOK_BATCH, "format": "md"},
        },
        "models.check": {
            "inference_provider": "databricks",
            "model_id": "configured-chat-model",
            "role": "chat",
        },
        "models.local.status": {"model_id": "organisation/tiny-model"},
        "models.local.download.create": {"model_id": "organisation/tiny-model"},
    }
    return deepcopy(examples.get(operation))


def _response_example(operation: str, status: int) -> Optional[Dict[str, Any]]:
    envelope = {
        "request_id": "req-books-001",
        "operation_metadata": _operation_metadata_example(operation),
    }
    if is_authoring_operation(operation):
        envelope["provenance"] = _authoring_provenance_example(operation)
    if status >= 400:
        return _error_example(operation, status)
    valid_result = {
        "valid": True,
        "syntax_valid": True,
        "profile_valid": True,
        "profile_count": 1,
        "profile_names": ["shacl-shacl.ttl"],
        "generic_profile_active": True,
        "generic_profile_name": "shacl-shacl.ttl",
        "domain_profile_count": 0,
        "domain_profile_names": [],
        "validation_level": "syntax+generic",
        "error_type": "none",
        "report_text": "Conforms: True",
        "message": "Valid Turtle and generic SHACL for SHACL.",
    }
    book_ref = {"iri": "ex:Book", "label": "Book"}
    title_ref = {"iri": "ex:title", "label": "title"}
    string_ref = {"iri": "xsd:string", "label": "string"}
    roles = {
        "focus_nodes": [book_ref],
        "constraint_paths": [title_ref],
        "related_terms": [string_ref],
    }
    resolution = {
        "rule": BOOK_RULE,
        "selected_targets": [book_ref, title_ref],
        "target_roles": roles,
        "resolved_by": "label",
        "resolution_score": 0.91,
        "score_kind": "lexical",
    }
    astrea_status = {
        "requested_mode": "none",
        "effective_mode": "none",
        "failure_policy": "continue",
        "message": "Astrea was not requested.",
    }
    summary = {
        "rules_total": 1,
        "rules_unresolved": 0,
        "targets_total": 2,
        "generated_total": 1,
        "valid": 1,
        "invalid": 0,
    }
    shape_outcome = {
        "rule_number": BOOK_RULE["number"],
        "rule_title": BOOK_RULE["title"],
        "selected_targets": [book_ref, title_ref],
        "target_roles": roles,
        "shape_document": BOOK_SHAPE,
        "valid": True,
        "attempts": 1,
        "error_type": "none",
        "message": "Valid SHACL generated on attempt 1.",
        "validation": valid_result,
    }
    examples = {
        "system.root": {
            **envelope,
            "name": __title__,
            "description": __description__,
            "version": __version__,
            "api_version": API_VERSION,
            "docs": f"{API_PREFIX}/docs",
            "redoc": f"{API_PREFIX}/redoc",
            "openapi": f"{API_PREFIX}/openapi.json",
            "capabilities": f"{API_PREFIX}/capabilities",
            "health": f"{API_PREFIX}/health",
            "workflows": {
                "rule_to_shape": f"{API_PREFIX}/workflows/rule-to-shape",
                "batch_to_shapes": f"{API_PREFIX}/workflows/batch-to-shapes",
                "batch_stream": f"{API_PREFIX}/batches/generate",
            },
            "documentation": f"{API_PREFIX}/docs",
            "api_documentation": f"{PROJECT_REPOSITORY_URL}/blob/main/docs/api.md",
            "repository": PROJECT_REPOSITORY_URL,
        },
        "system.health": {
            **envelope,
            "status": "ok",
            "version": __version__,
            "deployment_profile": "local",
        },
        "ontology.parse": {
            **envelope,
            "prefixes": "@prefix ex: <http://example.org/books#> .",
            "entities": [
                {
                    "id": "class-0", "iri": "ex:Book",
                    "full_iri": "http://example.org/books#Book", "label": "Book",
                    "type": "class", "kind": "Class", "domain": [], "range": [],
                    "superclasses": [], "comment": "", "ontology_note": "",
                    "annotations": {},
                },
                {
                    "id": "property-0", "iri": "ex:title",
                    "full_iri": "http://example.org/books#title", "label": "title",
                    "type": "property", "kind": "DatatypeProperty",
                    "domain": [book_ref], "range": [string_ref], "superclasses": [],
                    "comment": "", "ontology_note": "", "annotations": {},
                },
            ],
            "base_namespace": "http://example.org/books#",
            "shape_namespace": "http://example.org/books/shapes/",
            "shape_prefix": "shape",
            "namespace_analysis": {
                "namespace": "http://example.org/books#", "detected_by": "term-coverage",
                "term_count": 2, "total_terms": 2, "coverage": 1.0,
                "confidence": 1.0,
                "candidates": [{
                    "namespace": "http://example.org/books#", "term_count": 2,
                    "coverage": 1.0, "prefixes": ["ex"], "ontology_hint": False,
                }],
                "shape_namespace": "http://example.org/books/shapes/",
                "shape_namespace_source": "derived", "shape_prefix": "shape",
                "shape_prefix_source": "default", "managed_prefixes": ["shape"],
            },
        },
        "ontology.search": {
            **envelope,
            "inference_provider": "databricks",
            "embedding_model": "configured-embedding-model",
            "candidates": [
                {"entity_id": "class-0", "score": 0.91, "reasons": ["semantic similarity"]},
                {"entity_id": "property-0", "score": 0.89, "reasons": ["semantic similarity"]},
            ],
            "method": "semantic", "message": "Ranked 2 ontology terms.",
        },
        "rules.resolve-targets": {
            **envelope,
            "rules": [{
                "rule": BOOK_RULE,
                "target_details": [book_ref, title_ref],
                "target_roles": roles,
                "resolved_by": "label", "resolution_score": 0.91,
                "score_kind": "lexical",
                "candidates": [{
                    "target": "ex:title", "iri": "ex:title", "label": "title",
                    "type": "property", "kind": "DatatypeProperty",
                    "domain": [book_ref], "range": [string_ref], "superclasses": [],
                    "score": 0.91, "reasons": ["exact label phrase in text"],
                }],
                "signal_candidates": {"index": [], "label": [], "semantic": [], "llm": []},
            }],
            "summary": {"total": 1, "index": 0, "label": 1, "semantic": 0,
                        "llm": 0, "none": 0, "without_llm": 1,
                        "without_llm_excluding_index": 1},
            "ontology_namespace": "http://example.org/books#",
            "ontology_term_count": 2,
        },
        "shapes.build": {
            **envelope,
            "shape_document": BOOK_SHAPE, "valid": True, "attempts": 1,
            "hints": ["sh:minCount 1", "sh:maxCount 1", "sh:datatype xsd:string"],
            "fallback": False, "not_found": False, "error_type": "none",
            "message": "Valid SHACL generated on attempt 1.",
            "validation": valid_result, "logs": "", "inference_provider": "databricks",
            "generation_model": "configured-chat-model",
        },
        "baselines.astrea.generate": {
            **envelope,
            "available": True, "source": "astrea-api", "name": "books_astrea.ttl",
            "size": len(BOOK_SHAPE), "ontology_hash": "sha256:books-example",
            "shape_document": BOOK_SHAPE, "shape_count": 1,
            "validation": valid_result,
            "message": "Astrea generated one validated baseline shape.",
        },
        "shapes.merge": {
            **envelope,
            **valid_result,
            "shape_document": BOOK_SHAPE,
            "merge": {
                "merge_strategy": "generated-priority", "triples": 8,
                "warnings": [],
                "statistics": {"generated_shapes": 1, "astrea_shapes": 1},
            },
            "baseline_name": "astrea.ttl",
            "merge_message": "Merged with generated-shape priority.",
        },
        "workflows.rule.generate": {
            **envelope,
            "workflow": "rule-to-shape", "rule": resolution,
            "shape": shape_outcome, "unresolved": False, "unresolved_rules": [],
            "summary": summary,
            "namespaces": {
                "prefixes": "@prefix ex: <http://example.org/books#> .",
                "base_namespace": "http://example.org/books#",
                "shape_namespace": "http://example.org/books/shapes/",
                "shape_prefix": "shape",
            },
            "astrea": astrea_status, "final_shape_document": BOOK_SHAPE, "logs": "",
        },
        "workflows.batch.generate": {
            **envelope,
            "workflow": "batch-to-shapes", "summary": summary,
            "rules": [resolution], "shapes": [shape_outcome], "unresolved_rules": [],
            "namespaces": {
                "prefixes": "@prefix ex: <http://example.org/books#> .",
                "base_namespace": "http://example.org/books#",
                "shape_namespace": "http://example.org/books/shapes/",
                "shape_prefix": "shape",
            },
            "astrea": astrea_status, "final_shape_document": BOOK_SHAPE, "logs": "",
        },
        "models.check": {
            **envelope, "ok": True, "message": "Model is available.",
            "inference_provider": "databricks", "model_id": "configured-chat-model",
        },
        "models.local.status": {
            **envelope,
            "model_id": "organisation/tiny-model",
            "downloaded": True,
            "status": "downloaded",
            "message": "The model snapshot is available locally.",
        },
    }
    if operation == "system.capabilities":
        document = capabilities("local")
        document["api"] = {
            **api_catalog(),
            "service_layout": "unified",
            "runtime_endpoints": frontend_endpoint_map("unified"),
        }
        return json.loads(json.dumps({**envelope, **document}))
    if operation in {
        "ontology.index.create",
        "ontology.index.get",
        "ontology.index.delete",
        "models.local.download.create",
        "models.local.download.get",
        "models.local.download.delete",
    }:
        state = {
            "ontology.index.create": ("queued", 0.0, "Ontology index preparation was queued."),
            "ontology.index.get": ("running", 0.5, "Ontology index preparation is running."),
            "ontology.index.delete": ("cancelled", 0.5, "Ontology index cancellation was requested."),
            "models.local.download.create": ("queued", 0.0, "Local model download was queued."),
            "models.local.download.get": ("completed", 1.0, "Local model download completed."),
            "models.local.download.delete": ("cancelled", 0.25, "Local model download cancellation was requested."),
        }[operation]
        result = {
            **envelope,
            "job_id": "job-books-001",
            "status": state[0],
            "progress": state[1],
            "message": state[2],
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:05Z",
        }
        if operation.startswith("ontology.index."):
            result.update(completed_terms=1 if state[0] == "running" else 0, total_terms=2)
        return result
    if operation == "shapes.validate" and status == 200:
        return {
            "valid": False,
            "syntax_valid": False,
            "profile_valid": False,
            "profile_count": 1,
            "profile_names": ["shacl-shacl.ttl"],
            "generic_profile_active": True,
            "generic_profile_name": "shacl-shacl.ttl",
            "domain_profile_count": 0,
            "domain_profile_names": [],
            "validation_level": "syntax",
            "error": "Invalid Turtle syntax.",
            "error_type": "parse",
            "report_text": "",
            "message": "The SHACL document is not valid Turtle.",
            **envelope,
        }
    return deepcopy(examples.get(operation))


def _operation_metadata_example(operation: str) -> Dict[str, Any]:
    endpoint = next((item for item in ENDPOINTS if item.operation == operation), None)
    return {
        "request_id": "req-books-001",
        "operation": operation,
        "service": endpoint.service_id if endpoint and endpoint.service_id else "platform",
        "api_version": API_VERSION,
        "deployment_profile": "local",
        "created_at": "2026-01-01T00:00:00Z",
        "duration_ms": 12.5,
        "warnings": [],
    }


def _authoring_provenance_example(operation: str) -> Dict[str, Any]:
    base = {
        "evidence": [],
        "generation_parameters": {},
        "validation_profiles": ["shacl-shacl.ttl"],
        "validation_results": [],
        "warnings": [],
        "errors": [],
        "created_at": "2026-01-01T00:00:00Z",
    }
    if operation == "shapes.validate":
        return base
    if operation == "baselines.astrea.generate":
        return {
            **base,
            "baseline_usage": "evidence",
            "baseline_source": "astrea-api",
        }
    if operation == "shapes.merge":
        return {
            **base,
            "baseline_usage": "merge",
            "baseline_source": "astrea.ttl",
            "merge_strategy": "generated-priority",
        }
    document = {
        **base,
        "source_rule": BOOK_RULE,
        "selected_targets": [{"iri": "ex:Book", "label": "Book"}, {"iri": "ex:title", "label": "title"}],
        "target_roles": {
            "focus_nodes": [{"iri": "ex:Book", "label": "Book"}],
            "constraint_paths": [{"iri": "ex:title", "label": "title"}],
            "related_terms": [],
        },
        "resolved_by": "label",
        "resolution_score": 0.91,
        "score_kind": "lexical",
        "evidence": [],
        "generation_model": "configured-chat-model",
        "embedding_model": "configured-embedding-model",
        "inference_provider": "databricks",
        "generation_parameters": {"temperature": 0.2, "max_new_tokens": 3000},
        "baseline_usage": "none",
    }
    if operation in {"workflows.batch.generate", "batches.generate"}:
        document.pop("source_rule", None)
    return document


def _provenance_example(operation: str) -> Dict[str, Any]:
    """Retain the former helper name for downstream documentation tests."""
    return _authoring_provenance_example(operation)


def _error_example(operation: str, status: int) -> Dict[str, Any]:
    """Return one operation-aware ApiError example for an HTTP status."""
    request_id = "req-books-001"
    if status == 400:
        if operation in {
            "rules.resolve-targets",
            "workflows.rule.generate",
            "workflows.batch.generate",
            "batches.generate",
        }:
            return {
                "error": "invalid_request",
                "code": "INVALID_RESOLVER_CONFIGURATION",
                "message": "semantic_target_margin must be lower than semantic_threshold.",
                "request_id": request_id,
                "details": {},
            }
        return {
            "error": "invalid_request",
            "code": "MALFORMED_JSON",
            "message": "The request body is not valid JSON.",
            "request_id": request_id,
            "details": {},
        }
    if status == 401:
        return {
            "error": "provider_authentication_failed",
            "code": "PROVIDER_AUTHENTICATION_FAILED",
            "message": "The inference provider rejected the supplied credentials.",
            "request_id": request_id,
            "details": {"provider": "databricks"},
        }
    if status == 403:
        return {
            "error": "capability_disabled",
            "code": "LOCAL_MODELS_DISABLED",
            "message": "Local model execution is disabled in the public deployment profile.",
            "request_id": request_id,
            "details": {"provider": "huggingface"},
        }
    if status == 404:
        ontology_job = operation.startswith("ontology.index.")
        return {
            "error": "resource_not_found",
            "code": (
                "ONTOLOGY_INDEX_JOB_NOT_FOUND"
                if ontology_job
                else "LOCAL_MODEL_DOWNLOAD_JOB_NOT_FOUND"
            ),
            "message": "No job exists with the supplied identifier.",
            "request_id": request_id,
            "details": {"job_id": "job-unknown"},
        }
    if status == 409:
        return {
            "error": "job_state_conflict",
            "code": "JOB_ALREADY_COMPLETED",
            "message": "A completed job cannot be cancelled.",
            "request_id": request_id,
            "details": {"job_id": "job-books-001", "status": "completed"},
        }
    if status == 413:
        if operation in {"ontology.parse", "ontology.search", "ontology.index.create", "rules.resolve-targets", "baselines.astrea.generate", "workflows.rule.generate", "workflows.batch.generate", "batches.generate"}:
            limit_mb, resource = 200, "ontology upload"
        elif operation in {"shapes.validate", "shapes.merge"}:
            limit_mb, resource = 50, "SHACL document"
        else:
            limit_mb, resource = 256, "request body"
        return {
            "error": "payload_too_large",
            "code": "PAYLOAD_TOO_LARGE",
            "message": "The request exceeds the configured upload limit.",
            "request_id": request_id,
            "details": {"limit_mb": limit_mb, "resource": resource},
        }
    if status == 422:
        return {
            "error": "request_validation_failed",
            "code": "REQUEST_SCHEMA_VALIDATION_FAILED",
            "message": "Request body validation failed.",
            "request_id": request_id,
            "details": {
                "issues": [{
                    "location": ["rule", "text"],
                    "message": "Field required",
                    "type": "missing",
                }]
            },
        }
    if status == 429:
        return {
            "error": "rate_limit_exceeded",
            "code": "DEPLOYMENT_RATE_LIMIT_EXCEEDED",
            "message": "The deployment rate limit has been exceeded.",
            "request_id": request_id,
            "details": {"retry_after_seconds": 30},
        }
    if status == 500:
        return {
            "error": "internal_failure",
            "code": "UNEXPECTED_INTERNAL_ERROR",
            "message": "An unexpected internal error occurred.",
            "request_id": request_id,
            "details": {},
        }
    provider = (
        "astrea"
        if operation in {
            "baselines.astrea.generate",
            "workflows.rule.generate",
            "workflows.batch.generate",
        }
        else "databricks"
    )
    if status == 502:
        return {
            "error": "invalid_upstream_response",
            "code": "ASTREA_INVALID_RESPONSE" if provider == "astrea" else "MODEL_INVALID_RESPONSE",
            "message": "The upstream provider returned an invalid response.",
            "request_id": request_id,
            "details": {"provider": provider},
        }
    if status == 503:
        if operation in {
            "ontology.index.create",
            "models.local.download.create",
            "workflows.batch.generate",
            "batches.generate",
        }:
            return {
                "error": "capacity_exhausted",
                "code": "DEPLOYMENT_CAPACITY_EXHAUSTED",
                "message": "The deployment has reached its configured processing capacity.",
                "request_id": request_id,
                "details": {"resource": "asynchronous_jobs"},
            }
        details = {"provider": provider}
        if provider != "astrea":
            details["model"] = "configured-chat-model"
        return {
            "error": "upstream_unavailable",
            "code": "ASTREA_UNAVAILABLE" if provider == "astrea" else "MODEL_UNAVAILABLE",
            "message": "The configured upstream service is currently unavailable.",
            "request_id": request_id,
            "details": details,
        }
    if status == 504:
        return {
            "error": "upstream_timeout",
            "code": "ASTREA_REQUEST_TIMEOUT" if provider == "astrea" else "MODEL_REQUEST_TIMEOUT",
            "message": "The upstream provider did not respond before the timeout.",
            "request_id": request_id,
            "details": {"provider": provider},
        }
    raise ValueError(f"No ApiError example is defined for HTTP {status}.")


ERROR_RESPONSES = {
    400: "The request is semantically invalid or inconsistent.",
    401: "The configured inference provider rejected or requires authentication.",
    403: "The requested capability is disabled by the deployment profile.",
    404: "The requested resource or job was not found.",
    409: "The requested operation conflicts with the current job state.",
    413: "The request body or one uploaded resource exceeds its configured size limit.",
    422: "The JSON body does not conform to the request schema.",
    429: "An upstream or deployment rate limit was exceeded.",
    500: "An unexpected internal failure occurred.",
    502: "An upstream provider returned an invalid response.",
    503: "An upstream provider or configured model is unavailable.",
    504: "An upstream provider timed out.",
}


OPERATION_ERRORS = {
    "workflows.rule.generate": (400, 403, 422, 429, 500, 502, 503, 504),
    "workflows.batch.generate": (400, 403, 422, 429, 500, 502, 503, 504),
    "ontology.parse": (400, 422, 500),
    "ontology.search": (400, 403, 422, 500),
    "ontology.index.create": (400, 403, 422, 500, 503),
    "ontology.index.get": (404, 500),
    "ontology.index.delete": (404, 409, 500),
    "rules.resolve-targets": (400, 403, 422, 500),
    "shapes.build": (400, 403, 422, 500, 503, 504),
    "shapes.validate": (400, 422, 500),
    "baselines.astrea.generate": (400, 422, 429, 500, 502, 503, 504),
    "shapes.merge": (400, 422, 500),
    "batches.generate": (400, 403, 422, 500, 503, 504),
    "models.check": (400, 401, 403, 422, 429, 500, 503, 504),
    "models.local.status": (403, 422, 500),
    "models.local.download.create": (403, 422, 500, 503),
    "models.local.download.get": (404, 500),
    "models.local.download.delete": (404, 409, 500),
}


def _request_schema_name(operation: str) -> Optional[str]:
    model = REQUEST_MODELS.get(operation)
    return model.__name__ if model else None


def _response_schema_name(operation: str) -> Optional[str]:
    model = RESPONSE_MODELS.get(operation)
    if model:
        return model.__name__
    if operation == "system.openapi":
        return "OpenApiDocument"
    return None


def _success_response(endpoint) -> Dict[str, Any]:
    status = endpoint.success_status
    if endpoint.transport == "sse":
        is_batch_stream = endpoint.operation == "batches.generate"
        operation_metadata = _operation_metadata_example(endpoint.operation)
        provenance = (
            _authoring_provenance_example(endpoint.operation)
            if is_authoring_operation(endpoint.operation)
            else None
        )
        common = {
            "request_id": "req-books-001",
            "timestamp": "2026-01-01T00:00:00Z",
            "operation_metadata": operation_metadata,
            **({"provenance": provenance} if provenance else {}),
            "extensions": {},
        }
        event_examples = {
            "started": {
                **common,
                "event": "started",
                "sequence": 1,
                "message": (
                    "Batch generation started."
                    if is_batch_stream
                    else "Local model download started."
                ),
                "total_items": 1 if is_batch_stream else 100,
                **({"total_rules": 1} if is_batch_stream else {}),
            },
            "progress": {
                **common,
                "event": "progress",
                "sequence": 2,
                "message": (
                    "Resolving ontology terms."
                    if is_batch_stream
                    else "Downloading local model."
                ),
                "completed_items": 0,
                "total_items": 1 if is_batch_stream else 100,
                "progress": 0.0,
                **({"completed_rules": 0, "total_rules": 1} if is_batch_stream else {}),
            },
            "completed": {
                **common,
                "event": "completed",
                "sequence": 6,
                "message": (
                    "Batch generation completed."
                    if is_batch_stream
                    else "Local model download completed."
                ),
                "completed_items": 1 if is_batch_stream else 100,
                "total_items": 1 if is_batch_stream else 100,
                **({
                    "completed_rules": 1,
                    "total_rules": 1,
                    "final_shape_document": BOOK_SHAPE,
                } if is_batch_stream else {}),
            },
            "failed": {
                **common,
                "event": "failed",
                "sequence": 4,
                "error": {
                    "error": "upstream_timeout" if is_batch_stream else "upstream_unavailable",
                    "code": "MODEL_REQUEST_TIMEOUT" if is_batch_stream else "MODEL_DOWNLOAD_FAILED",
                    "message": (
                        "The inference provider timed out."
                        if is_batch_stream
                        else "The local model download failed."
                    ),
                    "request_id": "req-books-001",
                    "details": (
                        {"provider": "databricks"}
                        if is_batch_stream
                        else {"model": "organisation/tiny-model"}
                    ),
                },
            },
        }
        if is_batch_stream:
            event_examples["rule_resolved"] = {
                **common,
                "event": "rule_resolved",
                "sequence": 3,
                "rule": BOOK_RULE,
                "target_roles": {
                    "focus_nodes": [{"iri": "ex:Book", "label": "Book"}],
                    "constraint_paths": [{"iri": "ex:title", "label": "title"}],
                    "related_terms": [],
                },
                "resolved_by": "label",
                "resolution_score": 0.91,
                "score_kind": "lexical",
            }
        return {
            str(status): {
                "description": "A named SSE stream. Every data field is a JSON event from the discriminated SseEvent union; completed and failed are terminal.",
                "content": {
                    "text/event-stream": {
                        "schema": {"type": "string"},
                        "examples": {
                            name: {
                                "value": f"event: {name}\ndata: {json.dumps(value)}\n\n"
                            }
                            for name, value in event_examples.items()
                        },
                        "x-sse-event-schema": _schema_ref("SseEvent"),
                        "x-sse-event-examples": event_examples,
                        "x-sse-contract": {
                            "terminal_events": ["completed", "failed"],
                            "heartbeat_seconds": 15,
                            "idle_timeout_seconds": operational_settings().sse_idle_timeout_seconds,
                            "replay_supported": False,
                            "last_event_id_supported": False,
                            "disconnect_behavior": (
                                "Delivery stops after client disconnection; batch work may finish server-side."
                            ),
                            "pre_stream_errors": "ApiError JSON response",
                            "post_stream_errors": "failed terminal SSE event",
                        },
                    }
                },
            }
        }
    if endpoint.transport == "html":
        return {str(status): {"description": "HTML documentation page.", "content": {"text/html": {"schema": {"type": "string"}}}}}
    schema_name = _response_schema_name(endpoint.operation)
    content: Dict[str, Any] = {
        "schema": _schema_ref(schema_name) if schema_name else {"type": "object"}
    }
    example = _response_example(endpoint.operation, status)
    if example is not None:
        content["example"] = example
    return {str(status): {"description": HTTPStatus(status).phrase, "content": {"application/json": content}}}


def _operation_document(endpoint) -> Dict[str, Any]:
    description = endpoint.description or endpoint.summary
    operation: Dict[str, Any] = {
        "operationId": endpoint.operation.replace(".", "_"),
        "x-shard-operation": endpoint.operation,
        "tags": [next((service.title for service in LOGICAL_SERVICES if service.service_id == endpoint.service_id), "Platform")],
        "summary": endpoint.summary,
        "description": description,
        "responses": _success_response(endpoint),
    }
    error_statuses = set(OPERATION_ERRORS.get(endpoint.operation, (500,)))
    if endpoint.method in {"POST", "PUT", "PATCH"}:
        error_statuses.add(400)
        error_statuses.add(413)
    if endpoint.operation in RATE_LIMITED_OPERATIONS:
        error_statuses.add(429)
    for status in sorted(error_statuses):
        response = {
            "description": ERROR_RESPONSES[status],
            "content": {
                "application/json": {
                    "schema": _schema_ref("ApiError"),
                    "example": _response_example(endpoint.operation, status),
                }
            },
        }
        if status == 429:
            response["headers"] = {
                "Retry-After": {
                    "description": "Seconds before the client should retry.",
                    "schema": {"type": "integer", "minimum": 1},
                }
            }
        operation["responses"][str(status)] = response
    request_schema = _request_schema_name(endpoint.operation)
    if endpoint.method in {"POST", "PUT", "PATCH"} and request_schema:
        media: Dict[str, Any] = {"schema": _schema_ref(request_schema)}
        example = request_example(endpoint.operation)
        if example is not None:
            media["example"] = example
        operation["requestBody"] = {
            "required": True,
            "content": {"application/json": media},
        }
    if "{job_id}" in endpoint.path:
        operation["parameters"] = [{
            "name": "job_id",
            "in": "path",
            "required": True,
            "description": "Stable identifier returned when the job was created.",
            "schema": {"type": "string", "minLength": 1},
        }]
    return operation


def openapi_document() -> Dict[str, Any]:
    """Return a deterministic OpenAPI 3.1 document for canonical routes."""
    paths: Dict[str, Any] = {}
    for endpoint in ENDPOINTS:
        paths.setdefault(endpoint.path, {})[endpoint.method.lower()] = _operation_document(endpoint)

    return {
        "openapi": "3.1.0",
        "info": {
            "title": f"{__title__} REST API",
            "version": __version__,
            "summary": __description__,
            "description": (
                "Strict, versioned API for ontology-grounded SHACL authoring from data constraints. "
                "Guide-to-Shapes is the narrative workflow name; its API resource is a batch. "
                "Use Rule-to-Shape for one constraint, Batch-to-Shapes for a consolidated JSON "
                "Guide-to-Shapes result, or the batch SSE operation for incremental progress.\n\n"
                "## Client access and provider credentials\n"
                "SHARD does not require client authentication at the API layer in the current "
                "deployment. Databricks and Hugging Face tokens are write-only provider credentials "
                "used only for inference requests. Provider credential failures may return 401; "
                "deployment-profile capability restrictions return 403."
            ),
        },
        "jsonSchemaDialect": "https://spec.openapis.org/oas/3.1/dialect/base",
        "servers": [{"url": "/", "description": "Current SHARD deployment"}],
        "externalDocs": {
            "description": "SHARD API documentation",
            "url": f"{PROJECT_REPOSITORY_URL}/blob/main/docs/api.md",
        },
        "tags": [
            {"name": service.title, "description": service.responsibility}
            for service in LOGICAL_SERVICES
        ] + [{"name": "Platform", "description": "API discovery, health and deployment capabilities."}],
        "paths": paths,
        "components": {
            "schemas": _component_schemas(),
        },
        "x-shard-api-version": API_VERSION,
        "x-shard-api-prefix": API_PREFIX,
    }
