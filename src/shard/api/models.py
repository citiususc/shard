"""Strict public request and response models for the SHARD API.

The application services intentionally continue to use lightweight dictionaries.
This module defines the versioned HTTP boundary and translates canonical nested
requests to the flat application payloads used internally.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Dict, List, Literal, Mapping, Optional, Union

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    RootModel,
    SecretStr,
    field_serializer,
    field_validator,
    model_validator,
)

from shard.domain.limits import (
    MAX_EMBEDDING_POLL_SECONDS,
    MAX_EMBEDDING_TIMEOUT_SECONDS,
    MAX_NEW_TOKENS,
    MAX_SEMANTIC_TARGETS,
    MAX_TEMPERATURE,
    MAX_TOP_K,
)


class ApiModel(BaseModel):
    """Base class for closed public API objects."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ExtensibleApiModel(ApiModel):
    """Closed API object with two explicitly extensible containers."""

    metadata: Dict[str, JsonValue] = Field(default_factory=dict)
    extensions: Dict[str, JsonValue] = Field(default_factory=dict)


class OntologyInput(ApiModel):
    filename: str = "ontology.ttl"
    content: str = Field(min_length=1)


class BusinessRuleInput(ApiModel):
    number: str = "RULE-001"
    title: str = "Data constraint"
    text: str = Field(min_length=1)


class BusinessRulesBatchInput(ApiModel):
    filename: str = "business_rules.md"
    content: str = Field(min_length=1)
    format: Optional[Literal["md", "markdown", "html", "htm"]] = None


class OntologyTermReference(ApiModel):
    iri: str = Field(min_length=1)
    label: Optional[str] = None


class OntologyTerm(ApiModel):
    id: str
    iri: str
    full_iri: str
    label: str
    type: Literal["class", "property"]
    kind: str
    domain: List[OntologyTermReference] = Field(default_factory=list)
    range: List[OntologyTermReference] = Field(default_factory=list)
    superclasses: List[OntologyTermReference] = Field(default_factory=list)
    comment: str = ""
    ontology_note: str = Field(
        default="",
        validation_alias=AliasChoices("ontology_note", "ontologyNote"),
    )
    annotations: Dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Ontology annotations not represented by the stable catalog fields.",
    )

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_rule_annotations(cls, value: Any) -> Any:
        """Move former rule-specific catalog fields into generic annotations."""
        if not isinstance(value, Mapping):
            return value
        document = dict(value)
        annotations = dict(document.get("annotations") or {})
        former_rule = document.pop("business_rule", document.pop("businessRule", ""))
        former_rules = document.pop("rules", [])
        if former_rule:
            annotations.setdefault("former_rule", former_rule)
        if former_rules:
            annotations.setdefault("former_rules", former_rules)
        document["annotations"] = annotations
        return document


class TargetRoles(ApiModel):
    focus_nodes: List[OntologyTermReference] = Field(default_factory=list)
    constraint_paths: List[OntologyTermReference] = Field(default_factory=list)
    related_terms: List[OntologyTermReference] = Field(default_factory=list)


class DatabricksCredentials(ApiModel):
    base_url: Optional[str] = None
    token: SecretStr = Field(
        default=SecretStr(""),
        json_schema_extra={"format": "password", "writeOnly": True},
    )

    @field_serializer("token")
    def serialize_token(self, value: SecretStr) -> str:
        return value.get_secret_value()


class HuggingFaceCredentials(ApiModel):
    token: SecretStr = Field(
        default=SecretStr(""),
        json_schema_extra={"format": "password", "writeOnly": True},
    )

    @field_serializer("token")
    def serialize_token(self, value: SecretStr) -> str:
        return value.get_secret_value()


class InferenceOptions(ApiModel):
    provider: Optional[Literal["databricks", "huggingface"]] = None
    generation_model: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("generation_model", "llm_model", "model"),
        description="Generation model id. llm_model and model are deprecated input aliases.",
    )
    embedding_model: Optional[str] = None
    temperature: float = Field(default=0.5, ge=0, le=MAX_TEMPERATURE)
    max_new_tokens: int = Field(default=3000, ge=1, le=MAX_NEW_TOKENS)
    databricks: Optional[DatabricksCredentials] = None
    huggingface: Optional[HuggingFaceCredentials] = None


class ResolverOptions(ApiModel):
    semantic_threshold: float = Field(default=0.60, gt=0, le=1)
    semantic_target_margin: float = Field(default=0.16, ge=0, le=1)
    semantic_max_targets: int = Field(default=4, ge=1, le=MAX_SEMANTIC_TARGETS)
    top_k: int = Field(default=10, ge=1, le=MAX_TOP_K)
    label_threshold: float = Field(default=0.68, ge=0, le=1)
    strong_label_threshold: float = Field(default=0.86, ge=0, le=1)
    llm_fallback: bool = Field(
        default=True,
        validation_alias=AliasChoices("llm_fallback", "resolver_llm_fallback"),
        description="Enable constrained LLM fallback. resolver_llm_fallback is a deprecated input alias.",
    )
    wait_embeddings: bool = True
    embedding_timeout: int = Field(
        default=3600, ge=1, le=MAX_EMBEDDING_TIMEOUT_SECONDS
    )
    embedding_poll_seconds: float = Field(
        default=2.0, gt=0, le=MAX_EMBEDDING_POLL_SECONDS
    )
    strict_semantic: bool = False


class GenerationOptions(ApiModel):
    domain_context: str = ""
    generation_guidance: str = Field(
        default="",
        validation_alias=AliasChoices("generation_guidance", "guidance"),
        description="Optional SHACL generation guidance. guidance is a deprecated input alias.",
    )
    prefixes: str = ""
    base_namespace: Optional[str] = None
    shape_namespace: Optional[str] = None
    shape_prefix: str = "shape"
    llm_review: bool = Field(
        default=True,
        description=(
            "Run a second LLM pass that audits semantic coverage, cardinalities and "
            "ontology ranges before the existing SHACL validations."
        ),
    )
    review_max_attempts: int = Field(default=3, ge=1, le=5)


class ValidationProfile(ApiModel):
    name: str = "profile.ttl"
    content: str = Field(min_length=1)


class ValidationOptions(ApiModel):
    profiles: List[ValidationProfile] = Field(default_factory=list)


class ValidationResult(ApiModel):
    valid: bool
    syntax_valid: bool
    profile_valid: bool
    profile_count: int = 0
    profile_names: List[str] = Field(default_factory=list)
    generic_profile_active: bool = True
    generic_profile_name: str = "shacl-shacl.ttl"
    domain_profile_count: int = 0
    domain_profile_names: List[str] = Field(default_factory=list)
    validation_level: str
    error: Optional[str] = None
    error_type: str = "none"
    report_text: str = ""
    message: str = ""


class BaselineInput(ApiModel):
    name: str = "astrea.ttl"
    content: str = Field(min_length=1)


class AstreaOptions(ApiModel):
    mode: Literal[
        "none", "evidence", "merge", "evidence-and-merge", "baseline", "both"
    ] = Field(
        default="none",
        description="Astrea usage. baseline and both are deprecated input aliases.",
    )
    merge_strategy: Literal[
        "generated-priority", "restrictive", "priority-llm"
    ] = Field(
        default="generated-priority",
        validation_alias=AliasChoices(
            "merge_strategy", "merge_technique", "technique"
        ),
        description="Merge strategy. priority-llm, merge_technique and technique are deprecated input aliases.",
    )
    failure_policy: Literal["continue", "fail"] = "continue"
    baseline: Optional[BaselineInput] = None

    @field_validator("mode")
    @classmethod
    def canonical_mode(cls, value: str) -> str:
        return {"baseline": "evidence", "both": "evidence-and-merge"}.get(
            value, value
        )

    @field_validator("merge_strategy")
    @classmethod
    def canonical_merge_strategy(cls, value: str) -> str:
        return "generated-priority" if value == "priority-llm" else value


class EvidenceRecord(ExtensibleApiModel):
    source: str
    description: Optional[str] = None
    score: Optional[float] = None


class GenerationParameters(ApiModel):
    temperature: Optional[float] = None
    max_new_tokens: Optional[int] = Field(
        default=None,
        ge=1,
        le=MAX_NEW_TOKENS,
        validation_alias=AliasChoices("max_new_tokens", "max_tokens"),
    )
    shape_prefix: Optional[str] = None
    llm_review: Optional[bool] = None
    review_max_attempts: Optional[int] = Field(default=None, ge=1, le=5)


ScoreKind = Literal[
    "explicit", "lexical", "semantic_similarity", "llm_selected_candidate", "none"
]


class OperationMetadata(ApiModel):
    request_id: str
    operation: str
    service: str
    api_version: str
    deployment_profile: Literal["local", "public"]
    created_at: datetime
    duration_ms: float = Field(default=0, ge=0)
    warnings: List[str] = Field(default_factory=list)


class AuthoringProvenance(ApiModel):
    source_rule: Optional[BusinessRuleInput] = None
    selected_targets: List[OntologyTermReference] = Field(default_factory=list)
    target_roles: Optional[TargetRoles] = None
    resolved_by: Optional[Literal["index", "label", "semantic", "llm", "none"]] = None
    resolution_score: Optional[float] = Field(
        default=None,
        validation_alias=AliasChoices("resolution_score", "confidence"),
        description=(
            "Strategy-specific resolver score. It is not a calibrated probability and "
            "must only be compared with scores having the same score_kind."
        ),
    )
    score_kind: ScoreKind = "none"
    evidence: List[EvidenceRecord] = Field(default_factory=list)
    generation_model: Optional[str] = None
    embedding_model: Optional[str] = None
    inference_provider: Optional[Literal["databricks", "huggingface"]] = None
    generation_parameters: GenerationParameters = Field(default_factory=GenerationParameters)
    validation_profiles: List[str] = Field(default_factory=list)
    validation_results: List[ValidationResult] = Field(default_factory=list)
    baseline_usage: Optional[Literal["none", "evidence", "merge", "evidence-and-merge"]] = None
    baseline_source: Optional[str] = None
    merge_strategy: Optional[Literal["generated-priority", "restrictive"]] = None
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    created_at: datetime


class OperationalResponse(ApiModel):
    request_id: str
    operation_metadata: OperationMetadata


class AuthoringResponse(OperationalResponse):
    provenance: AuthoringProvenance


class ApiError(ApiModel):
    error: str
    code: str
    message: str
    request_id: str
    details: "ApiErrorDetails" = Field(default_factory=lambda: ApiErrorDetails())


class FieldIssue(ApiModel):
    location: List[str]
    message: str
    type: str


class ApiErrorDetails(ApiModel):
    issues: List[FieldIssue] = Field(default_factory=list)
    provider: Optional[str] = None
    job_id: Optional[str] = None
    status: Optional[str] = None
    upstream_status: Optional[int] = None
    retry_after_seconds: Optional[int] = Field(default=None, ge=0)
    model: Optional[str] = None
    limit: Optional[int] = Field(default=None, ge=0)
    limit_mb: Optional[int] = Field(default=None, ge=0)
    resource: Optional[str] = None


class SingleRuleTargetResolutionRequest(ApiModel):
    input_type: Literal["rule"]
    ontology: OntologyInput
    rule: BusinessRuleInput
    inference: InferenceOptions = Field(default_factory=InferenceOptions)
    resolver: ResolverOptions = Field(default_factory=ResolverOptions)


class BatchTargetResolutionRequest(ApiModel):
    input_type: Literal["batch"]
    ontology: OntologyInput
    batch: BusinessRulesBatchInput
    inference: InferenceOptions = Field(default_factory=InferenceOptions)
    resolver: ResolverOptions = Field(default_factory=ResolverOptions)


TargetResolutionInput = Annotated[
    Union[SingleRuleTargetResolutionRequest, BatchTargetResolutionRequest],
    Field(discriminator="input_type"),
]


class TargetResolutionRequest(RootModel[TargetResolutionInput]):
    pass


class RuleWorkflowRequest(ApiModel):
    ontology: OntologyInput
    rule: BusinessRuleInput
    inference: InferenceOptions = Field(default_factory=InferenceOptions)
    generation: GenerationOptions = Field(default_factory=GenerationOptions)
    resolver: ResolverOptions = Field(default_factory=ResolverOptions)
    validation: ValidationOptions = Field(default_factory=ValidationOptions)
    astrea: AstreaOptions = Field(default_factory=AstreaOptions)


class BatchWorkflowRequest(ApiModel):
    ontology: OntologyInput
    batch: BusinessRulesBatchInput
    inference: InferenceOptions = Field(default_factory=InferenceOptions)
    generation: GenerationOptions = Field(default_factory=GenerationOptions)
    resolver: ResolverOptions = Field(default_factory=ResolverOptions)
    validation: ValidationOptions = Field(default_factory=ValidationOptions)
    astrea: AstreaOptions = Field(default_factory=AstreaOptions)


class OntologyParseRequest(ApiModel):
    ontology: OntologyInput


class OntologySearchRequest(ApiModel):
    rule: BusinessRuleInput
    ontology_terms: List[OntologyTerm]
    ontology_hash: str = ""
    entity_types: List[Literal["class", "property"]] = Field(default_factory=list)
    top_k: int = Field(default=8, ge=1, le=MAX_TOP_K)
    inference: InferenceOptions = Field(default_factory=InferenceOptions)


class OntologyIndexCreateRequest(ApiModel):
    ontology_terms: List[OntologyTerm]
    ontology_hash: str = ""
    inference: InferenceOptions = Field(default_factory=InferenceOptions)


class ShapeBuildRequest(ApiModel):
    ontology: OntologyInput
    rule: BusinessRuleInput
    target_roles: TargetRoles
    inference: InferenceOptions = Field(default_factory=InferenceOptions)
    generation: GenerationOptions = Field(default_factory=GenerationOptions)
    validation: ValidationOptions = Field(default_factory=ValidationOptions)
    astrea: AstreaOptions = Field(default_factory=AstreaOptions)


class ShapeValidationRequest(ApiModel):
    shape_document: str = Field(min_length=1)
    prefixes: str = ""
    validation: ValidationOptions = Field(default_factory=ValidationOptions)


class AstreaBaselineRequest(ApiModel):
    ontology: OntologyInput


class ShapeMergeRequest(ApiModel):
    generated: BaselineInput
    baseline: BaselineInput
    merge_strategy: Literal[
        "generated-priority", "restrictive", "priority-llm"
    ] = Field(
        validation_alias=AliasChoices(
            "merge_strategy", "merge_technique", "technique"
        ),
        description="Merge strategy. priority-llm, merge_technique and technique are deprecated input aliases.",
    )
    validation: ValidationOptions = Field(default_factory=ValidationOptions)

    @field_validator("merge_strategy")
    @classmethod
    def canonical_merge_strategy(cls, value: str) -> str:
        return "generated-priority" if value == "priority-llm" else value


class BatchStreamRequest(BatchWorkflowRequest):
    pass


class ModelCheckRequest(ApiModel):
    inference_provider: Literal["databricks", "huggingface"]
    model_id: str = Field(min_length=1)
    role: Literal["chat", "embedding"] = "chat"
    inference: Optional[InferenceOptions] = None


class LocalModelRequest(ApiModel):
    model_id: str = Field(pattern=r"^[^/\s]+/[^/\s]+$")


class OntologyTermCandidate(ApiModel):
    entity_id: Optional[str] = None
    target: str
    iri: str
    full_iri: Optional[str] = None
    label: str
    type: Literal["class", "property"]
    kind: str
    domain: List[OntologyTermReference] = Field(default_factory=list)
    range: List[OntologyTermReference] = Field(default_factory=list)
    superclasses: List[OntologyTermReference] = Field(default_factory=list)
    score: Optional[float] = None
    reasons: List[str] = Field(default_factory=list)


class TargetResolutionItem(ApiModel):
    rule: BusinessRuleInput
    target_details: List[OntologyTermReference]
    target_roles: TargetRoles
    resolved_by: Literal["index", "label", "semantic", "llm", "none"]
    resolution_score: Optional[float] = Field(
        default=None,
        validation_alias=AliasChoices("resolution_score", "confidence"),
    )
    score_kind: ScoreKind
    candidates: List[OntologyTermCandidate] = Field(default_factory=list)
    signal_candidates: "SignalCandidates" = Field(default_factory=lambda: SignalCandidates())


class SignalCandidates(ApiModel):
    index: List[OntologyTermCandidate] = Field(default_factory=list)
    label: List[OntologyTermCandidate] = Field(default_factory=list)
    semantic: List[OntologyTermCandidate] = Field(default_factory=list)
    llm: List[OntologyTermCandidate] = Field(default_factory=list)


class ResolutionSummary(ApiModel):
    total: int
    index: int
    label: int
    semantic: int
    llm: int
    none: int
    without_llm: int
    without_llm_excluding_index: int


class TargetResolutionResponse(AuthoringResponse):
    rules: List[TargetResolutionItem]
    summary: ResolutionSummary
    ontology_namespace: Optional[str] = None
    ontology_term_count: int


class NamespaceCandidate(ApiModel):
    namespace: str
    term_count: int
    coverage: float
    prefixes: List[str]
    ontology_hint: bool


class NamespaceAnalysis(ApiModel):
    namespace: str
    detected_by: str
    term_count: int
    total_terms: int
    coverage: float
    confidence: float
    candidates: List[NamespaceCandidate]
    shape_namespace: str
    shape_namespace_source: str
    shape_prefix: str
    shape_prefix_source: str
    managed_prefixes: List[str]


class OntologyParseResponse(OperationalResponse):
    prefixes: str
    entities: List[OntologyTerm]
    base_namespace: str
    shape_namespace: str
    shape_prefix: str
    namespace_analysis: NamespaceAnalysis


class OntologySearchResponse(OperationalResponse):
    inference_provider: Optional[str] = None
    embedding_model: Optional[str] = None
    candidates: List["RankedOntologyTerm"] = Field(default_factory=list)
    method: str
    message: str


class RankedOntologyTerm(ApiModel):
    entity_id: str
    score: float
    reasons: List[str] = Field(default_factory=list)


JobStatus = Literal["queued", "running", "completed", "failed", "cancelled"]


class JobError(ApiModel):
    code: str
    message: str


class JobResponse(OperationalResponse):
    job_id: str
    status: JobStatus
    progress: float = Field(ge=0, le=1)
    message: str
    created_at: datetime
    updated_at: datetime
    error: Optional[JobError] = None


class EmbeddingIndexAcceptedResponse(JobResponse):
    completed_terms: int = 0
    total_terms: int = 0


class EmbeddingIndexStatusResponse(JobResponse):
    completed_terms: int = 0
    total_terms: int = 0


class SemanticReviewIssue(ApiModel):
    code: str
    message: str
    path: Optional[str] = None


class SemanticReviewResult(ApiModel):
    status: Literal["not_run", "passed", "failed"] = "not_run"
    critic_calls: int = Field(default=0, ge=0)
    correction_count: int = Field(default=0, ge=0)
    issues_found: int = Field(default=0, ge=0)
    issues: List[SemanticReviewIssue] = Field(default_factory=list)


class ShapeBuildResponse(AuthoringResponse):
    shape_document: str
    valid: bool
    attempts: int = 0
    hints: List[str] = Field(default_factory=list)
    fallback: bool = False
    not_found: bool = False
    error: Optional[str] = None
    error_type: str = "none"
    message: str = ""
    validation: Optional[ValidationResult] = None
    logs: str = ""
    inference_provider: Optional[str] = None
    generation_model: Optional[str] = None
    llm_review_applied: bool = False
    review_attempts: int = Field(default=0, ge=0)
    semantic_review: SemanticReviewResult = Field(
        default_factory=SemanticReviewResult
    )


class ShapeValidationResponse(AuthoringResponse, ValidationResult):
    pass


class AstreaBaselineResponse(AuthoringResponse):
    available: bool
    source: str
    name: str
    size: int
    ontology_hash: str
    shape_document: str
    shape_count: int
    validation: ValidationResult
    message: str


class MergeResult(ApiModel):
    merge_strategy: Literal["generated-priority", "restrictive"]
    triples: int
    warnings: List[str] = Field(default_factory=list)
    statistics: "MergeStatistics" = Field(default_factory=lambda: MergeStatistics())


class MergeStatistics(ApiModel):
    generated_shapes: int = 0
    astrea_shapes: int = 0
    astrea_fallback_shapes: int = 0
    generated_paths: int = 0
    astrea_fallback_paths: int = 0
    generated_target_classes: int = 0
    astrea_fallback_target_classes: int = 0
    merged_paths: int = 0
    merged_target_classes: int = 0


class ShapeMergeResponse(AuthoringResponse, ValidationResult):
    shape_document: str
    merge: MergeResult
    baseline_name: str
    merge_message: str


class WorkflowSummary(ApiModel):
    rules_total: int = 0
    rules_unresolved: int = 0
    targets_total: int = 0
    generated_total: int = 0
    valid: int = 0
    invalid: int = 0


class RuleResolutionOutcome(ApiModel):
    rule: BusinessRuleInput
    selected_targets: List[OntologyTermReference] = Field(default_factory=list)
    target_roles: TargetRoles = Field(default_factory=TargetRoles)
    resolved_by: Literal["index", "label", "semantic", "llm", "none"]
    resolution_score: Optional[float] = Field(
        default=None,
        validation_alias=AliasChoices("resolution_score", "confidence"),
    )
    score_kind: ScoreKind


class GeneratedShapeOutcome(ApiModel):
    rule_number: str
    rule_title: str
    selected_targets: List[OntologyTermReference] = Field(default_factory=list)
    target_roles: TargetRoles = Field(default_factory=TargetRoles)
    shape_document: str
    valid: bool
    attempts: int = 0
    error: Optional[str] = None
    error_type: str = "none"
    message: str = ""
    validation: Optional[ValidationResult] = None
    llm_review_applied: bool = False
    review_attempts: int = Field(default=0, ge=0)
    semantic_review: SemanticReviewResult = Field(
        default_factory=SemanticReviewResult
    )


class UnresolvedRuleOutcome(ApiModel):
    rule: BusinessRuleInput
    reason: str


class NamespaceSet(ApiModel):
    prefixes: str = ""
    base_namespace: str = ""
    shape_namespace: str = ""
    shape_prefix: str = "shape"


class AstreaStatus(ApiModel):
    requested_mode: Literal["none", "evidence", "merge", "evidence-and-merge"]
    effective_mode: Literal["none", "evidence", "merge", "evidence-and-merge"]
    failure_policy: Literal["continue", "fail"]
    available: Optional[bool] = None
    source: Optional[str] = None
    name: Optional[str] = None
    shape_count: Optional[int] = None
    validation: Optional[ValidationResult] = None
    error_type: Optional[str] = None
    message: str


class WorkflowMergeResult(ApiModel):
    shape_document: str
    validation: ValidationResult
    merge: MergeResult
    baseline_name: str
    message: str


class RuleWorkflowResponse(AuthoringResponse):
    workflow: Literal["rule-to-shape"]
    rule: RuleResolutionOutcome
    shape: Optional[GeneratedShapeOutcome] = None
    unresolved: bool
    unresolved_rules: List[UnresolvedRuleOutcome] = Field(default_factory=list)
    summary: WorkflowSummary
    namespaces: NamespaceSet = Field(default_factory=NamespaceSet)
    astrea: AstreaStatus
    merge: Optional[WorkflowMergeResult] = None
    final_shape_document: str
    logs: str = ""


class BatchWorkflowResponse(AuthoringResponse):
    workflow: Literal["batch-to-shapes"]
    summary: WorkflowSummary
    rules: List[RuleResolutionOutcome] = Field(default_factory=list)
    shapes: List[GeneratedShapeOutcome] = Field(default_factory=list)
    unresolved_rules: List[UnresolvedRuleOutcome] = Field(default_factory=list)
    namespaces: NamespaceSet = Field(default_factory=NamespaceSet)
    astrea: AstreaStatus
    merge: Optional[WorkflowMergeResult] = None
    final_shape_document: str
    logs: str = ""


class ModelCheckResponse(OperationalResponse):
    ok: bool
    message: str
    inference_provider: Optional[str] = None
    model_id: Optional[str] = None


class LocalModelStatusResponse(OperationalResponse):
    model_id: str
    downloaded: bool
    status: Literal["not-downloaded", "downloaded"]
    message: str


class ProviderCapability(ApiModel):
    enabled: bool
    execution: Literal["remote", "local"]
    message: Optional[str] = None


class ProductInfo(ApiModel):
    name: str
    title: str
    version: str


class DeploymentInfo(ApiModel):
    audience: str
    description: str


class ProviderCapabilities(ApiModel):
    databricks: ProviderCapability
    huggingface: ProviderCapability


class LogicalServiceRecord(ApiModel):
    service_id: str
    title: str
    responsibility: str


class EndpointCatalogRecord(ApiModel):
    operation: str
    method: str
    path: str
    legacy_path: Optional[str] = None
    service_id: Optional[str] = None
    role: str
    transport: str
    summary: str
    description: str = ""
    success_status: int


class RuntimeEndpointMap(ApiModel):
    capabilities: str
    parse: str
    prepare_terms: str
    resolve_rule: str
    build: str
    validate_endpoint: str = Field(alias="validate", serialization_alias="validate")
    astrea: str
    merge: str
    validate_model: str
    local_model_status: str
    download_local_model: str
    batch: str


class ApiCatalogResponse(ApiModel):
    product: ProductInfo
    version: str
    prefix: str
    services: List[LogicalServiceRecord]
    endpoints: List[EndpointCatalogRecord]
    service_layout: Literal["unified", "split"]
    runtime_endpoints: RuntimeEndpointMap


class CapabilitiesResponse(OperationalResponse):
    application: ProductInfo
    deployment_profile: Literal["local", "public"]
    deployment: DeploymentInfo
    repository_url: str
    providers: ProviderCapabilities
    api: ApiCatalogResponse


class HealthResponse(OperationalResponse):
    status: Literal["ok"]
    version: str
    deployment_profile: Literal["local", "public"]


class ApiRootResponse(OperationalResponse):
    name: str
    description: str
    version: str
    api_version: str
    docs: str
    redoc: str
    openapi: str
    capabilities: str
    health: str
    workflows: "WorkflowLinks"
    documentation: str
    api_documentation: str
    repository: str


class WorkflowLinks(ApiModel):
    rule_to_shape: str
    batch_to_shapes: str
    batch_stream: str


class SseEventBase(ApiModel):
    request_id: str
    sequence: int = Field(ge=1)
    timestamp: datetime
    operation_metadata: OperationMetadata
    provenance: Optional[AuthoringProvenance] = None
    extensions: Dict[str, JsonValue] = Field(default_factory=dict)


class StartedEvent(SseEventBase):
    event: Literal["started"]
    message: str
    total_items: int = Field(ge=0)
    total_rules: Optional[int] = Field(default=None, ge=0)


class ProgressEvent(SseEventBase):
    event: Literal["progress"]
    message: str
    completed_items: int = Field(ge=0)
    total_items: int = Field(ge=0)
    progress: float = Field(ge=0, le=1)
    completed_rules: Optional[int] = Field(default=None, ge=0)
    total_rules: Optional[int] = Field(default=None, ge=0)


class RuleResolvedEvent(SseEventBase):
    event: Literal["rule_resolved"]
    rule: BusinessRuleInput
    target_roles: TargetRoles
    resolved_by: Literal["index", "label", "semantic", "llm", "none"]
    resolution_score: Optional[float] = None
    score_kind: ScoreKind


class ShapeGeneratedEvent(SseEventBase):
    event: Literal["shape_generated"]
    rule: BusinessRuleInput
    target: Optional[OntologyTermReference]
    target_index: int = Field(ge=0)
    target_total: int = Field(ge=0)
    shape_document: str
    valid: bool
    attempts: int = Field(default=0, ge=0)
    llm_review_applied: bool = False
    review_attempts: int = Field(default=0, ge=0)
    semantic_review: SemanticReviewResult = Field(
        default_factory=SemanticReviewResult
    )
    error_type: str = "none"
    message: str = ""


class ValidationCompletedEvent(SseEventBase):
    event: Literal["validation_completed"]
    rule_number: str
    target: Optional[OntologyTermReference]
    validation: ValidationResult


class WarningEvent(SseEventBase):
    event: Literal["warning"]
    code: str
    message: str


class CompletedEvent(SseEventBase):
    event: Literal["completed"]
    message: str
    completed_items: int = Field(ge=0)
    total_items: int = Field(ge=0)
    completed_rules: Optional[int] = Field(default=None, ge=0)
    total_rules: Optional[int] = Field(default=None, ge=0)
    final_shape_document: Optional[str] = None


class FailedEvent(SseEventBase):
    event: Literal["failed"]
    error: ApiError


class HeartbeatEvent(SseEventBase):
    event: Literal["heartbeat"]
    message: str


SseEventDocument = Annotated[
    Union[
        StartedEvent,
        ProgressEvent,
        RuleResolvedEvent,
        ShapeGeneratedEvent,
        ValidationCompletedEvent,
        WarningEvent,
        CompletedEvent,
        FailedEvent,
        HeartbeatEvent,
    ],
    Field(discriminator="event"),
]


class SseEvent(RootModel[SseEventDocument]):
    pass


REQUEST_MODELS = {
    "workflows.rule.generate": RuleWorkflowRequest,
    "workflows.batch.generate": BatchWorkflowRequest,
    "ontology.parse": OntologyParseRequest,
    "ontology.search": OntologySearchRequest,
    "ontology.index.create": OntologyIndexCreateRequest,
    "rules.resolve-targets": TargetResolutionRequest,
    "shapes.build": ShapeBuildRequest,
    "shapes.validate": ShapeValidationRequest,
    "baselines.astrea.generate": AstreaBaselineRequest,
    "shapes.merge": ShapeMergeRequest,
    "batches.generate": BatchStreamRequest,
    "models.check": ModelCheckRequest,
    "models.local.status": LocalModelRequest,
    "models.local.download.create": LocalModelRequest,
}


RESPONSE_MODELS = {
    "system.root": ApiRootResponse,
    "ontology.parse": OntologyParseResponse,
    "ontology.search": OntologySearchResponse,
    "ontology.index.create": EmbeddingIndexAcceptedResponse,
    "ontology.index.get": EmbeddingIndexStatusResponse,
    "ontology.index.delete": EmbeddingIndexStatusResponse,
    "rules.resolve-targets": TargetResolutionResponse,
    "shapes.build": ShapeBuildResponse,
    "shapes.validate": ShapeValidationResponse,
    "baselines.astrea.generate": AstreaBaselineResponse,
    "shapes.merge": ShapeMergeResponse,
    "workflows.rule.generate": RuleWorkflowResponse,
    "workflows.batch.generate": BatchWorkflowResponse,
    "models.check": ModelCheckResponse,
    "models.local.status": LocalModelStatusResponse,
    "models.local.download.create": JobResponse,
    "models.local.download.get": JobResponse,
    "models.local.download.delete": JobResponse,
    "system.capabilities": CapabilitiesResponse,
    "system.health": HealthResponse,
}


PUBLIC_MODELS = tuple(dict.fromkeys([
    ApiError,
    OntologyInput,
    BusinessRuleInput,
    BusinessRulesBatchInput,
    OntologyTermReference,
    OntologyTerm,
    TargetRoles,
    InferenceOptions,
    ResolverOptions,
    GenerationOptions,
    ValidationProfile,
    ValidationOptions,
    ValidationResult,
    BaselineInput,
    AstreaOptions,
    MergeResult,
    OperationMetadata,
    AuthoringProvenance,
    SingleRuleTargetResolutionRequest,
    BatchTargetResolutionRequest,
    TargetResolutionRequest,
    RuleWorkflowRequest,
    BatchWorkflowRequest,
    OntologyParseRequest,
    OntologySearchRequest,
    OntologyIndexCreateRequest,
    ShapeBuildRequest,
    ShapeValidationRequest,
    AstreaBaselineRequest,
    ShapeMergeRequest,
    BatchStreamRequest,
    ModelCheckRequest,
    LocalModelRequest,
    OntologyParseResponse,
    OntologySearchResponse,
    EmbeddingIndexAcceptedResponse,
    EmbeddingIndexStatusResponse,
    TargetResolutionResponse,
    ShapeBuildResponse,
    ShapeValidationResponse,
    AstreaBaselineResponse,
    ShapeMergeResponse,
    RuleWorkflowResponse,
    BatchWorkflowResponse,
    ModelCheckResponse,
    LocalModelStatusResponse,
    CapabilitiesResponse,
    HealthResponse,
    ApiRootResponse,
    JobResponse,
    StartedEvent,
    ProgressEvent,
    RuleResolvedEvent,
    ShapeGeneratedEvent,
    ValidationCompletedEvent,
    WarningEvent,
    CompletedEvent,
    FailedEvent,
    HeartbeatEvent,
    SseEvent,
]))


def model_schema(model: type[BaseModel]) -> Dict[str, Any]:
    """Return a JSON Schema 2020-12 fragment for one public model."""
    return model.model_json_schema(ref_template="#/components/schemas/{model}")


def validate_request(operation: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate a canonical request and return its normalized Python form."""
    model = REQUEST_MODELS.get(operation)
    if model is None:
        return dict(payload)
    instance = model.model_validate(payload)
    if isinstance(instance, RootModel):
        return instance.root.model_dump(mode="python", exclude_none=True)
    return instance.model_dump(mode="python", exclude_none=True)


def validate_request_consistency(payload: Mapping[str, Any]) -> None:
    """Reject cross-field configurations that are valid JSON but inconsistent."""
    resolver = payload.get("resolver")
    if not isinstance(resolver, Mapping):
        return
    semantic_threshold = float(resolver.get("semantic_threshold", 0.60))
    semantic_target_margin = float(resolver.get("semantic_target_margin", 0.16))
    if semantic_target_margin >= semantic_threshold:
        from shard.api.errors import InvalidBusinessInput

        raise InvalidBusinessInput(
            "semantic_target_margin must be lower than semantic_threshold.",
            code="INVALID_RESOLVER_CONFIGURATION",
        )
    label_threshold = float(resolver.get("label_threshold", 0.68))
    strong_label_threshold = float(resolver.get("strong_label_threshold", 0.86))
    if strong_label_threshold < label_threshold:
        from shard.api.errors import InvalidBusinessInput

        raise InvalidBusinessInput(
            "strong_label_threshold must be greater than or equal to label_threshold.",
            code="INVALID_RESOLVER_CONFIGURATION",
        )


def _flat_inference(target: Dict[str, Any], inference: Mapping[str, Any]) -> None:
    if not inference:
        return
    target["provider"] = inference.get("provider")
    target["model"] = inference.get("generation_model")
    target["llm_model"] = inference.get("generation_model")
    target["embedding_model"] = inference.get("embedding_model")
    target["temperature"] = inference.get("temperature")
    target["max_new_tokens"] = inference.get("max_new_tokens")
    target["inference_config"] = dict(inference)


def to_application_payload(operation: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Translate one validated canonical request to existing application fields."""
    request = dict(payload)
    ontology = request.get("ontology") or {}
    rule = request.get("rule") or {}
    batch = request.get("batch") or {}
    generation = request.get("generation") or {}
    resolver = request.get("resolver") or {}
    validation = request.get("validation") or {}
    astrea = request.get("astrea") or {}
    inference = request.get("inference") or {}

    flat: Dict[str, Any] = {}
    if ontology:
        flat.update(ontology_content=ontology.get("content"), ontology_filename=ontology.get("filename"))
    if rule:
        flat.update(
            business_rule=rule.get("text"),
            rule_number=rule.get("number"),
            rule_title=rule.get("title"),
        )
    if batch:
        flat.update(
            batch_content=batch.get("content"),
            batch_filename=batch.get("filename"),
            batch_format=batch.get("format"),
        )
    flat.update(generation)
    flat.update(resolver)
    if "llm_fallback" in flat:
        flat["resolver_llm_fallback"] = flat.pop("llm_fallback")
    flat["validation_profiles"] = validation.get("profiles") or []
    if astrea:
        mode = astrea.get("mode", "none")
        flat["astrea_use_mode"] = {
            "evidence": "baseline",
            "evidence-and-merge": "both",
        }.get(mode, mode)
        strategy = astrea.get("merge_strategy", "generated-priority")
        flat["astrea_merge_technique"] = (
            "priority-llm" if strategy == "generated-priority" else strategy
        )
        flat["astrea_failure_policy"] = astrea.get("failure_policy", "continue")
        flat["astrea_baseline"] = astrea.get("baseline")
    _flat_inference(flat, inference)

    if operation == "ontology.parse":
        return {"filename": ontology.get("filename"), "content": ontology.get("content")}
    if operation == "ontology.search":
        flat.update(
            business_rule=rule.get("text"),
            ontology_terms=_terms_to_application(request.get("ontology_terms") or []),
            ontology_hash=request.get("ontology_hash", ""),
            entity_types=request.get("entity_types") or [],
            top_k=request.get("top_k", 8),
        )
    if operation == "ontology.index.create":
        flat.update(
            ontology_terms=_terms_to_application(request.get("ontology_terms") or []),
            ontology_hash=request.get("ontology_hash", ""),
        )
    if operation == "shapes.validate":
        flat.update(
            shape=request.get("shape_document", ""),
            prefixes=request.get("prefixes", ""),
        )
    if operation == "baselines.astrea.generate":
        return {"ontology_filename": ontology.get("filename"), "ontology_content": ontology.get("content")}
    if operation == "shapes.merge":
        generated = request.get("generated") or {}
        baseline = request.get("baseline") or {}
        strategy = request.get("merge_strategy")
        flat.update(
            generated_shapes=generated.get("content"),
            generated_filename=generated.get("name"),
            astrea_baseline=baseline,
            technique="priority-llm" if strategy == "generated-priority" else strategy,
        )
    if operation == "models.check":
        model_check = {
            "provider": request.get("inference_provider"),
            "model": request.get("model_id"),
            "role": request.get("role", "chat"),
        }
        if request.get("inference"):
            model_check["inference_config"] = dict(request["inference"])
        return model_check
    if operation.startswith("models.local."):
        return {"model": request.get("model_id")}
    if operation == "shapes.build":
        flat["target_roles"] = _target_roles_to_application(request.get("target_roles") or {})
        for role_name in ("focus_nodes", "constraint_paths", "related_terms"):
            if flat["target_roles"].get(role_name):
                flat["target"] = flat["target_roles"][role_name][0]
                break
    return {key: value for key, value in flat.items() if value is not None}


def _reference_to_application(value: Mapping[str, Any]) -> str:
    return str(value.get("iri") or "")


def _target_roles_to_application(roles: Mapping[str, Any]) -> Dict[str, List[Dict[str, str]]]:
    return {
        name: [
            {"iri": _reference_to_application(item), "label": str(item.get("label") or "")}
            for item in roles.get(name) or []
        ]
        for name in ("focus_nodes", "constraint_paths", "related_terms")
    }


def _terms_to_application(terms: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    result = []
    for term in terms:
        item = dict(term)
        for name in ("domain", "range"):
            refs = item.get(name) or []
            item[name] = _reference_to_application(refs[0]) if refs else ""
        item["superclasses"] = [
            _reference_to_application(ref) for ref in item.get("superclasses") or []
        ]
        item["ontologyNote"] = item.pop("ontology_note", "")
        item.pop("annotations", None)
        result.append(item)
    return result


def _as_reference(value: Any) -> Optional[Dict[str, str]]:
    if isinstance(value, str):
        text = value.strip()
        return {"iri": text} if text and text != "—" else None
    item = value if isinstance(value, Mapping) else {}
    iri = str(item.get("iri") or item.get("target") or item.get("full_iri") or "").strip()
    if not iri:
        return None
    label = str(item.get("label") or "").strip()
    return {"iri": iri, **({"label": label} if label else {})}


def _reference_list(value: Any) -> List[Dict[str, str]]:
    values = value if isinstance(value, list) else [value]
    return [reference for item in values if (reference := _as_reference(item))]


def canonical_ontology_term(term: Mapping[str, Any]) -> Dict[str, Any]:
    """Convert one internal catalog row to the stable public term model."""
    annotations = dict(term.get("annotations") or {})
    former_rule = str(term.get("business_rule") or term.get("businessRule") or "")
    former_rules = [str(item) for item in term.get("rules") or []]
    if former_rule:
        annotations.setdefault("former_rule", former_rule)
    if former_rules:
        annotations.setdefault("former_rules", former_rules)
    return {
        "id": str(term.get("id") or ""),
        "iri": str(term.get("iri") or term.get("full_iri") or ""),
        "full_iri": str(term.get("full_iri") or term.get("iri") or ""),
        "label": str(term.get("label") or ""),
        "type": term.get("type"),
        "kind": str(term.get("kind") or ""),
        "domain": _reference_list(term.get("domain")),
        "range": _reference_list(term.get("range")),
        "superclasses": _reference_list(term.get("superclasses") or []),
        "comment": str(term.get("comment") or ""),
        "ontology_note": str(term.get("ontology_note") or term.get("ontologyNote") or ""),
        "annotations": annotations,
    }


def score_kind_for_resolution(resolved_by: Any) -> ScoreKind:
    """Return the non-probabilistic score scale used by one resolver strategy."""
    return {
        "index": "explicit",
        "label": "lexical",
        "semantic": "semantic_similarity",
        "llm": "llm_selected_candidate",
        "none": "none",
    }.get(str(resolved_by or "none"), "none")


def _canonical_candidate(candidate: Mapping[str, Any]) -> Dict[str, Any]:
    target = str(candidate.get("target") or candidate.get("iri") or "")
    return {
        "entity_id": candidate.get("entity_id"),
        "target": target,
        "iri": str(candidate.get("iri") or target),
        "full_iri": candidate.get("full_iri"),
        "label": str(candidate.get("label") or target),
        "type": candidate.get("type") or "property",
        "kind": str(candidate.get("kind") or "Property"),
        "domain": _reference_list(candidate.get("domain")),
        "range": _reference_list(candidate.get("range")),
        "superclasses": _reference_list(candidate.get("superclasses") or []),
        "score": candidate.get("score"),
        "reasons": [str(item) for item in candidate.get("reasons") or []],
    }


def _canonical_validation(payload: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "valid": bool(payload.get("valid")),
        "syntax_valid": bool(payload.get("syntax_valid", payload.get("valid"))),
        "profile_valid": bool(payload.get("profile_valid", payload.get("valid"))),
        "profile_count": int(payload.get("profile_count") or 0),
        "profile_names": [str(item) for item in payload.get("profile_names") or []],
        "generic_profile_active": bool(payload.get("generic_profile_active", True)),
        "generic_profile_name": str(payload.get("generic_profile_name") or "shacl-shacl.ttl"),
        "domain_profile_count": int(payload.get("domain_profile_count") or 0),
        "domain_profile_names": [str(item) for item in payload.get("domain_profile_names") or []],
        "validation_level": str(payload.get("validation_level") or "syntax+generic"),
        "error": payload.get("error"),
        "error_type": str(payload.get("error_type") or "none"),
        "report_text": str(payload.get("report_text") or ""),
        "message": str(payload.get("message") or ""),
    }


def _canonical_astrea_status(value: Mapping[str, Any]) -> Dict[str, Any]:
    def mode(item: Any) -> str:
        normalized = str(item or "none")
        return {"baseline": "evidence", "both": "evidence-and-merge"}.get(normalized, normalized)

    validation = value.get("validation")
    return {
        "requested_mode": mode(value.get("requested_mode")),
        "effective_mode": mode(value.get("effective_mode")),
        "failure_policy": str(value.get("failure_policy") or "continue"),
        "available": value.get("available"),
        "source": value.get("source"),
        "name": value.get("name"),
        "shape_count": value.get("shape_count"),
        "validation": _canonical_validation(validation) if isinstance(validation, Mapping) else None,
        "error_type": value.get("error_type"),
        "message": str(value.get("message") or ""),
    }


def _canonical_resolution_outcome(row: Mapping[str, Any]) -> Dict[str, Any]:
    resolution = row.get("resolution") or {}
    roles = {
        "focus_nodes": _reference_list(resolution.get("focus_nodes") or []),
        "constraint_paths": _reference_list(resolution.get("constraint_paths") or []),
        "related_terms": _reference_list(resolution.get("related_terms") or []),
    }
    resolved_by = resolution.get("resolved_by") or "none"
    return {
        "rule": {
            "number": str(row.get("rule_number") or "RULE-001"),
            "title": str(row.get("title") or "Data constraint"),
            "text": str(row.get("text") or ""),
        },
        "selected_targets": _reference_list(resolution.get("targets") or []),
        "target_roles": roles,
        "resolved_by": resolved_by,
        "resolution_score": resolution.get("resolution_score", resolution.get("confidence")),
        "score_kind": score_kind_for_resolution(resolved_by),
    }


def _canonical_shape_outcome(row: Mapping[str, Any]) -> Dict[str, Any]:
    validation_fields = {name for name in ValidationResult.model_fields if name in row}
    return {
        "rule_number": str(row.get("rule_number") or "RULE-001"),
        "rule_title": str(row.get("rule_title") or row.get("title") or "Data constraint"),
        "selected_targets": _reference_list(row.get("targets") or []),
        "target_roles": {
            "focus_nodes": _reference_list(row.get("focus_nodes") or []),
            "constraint_paths": _reference_list(row.get("constraint_paths") or []),
            "related_terms": _reference_list(row.get("related_terms") or []),
        },
        "shape_document": str(row.get("shape") or row.get("shape_document") or ""),
        "valid": bool(row.get("valid")),
        "attempts": int(row.get("attempts") or 0),
        "error": row.get("error"),
        "error_type": str(row.get("error_type") or "none"),
        "message": str(row.get("message") or ""),
        "validation": _canonical_validation(row) if validation_fields else None,
        "llm_review_applied": bool(row.get("llm_review_applied")),
        "review_attempts": int(row.get("review_attempts") or 0),
        "semantic_review": row.get("semantic_review") or {},
    }


def _canonical_unresolved(row: Mapping[str, Any]) -> Dict[str, Any]:
    reason = "Rule could not be resolved to ontology terms."
    if row.get("missing_targets"):
        reason = "Resolved ontology terms were missing from the catalog."
    return {
        "rule": {
            "number": str(row.get("rule_number") or "RULE-001"),
            "title": str(row.get("title") or "Data constraint"),
            "text": str(row.get("text") or ""),
        },
        "reason": reason,
    }


def _canonical_workflow_merge(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, Mapping):
        return None
    merged = canonicalize_success("shapes.merge", value)
    return {
        "shape_document": merged.get("shape_document", ""),
        "validation": {
            key: merged[key] for key in ValidationResult.model_fields if key in merged
        },
        "merge": merged.get("merge") or {
            "merge_strategy": "generated-priority",
            "triples": 0,
        },
        "baseline_name": merged.get("baseline_name", "astrea.ttl"),
        "message": merged.get("merge_message", ""),
    }


def canonicalize_success(operation: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Convert an application result to canonical response field names."""
    result = dict(payload)
    if operation == "ontology.parse":
        result["entities"] = [canonical_ontology_term(item) for item in result.get("entities") or []]
    elif operation == "ontology.search":
        result = {
            "inference_provider": result.get("provider"),
            "embedding_model": result.get("embedding_model") or result.get("model"),
            "candidates": [
                {
                    "entity_id": str(item.get("entity_id") or ""),
                    "score": float(item.get("score") or 0),
                    "reasons": [str(reason) for reason in item.get("reasons") or []],
                }
                for item in result.get("candidates") or []
            ],
            "method": str(result.get("method") or "none"),
            "message": str(result.get("message") or ""),
        }
    elif operation == "rules.resolve-targets":
        rules = []
        for row in result.get("rules") or []:
            roles = row.get("role_details") or {
                name: row.get(name) or []
                for name in ("focus_nodes", "constraint_paths", "related_terms")
            }
            target_roles = {
                name: _reference_list(roles.get(name) or [])
                for name in ("focus_nodes", "constraint_paths", "related_terms")
            }
            signals = {
                str(name): [_canonical_candidate(item) for item in items or []]
                for name, items in (row.get("signal_candidates") or {}).items()
            }
            resolved_by = row.get("resolved_by") or "none"
            rules.append({
                "rule": {
                    "number": str(row.get("rule_number") or "RULE-001"),
                    "title": str(row.get("title") or "Data constraint"),
                    "text": str(row.get("text") or ""),
                },
                "target_details": _reference_list(row.get("target_details") or row.get("targets") or []),
                "target_roles": target_roles,
                "resolved_by": resolved_by,
                "resolution_score": row.get("resolution_score", row.get("confidence")),
                "score_kind": score_kind_for_resolution(resolved_by),
                "candidates": [_canonical_candidate(item) for item in row.get("candidates") or []],
                "signal_candidates": signals,
            })
        ontology = result.get("ontology") or {}
        result = {
            "rules": rules,
            "summary": result.get("summary") or {},
            "ontology_namespace": ontology.get("base_namespace"),
            "ontology_term_count": int(ontology.get("term_count") or 0),
        }
    elif operation == "shapes.build":
        validation_keys = {field for field in ValidationResult.model_fields if field in result}
        validation = _canonical_validation(result) if validation_keys else None
        result = {
            "shape_document": str(result.get("shape") or result.get("shape_document") or ""),
            "valid": bool(result.get("valid")),
            "attempts": int(result.get("attempts") or 0),
            "hints": [str(item) for item in result.get("hints") or []],
            "fallback": bool(result.get("fallback")),
            "not_found": bool(result.get("not_found")),
            "error": result.get("error"),
            "error_type": str(result.get("error_type") or "none"),
            "message": str(result.get("message") or ""),
            "validation": validation,
            "logs": str(result.get("logs") or ""),
            "inference_provider": result.get("provider"),
            "generation_model": result.get("model"),
            "llm_review_applied": bool(result.get("llm_review_applied")),
            "review_attempts": int(result.get("review_attempts") or 0),
            "semantic_review": result.get("semantic_review") or {},
        }
    elif operation == "shapes.validate":
        result = _canonical_validation(result)
    elif operation == "baselines.astrea.generate":
        result = {
            "available": bool(result.get("available")),
            "source": str(result.get("source") or "astrea-api"),
            "name": str(result.get("name") or "astrea.ttl"),
            "size": int(result.get("size") or 0),
            "ontology_hash": str(result.get("ontology_hash") or ""),
            "shape_document": str(result.get("shape_document") or ""),
            "shape_count": int(result.get("shape_count") or 0),
            "validation": _canonical_validation(result.get("validation") or {}),
            "message": str(result.get("message") or ""),
        }
    elif operation == "shapes.merge":
        strategy = str(result.get("technique") or result.get("merge_strategy") or "restrictive")
        if strategy == "priority-llm":
            strategy = "generated-priority"
        details = result.get("details") or result
        result = {
            **_canonical_validation(result),
            "shape_document": str(result.get("shape_document") or ""),
            "merge": {
                "merge_strategy": strategy,
                "triples": int(result.get("triples") or details.get("triples") or 0),
                "warnings": [str(item) for item in result.get("warnings") or details.get("warnings") or []],
                "statistics": {
                    str(key): int(value)
                    for key, value in (result.get("stats") or details.get("stats") or {}).items()
                },
            },
            "baseline_name": str(result.get("astrea_baseline_name") or "astrea.ttl"),
            "merge_message": str(result.get("merge_message") or ""),
        }
    elif operation == "workflows.batch.generate":
        generation = result.get("generation") or {}
        result = {
            "workflow": "batch-to-shapes",
            "summary": result.get("summary") or generation.get("summary") or {},
            "rules": [_canonical_resolution_outcome(item) for item in generation.get("rules") or []],
            "shapes": [_canonical_shape_outcome(item) for item in generation.get("shapes") or []],
            "unresolved_rules": [
                _canonical_unresolved(item) for item in generation.get("unresolved_rules") or []
            ],
            "namespaces": {
                "prefixes": str(generation.get("prefixes") or ""),
                "base_namespace": str(generation.get("base_namespace") or ""),
                "shape_namespace": str(generation.get("shape_namespace") or ""),
                "shape_prefix": str(generation.get("shape_prefix") or "shape"),
            },
            "astrea": _canonical_astrea_status(result.get("astrea") or {}),
            "merge": _canonical_workflow_merge(result.get("merge")),
            "final_shape_document": str(result.get("final_shape_document") or ""),
            "logs": str(result.get("logs") or ""),
        }
    elif operation == "workflows.rule.generate":
        raw_rule = result.get("rule") or {}
        result = {
            "workflow": "rule-to-shape",
            "rule": _canonical_resolution_outcome(raw_rule),
            "shape": _canonical_shape_outcome(result["shape"]) if result.get("shape") else None,
            "unresolved": bool(result.get("unresolved")),
            "unresolved_rules": [
                _canonical_unresolved(item) for item in result.get("unresolved_rules") or []
            ],
            "summary": result.get("summary") or {},
            "namespaces": result.get("namespaces") or {},
            "astrea": _canonical_astrea_status(result.get("astrea") or {}),
            "merge": _canonical_workflow_merge(result.get("merge")),
            "final_shape_document": str(result.get("final_shape_document") or ""),
            "logs": str(result.get("logs") or ""),
        }
    elif operation == "models.check":
        result = {
            "ok": bool(result.get("ok")),
            "message": str(result.get("message") or ""),
            "inference_provider": result.get("provider"),
            "model_id": result.get("model"),
        }
    elif operation == "models.local.status":
        status = result.get("status") or "not-downloaded"
        if status == "ready":
            status = "downloaded"
        result = {
            "model_id": str(result.get("model") or result.get("model_id") or ""),
            "downloaded": bool(result.get("downloaded")),
            "status": status,
            "message": str(result.get("message") or ""),
        }
    return {key: value for key, value in result.items() if value is not None}


def validate_response(operation: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate and serialize one canonical success response."""
    model = RESPONSE_MODELS.get(operation)
    if model is None:
        return dict(payload)
    return model.model_validate(payload).model_dump(
        mode="json", exclude_none=True, by_alias=True
    )


def enrich_provenance(
    operation: str,
    provenance: Mapping[str, Any],
    result: Mapping[str, Any],
) -> Dict[str, Any]:
    """Add result-derived grounding, validation and baseline facts."""
    record = dict(provenance)
    if operation == "rules.resolve-targets" and result.get("rules"):
        row = result["rules"][0]
        record.update({
            "source_rule": row.get("rule"),
            "selected_targets": row.get("target_details") or [],
            "target_roles": row.get("target_roles"),
            "resolved_by": row.get("resolved_by"),
            "resolution_score": row.get("resolution_score"),
            "score_kind": row.get("score_kind", "none"),
            "evidence": [
                {
                    "source": str(row.get("resolved_by") or "resolver"),
                    "description": "; ".join(candidate.get("reasons") or []),
                    "score": candidate.get("score"),
                }
                for candidate in row.get("candidates") or []
            ],
        })
    if operation == "workflows.rule.generate" and isinstance(result.get("rule"), Mapping):
        row = result["rule"]
        record.update({
            "source_rule": row.get("rule"),
            "selected_targets": row.get("selected_targets") or [],
            "target_roles": row.get("target_roles"),
            "resolved_by": row.get("resolved_by"),
            "resolution_score": row.get("resolution_score"),
            "score_kind": row.get("score_kind", "none"),
        })
    if operation == "workflows.batch.generate":
        selected = []
        seen = set()
        validations = []
        for row in result.get("rules") or []:
            for target in row.get("selected_targets") or []:
                iri = str(target.get("iri") or "")
                if iri and iri not in seen:
                    seen.add(iri)
                    selected.append(target)
        for shape in result.get("shapes") or []:
            if isinstance(shape.get("validation"), Mapping):
                validations.append(dict(shape["validation"]))
        record["selected_targets"] = selected
        record["validation_results"] = validations
    validation = None
    if operation == "shapes.validate":
        validation = result
    elif isinstance(result.get("validation"), Mapping):
        validation = result.get("validation")
    if validation:
        record["validation_results"] = [dict(validation)]
    astrea = result.get("astrea")
    if isinstance(astrea, Mapping):
        record["baseline_usage"] = astrea.get("effective_mode") or record.get("baseline_usage")
        if astrea.get("source"):
            record["baseline_source"] = astrea.get("source")
    if operation == "baselines.astrea.generate":
        record["baseline_source"] = str(result.get("source") or "astrea-api")
    if operation == "shapes.merge" and isinstance(result.get("merge"), Mapping):
        record["merge_strategy"] = result["merge"].get("merge_strategy")
        record["warnings"] = list(result["merge"].get("warnings") or [])
    return {key: value for key, value in record.items() if value is not None}
