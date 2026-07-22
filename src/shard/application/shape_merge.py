"""Combine reviewed generated shapes with an ontology-derived baseline."""

from shard.application.shape_validation import (
    validate_shape_content,
    validation_profiles_from_payload,
)

def merge_shapes(payload):
    """Merge final generated shapes with an Astrea baseline."""
    from shard.baselines import baseline_from_payload, merge_shape_documents

    generated = str(
        payload.get("generated_shapes")
        or payload.get("generated_content")
        or payload.get("shape_document")
        or ""
    )
    if not generated.strip():
        raise ValueError("Missing generated SHACL content to merge.")
    astrea_content, astrea_filename = baseline_from_payload(payload, purpose="merge")
    if not astrea_content.strip():
        raise ValueError("Generate an Astrea baseline before using a merge strategy.")

    technique = str(
        payload.get("merge_strategy")
        or payload.get("technique")
        or payload.get("merge_mode")
        or ""
    ).strip().lower()
    merged = merge_shape_documents(
        astrea_content,
        generated,
        technique,
        astrea_filename=astrea_filename,
        generated_filename=str(payload.get("generated_filename") or "shard_shapes.ttl"),
    )
    validation = validate_shape_content(
        merged["shape_document"],
        "",
        validation_profiles_from_payload(payload),
    )
    return {
        **merged,
        **validation,
        "astrea_baseline_name": astrea_filename,
        "merge_message": (
            f"Merged generated shapes with '{astrea_filename}' using {technique}."
        ),
    }
