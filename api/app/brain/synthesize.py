"""Compile a summary from accepted memories only.

The compiled text is the grounding record. A model narrative, when requested,
is stored separately and is not treated as a source memory.
"""

from __future__ import annotations

_LABELS = (
    ("identity", "Identity"),
    ("education", "Education"),
    ("interests", "Interests"),
    ("projects", "Projects"),
    ("experiences", "Experiences"),
    ("goals", "Goals"),
    ("preferences", "Preferences"),
    ("skills", "Skills"),
    ("relationships", "Relationships"),
    ("other", "Other"),
)


def compile_summary(memories: list[dict]) -> dict:
    accepted = [memory for memory in memories if memory.get("status", "accepted") == "accepted"]
    if not accepted:
        return {
            "text": "No accepted memories yet. This summary will not invent a biography.",
            "sections": {},
            "uncertainties": ["Nothing has been accepted."],
            "accepted_count": 0,
            "basis": "accepted memories",
        }
    sections: dict[str, list[str]] = {}
    for memory in accepted:
        sections.setdefault(memory.get("category") or "other", []).append(memory["content"])
    paragraphs = ["This summary uses only accepted memories."]
    for key, label in _LABELS:
        items = sections.get(key)
        if items:
            paragraphs.append(f"{label}: " + " ".join(items))
    uncertainties: list[str] = []
    if "identity" not in sections:
        uncertainties.append("No accepted identity notes.")
    if "goals" not in sections:
        uncertainties.append("No accepted goals.")
    if "projects" not in sections and "interests" in sections:
        uncertainties.append("Interests are recorded, and no projects are accepted yet.")
    inferred = [memory["content"] for memory in accepted if memory.get("stance") != "explicit"]
    if inferred:
        uncertainties.append("Accepted inferences, not stated facts: " + " ".join(inferred[:3]))
    if uncertainties:
        paragraphs.append("Uncertainty: " + " ".join(uncertainties))
    return {
        "text": "\n\n".join(paragraphs),
        "sections": sections,
        "uncertainties": uncertainties,
        "accepted_count": len(accepted),
        "basis": "accepted memories",
    }
