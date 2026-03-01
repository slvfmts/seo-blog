"""Pre-publish meta field validation (warn-only)."""


def validate_meta_before_publish(draft) -> list[str]:
    """Warn-only pre-publish validation of SEO meta fields. Returns list of warnings."""
    warnings = []
    mt = draft.meta_title
    if not mt:
        warnings.append("meta_title is missing")
    elif not (30 <= len(mt) <= 60):
        warnings.append(f"meta_title length {len(mt)} outside 30-60 range")
    md = draft.meta_description
    if not md:
        warnings.append("meta_description is missing")
    elif not (80 <= len(md) <= 160):
        warnings.append(f"meta_description length {len(md)} outside 80-160 range")
    if not draft.slug:
        warnings.append("slug is missing")
    return warnings
