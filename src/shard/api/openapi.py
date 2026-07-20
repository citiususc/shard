"""Generate the OpenAPI contract for the versioned SHARD API."""

from __future__ import annotations

from typing import Any, Dict

from shard import __description__, __title__, __version__
from shard.api.contract import API_PREFIX, API_VERSION, ENDPOINTS, LOGICAL_SERVICES
from shard.deployment.policy import PROJECT_REPOSITORY_URL


SCHEMA_REF = "#/components/schemas/"


def _schema_ref(name: str) -> Dict[str, str]:
    return {"$ref": f"{SCHEMA_REF}{name}"}


def _schemas() -> Dict[str, Any]:
    profile = {
        "type": "object",
        "required": ["content"],
        "properties": {
            "name": {"type": "string", "default": "profile.ttl"},
            "content": {"type": "string", "description": "RDF content of a domain SHACL for SHACL profile."},
        },
        "additionalProperties": False,
    }
    ontology = {
        "type": "object",
        "required": ["content"],
        "properties": {
            "filename": {"type": "string", "default": "ontology.ttl"},
            "content": {"type": "string", "description": "OWL/RDF ontology content."},
        },
        "additionalProperties": False,
    }
    inference = {
        "type": "object",
        "properties": {
            "provider": {"type": "string", "enum": ["databricks", "huggingface"]},
            "generation_model": {"type": "string", "description": "Chat model used for resolution fallback and SHACL generation."},
            "embedding_model": {"type": "string", "description": "Embedding model used for semantic ontology-term ranking."},
            "temperature": {"type": "number", "default": 0.5},
            "databricks": {
                "type": "object",
                "properties": {
                    "base_url": {"type": "string", "format": "uri"},
                    "token": {"type": "string", "format": "password", "writeOnly": True},
                },
                "additionalProperties": False,
            },
            "huggingface": {
                "type": "object",
                "properties": {
                    "token": {"type": "string", "format": "password", "writeOnly": True},
                },
                "additionalProperties": False,
            },
        },
        "additionalProperties": False,
    }
    generation = {
        "type": "object",
        "properties": {
            "domain_context": {"type": "string"},
            "guidance": {"type": "string"},
            "prefixes": {"type": "string", "description": "Optional Turtle prefix block. Derived from the ontology when omitted."},
            "base_namespace": {"type": "string", "format": "uri"},
            "shape_namespace": {"type": "string", "format": "uri"},
            "shape_prefix": {"type": "string", "default": "shape"},
        },
        "additionalProperties": False,
    }
    resolver = {
        "type": "object",
        "properties": {
            "semantic_threshold": {"type": "number", "minimum": -1, "maximum": 1, "default": 0.60},
            "semantic_target_margin": {"type": "number", "minimum": 0, "maximum": 2, "default": 0.16},
            "semantic_max_targets": {"type": "integer", "minimum": 1, "maximum": 20, "default": 4},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            "llm_fallback": {"type": "boolean", "default": True},
            "wait_embeddings": {"type": "boolean", "default": True},
            "embedding_timeout": {"type": "integer", "minimum": 1, "default": 900},
            "embedding_poll_seconds": {"type": "number", "exclusiveMinimum": 0, "default": 2.0},
            "strict_semantic": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
    }
    baseline = {
        "type": "object",
        "required": ["content"],
        "properties": {
            "name": {"type": "string", "default": "astrea.ttl"},
            "content": {"type": "string", "description": "Previously generated Astrea SHACL content."},
        },
        "additionalProperties": False,
    }
    astrea = {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["none", "baseline", "merge", "both"],
                "default": "none",
                "description": "Use Astrea as generation evidence, for final merge, for both, or not at all.",
            },
            "merge_technique": {
                "type": "string",
                "enum": ["priority-llm", "restrictive"],
                "default": "priority-llm",
            },
            "failure_policy": {
                "type": "string",
                "enum": ["continue", "fail"],
                "default": "continue",
                "description": "Continue without Astrea or fail the workflow if the external service is unavailable.",
            },
            "baseline": _schema_ref("BaselineInput"),
        },
        "additionalProperties": False,
    }
    common_properties = {
        "ontology": _schema_ref("OntologyInput"),
        "inference": _schema_ref("InferenceOptions"),
        "generation": _schema_ref("GenerationOptions"),
        "resolver": _schema_ref("ResolverOptions"),
        "validation_profiles": {
            "type": "array",
            "items": _schema_ref("ValidationProfile"),
            "description": "Optional domain profiles. The generic profile is always applied automatically.",
        },
        "astrea": _schema_ref("AstreaOptions"),
    }
    rule_request = {
        "type": "object",
        "required": ["ontology", "rule"],
        "properties": {
            **common_properties,
            "rule": _schema_ref("BusinessRuleInput"),
        },
        "additionalProperties": True,
    }
    guide_request = {
        "type": "object",
        "required": ["ontology", "guide"],
        "properties": {
            **common_properties,
            "guide": _schema_ref("BusinessRulesGuideInput"),
        },
        "additionalProperties": True,
    }
    return {
        "OntologyInput": ontology,
        "BusinessRuleInput": {
            "type": "object",
            "required": ["text"],
            "properties": {
                "number": {"type": "string", "default": "RULE-001"},
                "title": {"type": "string", "default": "Business rule"},
                "text": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "BusinessRulesGuideInput": {
            "type": "object",
            "required": ["content"],
            "properties": {
                "filename": {
                    "type": "string",
                    "default": "business_rules.md",
                    "description": "A .md or .html filename used to select the common guide parser.",
                },
                "content": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "InferenceOptions": inference,
        "GenerationOptions": generation,
        "ResolverOptions": resolver,
        "ValidationProfile": profile,
        "BaselineInput": baseline,
        "AstreaOptions": astrea,
        "RuleWorkflowRequest": rule_request,
        "GuideWorkflowRequest": guide_request,
        "OntologyParseRequest": {
            "type": "object",
            "required": ["filename", "content"],
            "properties": {
                "filename": {"type": "string"},
                "content": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "OntologyTerm": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "iri": {"type": "string"},
                "full_iri": {"type": "string", "format": "uri"},
                "label": {"type": "string"},
                "type": {"type": "string", "enum": ["class", "property"]},
                "kind": {"type": "string"},
                "domain": {"type": ["string", "array"]},
                "range": {"type": ["string", "array"]},
                "ontologyNote": {"type": "string"},
            },
            "additionalProperties": True,
        },
        "RuntimeInferenceConfig": {
            "type": "object",
            "properties": {
                "provider": {"type": "string", "enum": ["databricks", "huggingface"]},
                "databricks": {
                    "type": "object",
                    "properties": {
                        "base_url": {"type": "string", "format": "uri"},
                        "token": {"type": "string", "format": "password", "writeOnly": True},
                    },
                    "additionalProperties": False,
                },
                "huggingface": {
                    "type": "object",
                    "properties": {
                        "token": {"type": "string", "format": "password", "writeOnly": True},
                    },
                    "additionalProperties": False,
                },
            },
            "additionalProperties": True,
        },
        "TermRankingRequest": {
            "type": "object",
            "required": ["business_rule", "ontology_terms"],
            "properties": {
                "business_rule": {"type": "string"},
                "ontology_terms": {"type": "array", "items": _schema_ref("OntologyTerm")},
                "ontology_hash": {"type": "string"},
                "embedding_model": {"type": "string"},
                "entity_types": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["class", "property"]},
                },
                "top_k": {"type": "integer", "minimum": 1, "maximum": 100, "default": 8},
                "inference_config": _schema_ref("RuntimeInferenceConfig"),
            },
            "additionalProperties": True,
        },
        "EmbeddingIndexRequest": {
            "type": "object",
            "properties": {
                "ontology_terms": {"type": "array", "items": _schema_ref("OntologyTerm")},
                "ontology_hash": {"type": "string"},
                "ontology_fingerprint": {"type": "string"},
                "embedding_model": {"type": "string"},
                "inference_config": _schema_ref("RuntimeInferenceConfig"),
            },
            "additionalProperties": True,
        },
        "TargetResolutionRequest": {
            "type": "object",
            "required": ["ontology_content"],
            "anyOf": [
                {"required": ["business_rule"]},
                {"required": ["guide_content"]},
            ],
            "properties": {
                "ontology_filename": {"type": "string", "default": "ontology.ttl"},
                "ontology_content": {"type": "string"},
                "business_rule": {"type": "string"},
                "rule_number": {"type": "string"},
                "rule_title": {"type": "string"},
                "guide_filename": {"type": "string"},
                "guide_content": {"type": "string"},
                "embedding_model": {"type": "string"},
                "llm_model": {"type": "string"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                "label_threshold": {"type": "number", "default": 0.68},
                "strong_label_threshold": {"type": "number", "default": 0.86},
                "semantic_threshold": {"type": "number", "default": 0.60},
                "semantic_target_margin": {"type": "number", "default": 0.16},
                "semantic_max_targets": {"type": "integer", "minimum": 1, "maximum": 20, "default": 4},
                "resolver_llm_fallback": {"type": "boolean", "default": True},
                "index_map": {"type": "object", "additionalProperties": True},
                "inference_config": _schema_ref("RuntimeInferenceConfig"),
            },
            "additionalProperties": True,
        },
        "TargetRoles": {
            "type": "object",
            "properties": {
                "focus_nodes": {
                    "type": "array",
                    "items": {"oneOf": [{"type": "string"}, _schema_ref("OntologyTerm")]},
                },
                "constraint_paths": {
                    "type": "array",
                    "items": {"oneOf": [{"type": "string"}, _schema_ref("OntologyTerm")]},
                },
                "related_terms": {
                    "type": "array",
                    "items": {"oneOf": [{"type": "string"}, _schema_ref("OntologyTerm")]},
                },
            },
            "additionalProperties": False,
        },
        "ShapeBuildRequest": {
            "type": "object",
            "required": ["business_rule", "ontology_content"],
            "properties": {
                "business_rule": {"type": "string"},
                "ontology_filename": {"type": "string", "default": "ontology.ttl"},
                "ontology_content": {"type": "string"},
                "target": _schema_ref("OntologyTerm"),
                "target_roles": _schema_ref("TargetRoles"),
                "model": {"type": "string"},
                "temperature": {"type": "number", "default": 0.5},
                "domain_context": {"type": "string"},
                "generation_guidance": {"type": "string"},
                "prefixes": {"type": "string"},
                "base_namespace": {"type": "string"},
                "shape_namespace": {"type": "string"},
                "shape_prefix": {"type": "string"},
                "validation_profiles": {"type": "array", "items": _schema_ref("ValidationProfile")},
                "astrea_use_mode": {"type": "string", "enum": ["none", "baseline", "merge", "both"]},
                "astrea_baseline": _schema_ref("BaselineInput"),
                "inference_config": _schema_ref("RuntimeInferenceConfig"),
            },
            "additionalProperties": True,
        },
        "GuideStreamRequest": {
            "type": "object",
            "required": ["ontology_content", "guide_content"],
            "properties": {
                "ontology_filename": {"type": "string", "default": "ontology.ttl"},
                "ontology_content": {"type": "string"},
                "guide_filename": {"type": "string"},
                "guide_content": {"type": "string"},
                "llm_model": {"type": "string"},
                "embedding_model": {"type": "string"},
                "temperature": {"type": "number", "default": 0.5},
                "domain_context": {"type": "string"},
                "generation_guidance": {"type": "string"},
                "semantic_threshold": {"type": "number", "default": 0.60},
                "semantic_target_margin": {"type": "number", "default": 0.16},
                "semantic_max_targets": {"type": "integer", "minimum": 1, "maximum": 20, "default": 4},
                "resolver_llm_fallback": {"type": "boolean", "default": True},
                "wait_embeddings": {"type": "boolean", "default": True},
                "embedding_timeout": {"type": "integer", "minimum": 1, "default": 900},
                "validation_profiles": {"type": "array", "items": _schema_ref("ValidationProfile")},
                "astrea_use_mode": {"type": "string", "enum": ["none", "baseline", "merge", "both"]},
                "astrea_baseline": _schema_ref("BaselineInput"),
                "inference_config": _schema_ref("RuntimeInferenceConfig"),
            },
            "additionalProperties": True,
        },
        "ModelCheckRequest": {
            "type": "object",
            "required": ["provider", "model"],
            "properties": {
                "provider": {"type": "string", "enum": ["databricks", "huggingface"]},
                "model": {"type": "string"},
                "role": {"type": "string", "enum": ["chat", "embedding"], "default": "chat"},
                "inference_config": _schema_ref("RuntimeInferenceConfig"),
            },
            "additionalProperties": True,
        },
        "LocalModelRequest": {
            "type": "object",
            "required": ["model"],
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Repository-style model id selected for local inference.",
                },
                "provider": {"const": "huggingface"},
                "inference_config": _schema_ref("RuntimeInferenceConfig"),
            },
            "additionalProperties": True,
        },
        "ShapeValidationRequest": {
            "type": "object",
            "required": ["shape"],
            "properties": {
                "shape": {"type": "string"},
                "prefixes": {"type": "string"},
                "validation_profiles": {"type": "array", "items": _schema_ref("ValidationProfile")},
            },
            "additionalProperties": True,
        },
        "AstreaBaselineRequest": {
            "type": "object",
            "required": ["ontology_content"],
            "properties": {
                "ontology_filename": {"type": "string", "default": "ontology.ttl"},
                "ontology_content": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "ShapeMergeRequest": {
            "type": "object",
            "required": ["generated_shapes", "astrea_baseline", "technique"],
            "properties": {
                "generated_shapes": {"type": "string"},
                "astrea_baseline": _schema_ref("BaselineInput"),
                "technique": {"type": "string", "enum": ["priority-llm", "restrictive"]},
                "validation_profiles": {"type": "array", "items": _schema_ref("ValidationProfile")},
            },
            "additionalProperties": True,
        },
        "FreeFormRequest": {
            "type": "object",
            "description": "Operational payload used by the interactive client. Prefer a workflow endpoint for external automation.",
            "additionalProperties": True,
        },
        "WorkflowSummary": {
            "type": "object",
            "properties": {
                "rules_total": {"type": "integer"},
                "rules_unresolved": {"type": "integer"},
                "targets_total": {"type": "integer"},
                "generated_total": {"type": "integer"},
                "valid": {"type": "integer"},
                "invalid": {"type": "integer"},
            },
            "additionalProperties": True,
        },
        "RuleWorkflowResponse": {
            "type": "object",
            "required": ["workflow", "rule", "unresolved", "summary", "final_shape_document"],
            "properties": {
                "workflow": {"const": "rule-to-shape"},
                "rule": {"type": "object", "additionalProperties": True},
                "shape": {"type": ["object", "null"], "additionalProperties": True},
                "unresolved": {"type": "boolean"},
                "unresolved_rules": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                "summary": _schema_ref("WorkflowSummary"),
                "namespaces": {"type": "object", "additionalProperties": True},
                "astrea": {"type": "object", "additionalProperties": True},
                "merge": {"type": ["object", "null"], "additionalProperties": True},
                "final_shape_document": {"type": "string"},
                "logs": {"type": "string"},
                "request_id": {"type": "string"},
                "provenance": {"type": "object", "additionalProperties": True},
            },
            "additionalProperties": True,
        },
        "GuideWorkflowResponse": {
            "type": "object",
            "required": ["workflow", "summary", "generation", "final_shape_document"],
            "properties": {
                "workflow": {"const": "guide-to-shapes"},
                "summary": _schema_ref("WorkflowSummary"),
                "generation": {"type": "object", "additionalProperties": True},
                "astrea": {"type": "object", "additionalProperties": True},
                "merge": {"type": ["object", "null"], "additionalProperties": True},
                "final_shape_document": {"type": "string"},
                "logs": {"type": "string"},
                "request_id": {"type": "string"},
                "provenance": {"type": "object", "additionalProperties": True},
            },
            "additionalProperties": True,
        },
        "ApiError": {
            "type": "object",
            "properties": {
                "error": {"type": "string"},
                "code": {"type": "string"},
                "message": {"type": "string"},
                "request_id": {"type": "string"},
            },
            "additionalProperties": True,
        },
        "FreeFormResponse": {"type": "object", "additionalProperties": True},
    }


def _request_schema(operation: str) -> str:
    return {
        "workflows.rule.generate": "RuleWorkflowRequest",
        "workflows.guide.generate": "GuideWorkflowRequest",
        "ontology.parse": "OntologyParseRequest",
        "ontology.search": "TermRankingRequest",
        "ontology.index.prepare": "EmbeddingIndexRequest",
        "ontology.index.status": "EmbeddingIndexRequest",
        "ontology.index.cancel": "EmbeddingIndexRequest",
        "rules.resolve-targets": "TargetResolutionRequest",
        "shapes.build": "ShapeBuildRequest",
        "shapes.validate": "ShapeValidationRequest",
        "baselines.astrea.generate": "AstreaBaselineRequest",
        "shapes.merge": "ShapeMergeRequest",
        "guides.generate": "GuideStreamRequest",
        "models.check": "ModelCheckRequest",
        "models.local.status": "LocalModelRequest",
        "models.local.download": "LocalModelRequest",
    }.get(operation, "FreeFormRequest")


def _response_schema(operation: str) -> str:
    return {
        "workflows.rule.generate": "RuleWorkflowResponse",
        "workflows.guide.generate": "GuideWorkflowResponse",
    }.get(operation, "FreeFormResponse")


def _workflow_example(operation: str) -> Dict[str, Any] | None:
    common = {
        "ontology": {
            "filename": "ontology.ttl",
            "content": "@prefix ex: <http://example.org/domain#> .\n@prefix owl: <http://www.w3.org/2002/07/owl#> .\nex:Asset a owl:Class .",
        },
        "inference": {
            "provider": "databricks",
            "generation_model": "gemma-3-12b",
            "embedding_model": "qwen3-embedding-0-6b",
            "temperature": 0.2,
        },
        "resolver": {
            "semantic_threshold": 0.60,
            "llm_fallback": True,
        },
        "astrea": {"mode": "none"},
    }
    if operation == "workflows.rule.generate":
        return {
            **common,
            "rule": {
                "number": "BR-001",
                "title": "Asset identifier",
                "text": "Every asset must have exactly one identifier.",
            },
        }
    if operation == "workflows.guide.generate":
        return {
            **common,
            "guide": {
                "filename": "business_rules.md",
                "content": "# Business Rules\n\n## Rule\n\n- Number: BR-001\n- Title: Asset identifier\n\n### Business rule\n\nEvery asset must have exactly one identifier.\n",
            },
        }
    return None


def openapi_document() -> Dict[str, Any]:
    """Return a deterministic OpenAPI 3.1 document for canonical routes."""
    service_titles = {service.service_id: service.title for service in LOGICAL_SERVICES}
    paths: Dict[str, Any] = {}
    for endpoint in ENDPOINTS:
        tag = service_titles.get(endpoint.service_id, "Platform")
        if endpoint.transport == "sse":
            response_media_type = "text/event-stream"
            response_schema = {"type": "string"}
        elif endpoint.transport == "html":
            response_media_type = "text/html"
            response_schema = {"type": "string"}
        else:
            response_media_type = "application/json"
            response_schema = _schema_ref(_response_schema(endpoint.operation))
        operation: Dict[str, Any] = {
            "operationId": endpoint.operation.replace(".", "_"),
            "x-shard-operation": endpoint.operation,
            "tags": [tag],
            "summary": endpoint.summary,
            "responses": {
                "200": {
                    "description": "Successful response.",
                    "content": {
                        response_media_type: {"schema": response_schema}
                    },
                },
                "400": {
                    "description": "Invalid request.",
                    "content": {"application/json": {"schema": _schema_ref("ApiError")}},
                },
                "500": {
                    "description": "Service failure.",
                    "content": {"application/json": {"schema": _schema_ref("ApiError")}},
                },
            },
        }
        if endpoint.method == "POST":
            media: Dict[str, Any] = {"schema": _schema_ref(_request_schema(endpoint.operation))}
            example = _workflow_example(endpoint.operation)
            if example is not None:
                media["example"] = example
            operation["requestBody"] = {
                "required": True,
                "content": {"application/json": media},
            }
        paths.setdefault(endpoint.path, {})[endpoint.method.lower()] = operation

    return {
        "openapi": "3.1.0",
        "info": {
            "title": f"{__title__} REST API",
            "version": __version__,
            "summary": __description__,
            "description": (
                "Versioned API for ontology-grounded SHACL authoring from business rules. "
                "Use workflow endpoints for complete automation and operational endpoints "
                "for fine-grained control."
            ),
        },
        "jsonSchemaDialect": "https://spec.openapis.org/oas/3.1/dialect/base",
        "servers": [{"url": "/", "description": "Current SHARD deployment"}],
        "externalDocs": {
            "description": "SHARD API guide",
            "url": f"{PROJECT_REPOSITORY_URL}/blob/main/docs/api.md",
        },
        "tags": [
            {"name": service.title, "description": service.responsibility}
            for service in LOGICAL_SERVICES
        ] + [{"name": "Platform", "description": "API discovery, health and deployment capabilities."}],
        "paths": paths,
        "components": {"schemas": _schemas()},
        "x-shard-api-version": API_VERSION,
        "x-shard-api-prefix": API_PREFIX,
    }
