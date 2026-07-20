"""HTTP client for generating baseline SHACL shapes with Astrea."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import httpx
from rdflib import Graph
from rdflib.namespace import RDF, SH


DEFAULT_ASTREA_API_URL = "https://astrea.linkeddata.es/api/shacl/document"
ASTREA_API_URL_ENV = "SHARD_ASTREA_API_URL"
ASTREA_TIMEOUT_ENV = "SHARD_ASTREA_TIMEOUT"
DEFAULT_ASTREA_TIMEOUT = 120.0


class AstreaUnavailableError(RuntimeError):
    """Raised when the configured Astrea service cannot be reached."""


class AstreaResponseError(RuntimeError):
    """Raised when Astrea returns an unusable response."""


def _service_url(endpoint: Optional[str]) -> str:
    return str(endpoint or os.environ.get(ASTREA_API_URL_ENV) or DEFAULT_ASTREA_API_URL).strip()


def _timeout_seconds(timeout: Optional[float]) -> float:
    value = timeout if timeout is not None else os.environ.get(ASTREA_TIMEOUT_ENV)
    if value in (None, ""):
        return DEFAULT_ASTREA_TIMEOUT
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{ASTREA_TIMEOUT_ENV} must be a number of seconds.") from exc
    if parsed <= 0:
        raise ValueError(f"{ASTREA_TIMEOUT_ENV} must be greater than zero.")
    return parsed


def _post_document(
    client: httpx.Client,
    endpoint: str,
    ontology_turtle: str,
    timeout: float,
) -> httpx.Response:
    return client.post(
        endpoint,
        json={"ontology": ontology_turtle, "serialisation": "TURTLE"},
        headers={"Accept": "text/turtle"},
        timeout=timeout,
    )


def generate_astrea_shapes(
    ontology_turtle: str,
    *,
    endpoint: Optional[str] = None,
    timeout: Optional[float] = None,
    client: Optional[httpx.Client] = None,
) -> Dict[str, Any]:
    """Generate and parse Astrea shapes for an in-memory Turtle ontology."""
    if not str(ontology_turtle or "").strip():
        raise ValueError("Missing ontology content for Astrea generation.")

    service_url = _service_url(endpoint)
    timeout_seconds = _timeout_seconds(timeout)
    owns_client = client is None
    active_client = client or httpx.Client(follow_redirects=True)
    try:
        try:
            response = _post_document(
                active_client,
                service_url,
                ontology_turtle,
                timeout_seconds,
            )
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            raise AstreaUnavailableError(
                "Astrea is currently unavailable or did not respond in time."
            ) from exc
        except httpx.HTTPError as exc:
            raise AstreaUnavailableError(
                "Astrea could not be reached."
            ) from exc
    finally:
        if owns_client:
            active_client.close()

    if response.status_code in {408, 425, 429} or response.status_code >= 500:
        raise AstreaUnavailableError(
            f"Astrea is currently unavailable (HTTP {response.status_code})."
        )
    if not response.is_success:
        raise AstreaResponseError(
            f"Astrea rejected the ontology (HTTP {response.status_code})."
        )

    document = response.text.strip()
    if not document:
        raise AstreaResponseError("Astrea returned an empty response.")

    graph = Graph(bind_namespaces="none")
    try:
        graph.parse(data=document, format="turtle")
    except Exception as exc:
        raise AstreaResponseError(
            f"Astrea returned invalid Turtle: {exc}"
        ) from exc

    node_shapes = set(graph.subjects(RDF.type, SH.NodeShape))
    property_shapes = set(graph.subjects(RDF.type, SH.PropertyShape))
    if not node_shapes and not property_shapes:
        raise AstreaResponseError(
            "Astrea did not return any SHACL NodeShape or PropertyShape."
        )

    shape_document = graph.serialize(format="turtle").strip()
    return {
        "shape_document": shape_document,
        "shape_count": len(node_shapes | property_shapes),
        "node_shape_count": len(node_shapes),
        "property_shape_count": len(property_shapes),
        "triple_count": len(graph),
        "partial": response.status_code == 206,
        "service_url": service_url,
    }
