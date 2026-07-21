"""Resolve data constraints to auditable, role-grouped ontology terms.

The workflow parses a
Data Constraints template, parses the uploaded ontology into the existing catalog
shape, and resolves each rule to focus nodes, constrained paths and related
terms without generating SHACL.

"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any, Dict, Iterable, List, Optional

from shard.application.ontology_catalog import parse_ontology
from shard.domain.business_rules import BusinessRule, parse_business_rules
from shard.domain.limits import MAX_SEMANTIC_TARGETS, MAX_TOP_K
from shard.observability import logger

DEFAULT_TOP_K = 10
DEFAULT_LABEL_THRESHOLD = 0.68
DEFAULT_STRONG_LABEL_THRESHOLD = 0.86
DEFAULT_SEMANTIC_THRESHOLD = 0.60
DEFAULT_SEMANTIC_TARGET_MARGIN = 0.16
DEFAULT_SEMANTIC_MAX_TARGETS = 4

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "each", "every",
    "for", "from", "have", "has", "in", "is", "it", "its", "must", "of",
    "on", "one", "or", "shall", "the", "to", "with",
}

_LEADING_RELATION_WORDS = {
    "has", "have", "is", "are", "was", "were", "requires", "require",
    "required", "produces", "produce", "assigned", "assigns", "assign",
    "inspects", "inspect", "inspected", "located", "locates", "links", "link",
    "linked",
}


@dataclass
class RuleResolution:
    """Auditable resolution of one data constraint to role-grouped ontology terms."""

    rule_number: str
    targets: List[str]
    resolved_by: str
    confidence: Optional[float]
    candidates: List[Dict[str, Any]]
    signal_candidates: Dict[str, List[Dict[str, Any]]]
    focus_nodes: List[str] = field(default_factory=list)
    constraint_paths: List[str] = field(default_factory=list)
    related_terms: List[str] = field(default_factory=list)


def _split_camel(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value or "")
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", value)
    return value


def _normalise_text(value: str) -> str:
    value = _split_camel(str(value or ""))
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _stem(token: str) -> str:
    token = token.lower()
    irregular = {
        "located": "locate",
        "locates": "locate",
        "linked": "link",
        "links": "link",
        "inspected": "inspect",
        "inspects": "inspect",
        "assigned": "assign",
        "assigns": "assign",
        "produces": "produce",
        "required": "require",
        "requires": "require",
    }
    if token in irregular:
        return irregular[token]
    for suffix in ("ing", "ed", "es", "s"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _tokens(value: str, *, keep_stopwords: bool = False) -> List[str]:
    tokens = [_stem(t) for t in _normalise_text(value).split()]
    if keep_stopwords:
        return [t for t in tokens if t]
    return [t for t in tokens if t and t not in _STOPWORDS]


def _contains_phrase(normalised_text: str, alias: str) -> bool:
    alias = _normalise_text(alias)
    if not alias:
        return False
    return f" {alias} " in f" {normalised_text} "


def _match_location(alias_norm: str, title_norm: str, body_norm: str) -> str:
    in_title = _contains_phrase(title_norm, alias_norm)
    in_body = _contains_phrase(body_norm, alias_norm)
    if in_title and in_body:
        return "title+text"
    if in_title:
        return "title"
    if in_body:
        return "text"
    return "combined text"


def _overlap_location(overlap: set[str], title_tokens: set[str], body_tokens: set[str]) -> str:
    if not overlap:
        return "combined text"
    in_title = bool(overlap & title_tokens)
    in_body = bool(overlap & body_tokens)
    if in_title and in_body:
        return "title+text"
    if in_title:
        return "title"
    if in_body:
        return "text"
    return "combined text"


def _local_name(term: Dict[str, Any]) -> str:
    iri = str(term.get("iri") or term.get("full_iri") or "")
    if iri.startswith("<") and iri.endswith(">"):
        iri = iri[1:-1]
    local = iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]
    if ":" in local:
        local = local.split(":", 1)[1]
    return local


def _term_target(term: Dict[str, Any]) -> str:
    return str(term.get("iri") or term.get("full_iri") or term.get("id") or "")


def _term_refs(term: Dict[str, Any]) -> set[str]:
    return {
        str(value)
        for value in (
            term.get("id"),
            term.get("iri"),
            term.get("full_iri"),
            term.get("label"),
        )
        if value
    }


def _append_unique(values: List[str], value: Any) -> None:
    text = str(value or "").strip()
    if text and text not in values:
        values.append(text)


def _classify_target_roles(
    targets: List[str],
    ontology_terms: List[Dict[str, Any]],
) -> Dict[str, List[str]]:
    """Classify resolved terms by their role in one logical rule constraint."""
    lookup: Dict[str, Dict[str, Any]] = {}
    for term in ontology_terms:
        for ref in _term_refs(term):
            lookup[ref] = term

    selected = [lookup.get(str(target)) for target in targets]
    selected = [term for term in selected if term]
    properties = [term for term in selected if term.get("type") == "property"]
    classes = [term for term in selected if term.get("type") == "class"]

    domain_refs = {
        str(term.get("domain"))
        for term in properties
        if term.get("domain") and str(term.get("domain")) != "—"
    }
    range_refs = {
        str(term.get("range"))
        for term in properties
        if term.get("range") and str(term.get("range")) != "—"
    }
    selected_superclass_refs = {
        str(superclass)
        for term in classes
        for superclass in (term.get("superclasses") or [])
        if superclass
    }

    focus_nodes: List[str] = []
    constraint_paths: List[str] = []
    related_terms: List[str] = []

    for term in properties:
        _append_unique(constraint_paths, _term_target(term))

    for term in classes:
        refs = _term_refs(term)
        target = _term_target(term)
        if refs & selected_superclass_refs:
            _append_unique(related_terms, target)
        elif refs & domain_refs:
            _append_unique(focus_nodes, target)
        elif refs & range_refs:
            _append_unique(related_terms, target)
        else:
            _append_unique(focus_nodes, target)

    # A property carries enough ontology evidence to recover its domain class,
    # even when semantic ranking selected only the property itself.
    for domain_ref in domain_refs:
        domain_term = lookup.get(domain_ref)
        if domain_term and domain_term.get("type") == "class":
            domain_target = _term_target(domain_term)
            if domain_target not in related_terms:
                _append_unique(focus_nodes, domain_target)

    assigned = set(focus_nodes) | set(constraint_paths) | set(related_terms)
    for target in targets:
        if str(target) not in assigned:
            _append_unique(related_terms, target)

    return {
        "focus_nodes": focus_nodes,
        "constraint_paths": constraint_paths,
        "related_terms": related_terms,
    }


def _rule_resolution(
    *,
    rule: BusinessRule,
    targets: List[str],
    resolved_by: str,
    confidence: Optional[float],
    candidates: List[Dict[str, Any]],
    signal_candidates: Dict[str, List[Dict[str, Any]]],
    ontology_terms: List[Dict[str, Any]],
) -> RuleResolution:
    roles = _classify_target_roles(targets, ontology_terms)
    return RuleResolution(
        rule_number=rule.number,
        targets=targets,
        resolved_by=resolved_by,
        confidence=confidence,
        candidates=candidates,
        signal_candidates=signal_candidates,
        **roles,
    )


def _term_aliases(term: Dict[str, Any]) -> List[tuple[str, str]]:
    aliases: List[tuple[str, str]] = []
    label = str(term.get("label") or "").strip()
    local = _local_name(term)
    if label:
        aliases.append((label, "label"))
    if local and _normalise_text(local) != _normalise_text(label):
        aliases.append((_split_camel(local), "local-name"))

    # For relation-like property names such as hasLifecycleStatus, keep a
    # second alias without the leading verb. This is domain-agnostic and useful
    # when rules say "lifecycle status" rather than "has lifecycle status".
    for alias, source in list(aliases):
        parts = _tokens(alias, keep_stopwords=True)
        remainder = parts[1:] if len(parts) > 1 and parts[0] in _LEADING_RELATION_WORDS else []
        if len(remainder) >= 2:
            aliases.append((" ".join(remainder), f"{source}-without-leading-relation"))
    return aliases


def _candidate_dict(term: Dict[str, Any], score: float, reasons: Iterable[str]) -> Dict[str, Any]:
    return {
        "entity_id": term.get("id"),
        "target": _term_target(term),
        "iri": term.get("iri"),
        "full_iri": term.get("full_iri"),
        "label": term.get("label"),
        "type": term.get("type"),
        "kind": term.get("kind"),
        "domain": term.get("domain"),
        "range": term.get("range"),
        "superclasses": list(term.get("superclasses") or []),
        "score": round(float(score), 3),
        "reasons": list(reasons),
    }


def _merge_candidate(
    candidates: Dict[str, Dict[str, Any]],
    term: Dict[str, Any],
    score: float,
    reason: str,
) -> None:
    target = _term_target(term)
    if not target:
        return
    existing = candidates.get(target)
    if existing is None or score > existing["score"]:
        candidates[target] = _candidate_dict(term, score, [reason])
    elif reason not in existing["reasons"]:
        existing["reasons"].append(reason)


def _label_candidates(rule: BusinessRule, ontology_terms: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rule_text = f"{rule.title}\n{rule.text}"
    title_norm = _normalise_text(rule.title)
    body_norm = _normalise_text(rule.text)
    normalised_rule = _normalise_text(rule_text)
    rule_tokens = set(_tokens(rule_text))
    title_tokens = set(_tokens(rule.title))
    body_tokens = set(_tokens(rule.text))
    candidates: Dict[str, Dict[str, Any]] = {}

    for term in ontology_terms:
        for alias, alias_source in _term_aliases(term):
            alias_norm = _normalise_text(alias)
            alias_tokens = _tokens(alias)
            if not alias_norm or not alias_tokens:
                continue

            if _contains_phrase(normalised_rule, alias_norm):
                base = 0.78 + min(0.16, 0.04 * len(alias_tokens))
                if alias_source == "label":
                    base += 0.03
                if term.get("type") == "property":
                    base += 0.02
                _merge_candidate(
                    candidates,
                    term,
                    min(0.98, base),
                    f"exact {alias_source} phrase in {_match_location(alias_norm, title_norm, body_norm)}: {alias_norm}",
                )
                continue

            overlap = set(alias_tokens) & rule_tokens
            if len(alias_tokens) >= 2 and overlap:
                ratio = len(overlap) / len(set(alias_tokens))
                if ratio >= 0.67:
                    score = 0.62 + min(0.18, 0.12 * ratio)
                    if term.get("type") == "property":
                        score += 0.03
                    _merge_candidate(
                        candidates,
                        term,
                        min(0.85, score),
                        f"{alias_source} token overlap in {_overlap_location(overlap, title_tokens, body_tokens)}: {', '.join(sorted(overlap))}",
                    )

    # Extra deterministic signal from ontology notes/comments. This is still
    # ontology-catalog evidence, not model inference, and helps relation labels
    # that are paraphrased in the data constraint.
    for term in ontology_terms:
        note_tokens = set(_tokens(" ".join(str(term.get(k, "")) for k in ("label", "ontologyNote", "comment"))))
        overlap = note_tokens & rule_tokens
        if len(overlap) < 2:
            continue
        if term.get("type") == "property" and not _property_domain_and_range_mentioned(term, rule_tokens):
            continue
        score = min(0.78, 0.54 + 0.05 * len(overlap))
        if term.get("type") == "property":
            score += 0.04
        _merge_candidate(
            candidates,
            term,
            score,
            f"ontology note/comment overlap in {_overlap_location(overlap, title_tokens, body_tokens)}: {', '.join(sorted(overlap)[:5])}",
        )

    return sorted(candidates.values(), key=lambda item: item["score"], reverse=True)


def _side_tokens(value: str) -> set[str]:
    text = str(value or "")
    if not text or text == "—":
        return set()
    local = text.rsplit("#", 1)[-1].rsplit("/", 1)[-1]
    if ":" in local:
        local = local.split(":", 1)[1]
    return set(_tokens(local))


def _property_domain_and_range_mentioned(term: Dict[str, Any], rule_tokens: set[str]) -> bool:
    domain = _side_tokens(term.get("domain", ""))
    range_tokens = _side_tokens(term.get("range", ""))
    domain_ok = bool(domain and domain <= rule_tokens)
    range_ok = bool(range_tokens and range_tokens <= rule_tokens)
    return domain_ok and range_ok


def _targets_from_candidates(
    candidates: List[Dict[str, Any]],
    threshold: float,
    *,
    max_targets: int = 8,
    score_margin: float = 0.22,
) -> List[str]:
    if not candidates:
        return []
    best = candidates[0]["score"]
    floor = max(threshold, best - score_margin)
    targets = []
    for candidate in candidates:
        if candidate["score"] < floor:
            continue
        target = str(candidate.get("target") or "")
        if target and target not in targets:
            targets.append(target)
        if len(targets) >= max_targets:
            break
    return targets


def _normalise_target_ref(value: Any) -> str:
    value = str(value or "").strip()
    if not value or value == "—":
        return ""
    return value


def _candidate_refs(candidate: Dict[str, Any]) -> set[str]:
    refs = set()
    for key in ("target", "full_iri", "label"):
        ref = _normalise_target_ref(candidate.get(key))
        if ref:
            refs.add(ref)
    return refs


def _ref_matches_candidate(ref: Any, candidate: Dict[str, Any]) -> bool:
    ref = _normalise_target_ref(ref)
    return bool(ref and ref in _candidate_refs(candidate))


def _candidates_domain_range_related(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    if a.get("type") == "property" and b.get("type") == "class":
        return _ref_matches_candidate(a.get("domain"), b) or _ref_matches_candidate(a.get("range"), b)
    if b.get("type") == "property" and a.get("type") == "class":
        return _ref_matches_candidate(b.get("domain"), a) or _ref_matches_candidate(b.get("range"), a)
    return False


def _semantic_targets_from_candidates(
    candidates: List[Dict[str, Any]],
    threshold: float,
    *,
    max_targets: int = DEFAULT_SEMANTIC_MAX_TARGETS,
    score_margin: float = DEFAULT_SEMANTIC_TARGET_MARGIN,
) -> List[str]:
    if not candidates:
        return []
    primary = candidates[0]
    best = float(primary.get("score", 0))
    if best < threshold:
        return []

    floor = max(threshold, best - score_margin)
    targets = []
    primary_target = str(primary.get("target") or "")
    if primary_target:
        targets.append(primary_target)

    for candidate in candidates[1:]:
        score = float(candidate.get("score", 0))
        if score < floor:
            continue
        if not _candidates_domain_range_related(primary, candidate):
            continue

        target = str(candidate.get("target") or "")
        if target and target not in targets:
            targets.append(target)
        if len(targets) >= max_targets:
            break
    return targets


def _index_candidates(rule: BusinessRule, ontology_terms: List[Dict[str, Any]], index_map: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not index_map or not rule.number or rule.number not in index_map:
        return []
    value = index_map[rule.number]
    requested = value if isinstance(value, list) else [value]
    lookup: Dict[str, Dict[str, Any]] = {}
    for term in ontology_terms:
        for key in (term.get("iri"), term.get("full_iri"), term.get("label"), term.get("id")):
            if key:
                lookup[str(key)] = term

    candidates = []
    for target in requested:
        term = lookup.get(str(target))
        if term:
            candidates.append(_candidate_dict(term, 1.0, ["explicit index_map"]))
    return candidates


def _coerce_ranked_candidates(
    ranked: Iterable[Dict[str, Any]],
    ontology_terms: List[Dict[str, Any]],
    default_reason: str,
) -> List[Dict[str, Any]]:
    terms_by_key: Dict[str, Dict[str, Any]] = {}
    for term in ontology_terms:
        for key in (term.get("id"), term.get("iri"), term.get("full_iri"), term.get("label")):
            if key:
                terms_by_key[str(key)] = term

    candidates: List[Dict[str, Any]] = []
    for item in ranked or []:
        lookup_key = (
            item.get("entity_id")
            or item.get("target")
            or item.get("iri")
            or item.get("full_iri")
            or item.get("label")
        )
        term = terms_by_key.get(str(lookup_key))
        if not term:
            continue
        raw_score = float(item.get("score", 0))
        score = raw_score / 100.0 if raw_score > 1.0 else raw_score
        candidates.append(_candidate_dict(
            term,
            score,
            item.get("reasons") or [default_reason],
        ))
    return sorted(candidates, key=lambda item: item["score"], reverse=True)


def _semantic_candidates(
    rule: BusinessRule,
    ontology_terms: List[Dict[str, Any]],
    payload: Dict[str, Any],
    top_k: int,
    semantic_ranker: Any = None,
) -> List[Dict[str, Any]]:
    if callable(semantic_ranker):
        try:
            ranked = semantic_ranker(rule, ontology_terms, payload, top_k)
            return _coerce_ranked_candidates(ranked, ontology_terms, "semantic ranker")
        except Exception as exc:
            logger.warn(f"Injected semantic rule-context ranker failed: {exc}")
            return []

    try:
        from shard.application.term_ranking import (
            _normalise_embedding_model_id,
            rank_semantic,
        )
    except Exception as exc:
        logger.warn(f"Could not import semantic ranker: {exc}")
        return []

    model_id = _normalise_embedding_model_id(
        payload,
        payload.get("embedding_model") or "system.ai.qwen3-embedding-0-6b",
    )
    try:
        ranked = rank_semantic(
            f"{rule.title}\n{rule.text}",
            ontology_terms,
            model_id,
            ontology_hash=payload.get("ontology_hash", ""),
            allowed_types=set(payload.get("entity_types") or []),
            top_k=top_k,
            payload=payload,
        )
    except Exception as exc:
        if payload.get("strict_semantic"):
            raise
        logger.warn(f"Semantic rule-context resolution unavailable: {exc}")
        return []

    return _coerce_ranked_candidates(ranked, ontology_terms, "semantic similarity")


def _llm_candidate_pool(
    rule: BusinessRule,
    ontology_terms: List[Dict[str, Any]],
    label_hits: List[Dict[str, Any]],
    semantic_hits: List[Dict[str, Any]],
    top_k: int,
) -> List[Dict[str, Any]]:
    limit = max(top_k, 25)
    pool: Dict[str, Dict[str, Any]] = {}
    for candidate in list(label_hits or []) + list(semantic_hits or []):
        target = str(candidate.get("target") or "")
        if target and target not in pool:
            pool[target] = candidate

    try:
        from shard.application.term_ranking import rank_lexical
        ranked = rank_lexical(
            f"{rule.title}\n{rule.text}",
            ontology_terms,
            allowed_types=None,
            top_k=limit,
        )
        lexical = _coerce_ranked_candidates(ranked, ontology_terms, "llm prefilter: lexical ranker")
        for candidate in lexical:
            target = str(candidate.get("target") or "")
            if target and target not in pool:
                pool[target] = candidate
    except Exception as exc:
        logger.warn(f"Could not build lexical LLM candidate pool: {exc}")

    broad = sorted(ontology_terms, key=lambda term: (str(term.get("type", "")), str(term.get("label", ""))))
    for term in broad:
        target = _term_target(term)
        if target and target not in pool:
            pool[target] = _candidate_dict(term, 0.01, ["llm broad candidate pool"])
        if len(pool) >= limit:
            break
    return list(pool.values())[:limit]


def _llm_candidates(
    rule: BusinessRule,
    candidates: List[Dict[str, Any]],
    llm: Any = None,
) -> List[Dict[str, Any]]:
    """Optional LLM target choice. Only callable LLM hooks are used here."""
    if not callable(llm) or not candidates:
        return []
    allowed = [c["target"] for c in candidates if c.get("target")]
    prompt_payload = {
        "rule_number": rule.number,
        "title": rule.title,
        "text": rule.text,
        "allowed_targets": allowed,
        "allowed_candidates": [
            {
                "target": c.get("target"),
                "label": c.get("label"),
                "type": c.get("type"),
                "kind": c.get("kind"),
                "domain": c.get("domain"),
                "range": c.get("range"),
                "score": c.get("score"),
                "reasons": c.get("reasons") or [],
            }
            for c in candidates
            if c.get("target")
        ],
    }
    try:
        selected = llm(prompt_payload)
    except Exception as exc:
        logger.warn(f"LLM rule-context fallback failed: {exc}")
        return []
    if isinstance(selected, str):
        selected = [selected]
    selected_set = {str(item) for item in selected or []}
    out = []
    for candidate in candidates:
        if candidate.get("target") in selected_set:
            clone = dict(candidate)
            clone["score"] = max(0.75, float(clone.get("score", 0)))
            clone["reasons"] = list(clone.get("reasons") or []) + ["llm selected from allowed list"]
            out.append(clone)
    return out


def resolve_rule_target(
    rule: BusinessRule,
    ontology_terms: List[Dict[str, Any]],
    index_map: Optional[Dict[str, Any]] = None,
    llm: Any = None,
    *,
    semantic_payload: Optional[Dict[str, Any]] = None,
    semantic_ranker: Any = None,
    label_threshold: float = DEFAULT_LABEL_THRESHOLD,
    strong_label_threshold: float = DEFAULT_STRONG_LABEL_THRESHOLD,
    semantic_threshold: float = DEFAULT_SEMANTIC_THRESHOLD,
    semantic_target_margin: float = DEFAULT_SEMANTIC_TARGET_MARGIN,
    semantic_max_targets: int = DEFAULT_SEMANTIC_MAX_TARGETS,
    top_k: int = DEFAULT_TOP_K,
) -> RuleResolution:
    """Resolve one data constraint to ontology terms and roles using auditable signals."""
    index_hits = _index_candidates(rule, ontology_terms, index_map or {})
    if index_hits:
        return _rule_resolution(
            rule=rule,
            targets=[c["target"] for c in index_hits],
            resolved_by="index",
            confidence=1.0,
            candidates=index_hits[:top_k],
            signal_candidates={"index": index_hits[:top_k], "label": [], "semantic": [], "llm": []},
            ontology_terms=ontology_terms,
        )

    label_hits = _label_candidates(rule, ontology_terms)
    if label_hits and label_hits[0]["score"] >= strong_label_threshold:
        targets = _targets_from_candidates(label_hits, label_threshold)
        return _rule_resolution(
            rule=rule,
            targets=targets,
            resolved_by="label",
            confidence=label_hits[0]["score"],
            candidates=label_hits[:top_k],
            signal_candidates={"index": [], "label": label_hits[:top_k], "semantic": [], "llm": []},
            ontology_terms=ontology_terms,
        )

    semantic_hits = _semantic_candidates(rule, ontology_terms, semantic_payload or {}, top_k, semantic_ranker=semantic_ranker)
    if semantic_hits and semantic_hits[0]["score"] >= semantic_threshold:
        targets = _semantic_targets_from_candidates(
            semantic_hits,
            semantic_threshold,
            max_targets=semantic_max_targets,
            score_margin=semantic_target_margin,
        )
        return _rule_resolution(
            rule=rule,
            targets=targets,
            resolved_by="semantic",
            confidence=semantic_hits[0]["score"],
            candidates=semantic_hits[:top_k],
            signal_candidates={"index": [], "label": label_hits[:top_k], "semantic": semantic_hits[:top_k], "llm": []},
            ontology_terms=ontology_terms,
        )

    fallback_pool = _llm_candidate_pool(rule, ontology_terms, label_hits, semantic_hits, top_k)
    llm_hits = _llm_candidates(rule, fallback_pool, llm=llm)
    if llm_hits:
        return _rule_resolution(
            rule=rule,
            targets=[c["target"] for c in llm_hits],
            resolved_by="llm",
            confidence=llm_hits[0]["score"],
            candidates=llm_hits[:top_k],
            signal_candidates={
                "index": [],
                "label": label_hits[:top_k],
                "semantic": semantic_hits[:top_k],
                "llm": llm_hits[:top_k],
            },
            ontology_terms=ontology_terms,
        )

    if label_hits and label_hits[0]["score"] >= label_threshold:
        targets = _targets_from_candidates(label_hits, label_threshold)
        return _rule_resolution(
            rule=rule,
            targets=targets,
            resolved_by="label",
            confidence=label_hits[0]["score"],
            candidates=label_hits[:top_k],
            signal_candidates={"index": [], "label": label_hits[:top_k], "semantic": semantic_hits[:top_k], "llm": []},
            ontology_terms=ontology_terms,
        )

    candidates = (label_hits or semantic_hits)[:top_k]
    return _rule_resolution(
        rule=rule,
        targets=[],
        resolved_by="none",
        confidence=None,
        candidates=candidates,
        signal_candidates={"index": [], "label": label_hits[:top_k], "semantic": semantic_hits[:top_k], "llm": []},
        ontology_terms=ontology_terms,
    )


def _runtime_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    return payload.get("inference_config") or payload.get("model_config") or {}


def _target_details(
    targets: List[str],
    candidates: List[Dict[str, Any]],
    ontology_terms: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    by_target = {str(candidate.get("target")): candidate for candidate in candidates}
    for term in ontology_terms:
        target = _term_target(term)
        if target and target not in by_target:
            by_target[target] = _candidate_dict(term, 0, ["target detail lookup"])

    details = []
    for target in targets:
        candidate = by_target.get(str(target), {})
        details.append({
            "target": target,
            "type": candidate.get("type"),
            "kind": candidate.get("kind"),
            "label": candidate.get("label"),
        })
    return details


def resolve_template(
    payload: Dict[str, Any],
    *,
    semantic_ranker: Any = None,
    llm: Any = None,
) -> Dict[str, Any]:
    """Parse ontology + rules and return a role-aware resolution table."""
    ontology_content = payload.get("ontology_content") or ""
    batch_content = payload.get("batch_content") or ""
    business_rule = payload.get("business_rule") or ""
    if not ontology_content:
        raise ValueError("Missing ontology_content.")
    if not batch_content and not business_rule:
        raise ValueError("Missing batch_content or business_rule.")

    ontology = parse_ontology(
        payload.get("ontology_filename") or "ontology.ttl",
        ontology_content,
    )
    ontology_terms = ontology.get("entities") or []
    if batch_content:
        rules = parse_business_rules(
            batch_content,
            filename=payload.get("batch_filename") or "business_rules_template",
        )
    else:
        rules = [BusinessRule(
            number=str(payload.get("rule_number") or ""),
            title=str(payload.get("rule_title") or ""),
            text=str(business_rule),
            source_format="interactive",
            raw=str(business_rule),
        )]
    top_k = max(1, min(MAX_TOP_K, int(payload.get("top_k", DEFAULT_TOP_K))))
    label_threshold = float(payload.get("label_threshold", DEFAULT_LABEL_THRESHOLD))
    strong_label_threshold = float(payload.get("strong_label_threshold", DEFAULT_STRONG_LABEL_THRESHOLD))
    semantic_threshold = float(payload.get("semantic_threshold", DEFAULT_SEMANTIC_THRESHOLD))
    semantic_target_margin = float(payload.get("semantic_target_margin", DEFAULT_SEMANTIC_TARGET_MARGIN))
    semantic_max_targets = max(
        1,
        min(
            MAX_SEMANTIC_TARGETS,
            int(payload.get("semantic_max_targets", DEFAULT_SEMANTIC_MAX_TARGETS)),
        ),
    )
    semantic_payload = {
        **payload,
        "ontology_terms": ontology_terms,
        "inference_config": _runtime_config(payload),
    }

    rows = []
    summary = {"total": len(rules), "index": 0, "label": 0, "semantic": 0, "llm": 0, "none": 0}
    for rule in rules:
        resolution = resolve_rule_target(
            rule,
            ontology_terms,
            index_map=payload.get("index_map") or None,
            llm=llm,
            semantic_payload=semantic_payload,
            semantic_ranker=semantic_ranker,
            label_threshold=label_threshold,
            strong_label_threshold=strong_label_threshold,
            semantic_threshold=semantic_threshold,
            semantic_target_margin=semantic_target_margin,
            semantic_max_targets=semantic_max_targets,
            top_k=top_k,
        )
        summary[resolution.resolved_by] = summary.get(resolution.resolved_by, 0) + 1
        role_details = {
            "focus_nodes": _target_details(resolution.focus_nodes, resolution.candidates, ontology_terms),
            "constraint_paths": _target_details(resolution.constraint_paths, resolution.candidates, ontology_terms),
            "related_terms": _target_details(resolution.related_terms, resolution.candidates, ontology_terms),
        }
        rows.append({
            "rule_number": rule.number,
            "title": rule.title,
            "text": rule.text,
            "source_format": rule.source_format,
            "target_details": _target_details(resolution.targets, resolution.candidates, ontology_terms),
            "role_details": role_details,
            **asdict(resolution),
        })

    summary["without_llm"] = summary.get("index", 0) + summary.get("label", 0) + summary.get("semantic", 0)
    summary["without_llm_excluding_index"] = summary.get("label", 0) + summary.get("semantic", 0)
    return {
        "rules": rows,
        "summary": summary,
        "ontology": {
            "base_namespace": ontology.get("base_namespace"),
            "term_count": len(ontology_terms),
        },
    }
