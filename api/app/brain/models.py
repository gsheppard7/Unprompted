"""Shared literals and the extraction schema.

Model confidence is not stored. A stance is a label, and the numeric score
attached later is a fixed heuristic for that label.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

CATEGORIES = (
    "identity",
    "education",
    "interests",
    "projects",
    "experiences",
    "goals",
    "preferences",
    "skills",
    "relationships",
    "other",
)

STATUSES = ("proposed", "accepted", "rejected", "superseded")
STANCES = ("explicit", "inferred", "hypothesis")
ENTITY_TYPES = (
    "person",
    "field",
    "interest",
    "skill",
    "project",
    "technology",
    "organization",
    "goal",
    "preference",
    "other",
)
SOURCE_TYPES = ("manual", "upload", "question_answer", "conversation")
QUESTION_STATUSES = ("pending", "answered", "skipped", "dismissed")

STANCE_CONFIDENCE = {
    "explicit": 0.72,
    "inferred": 0.4,
    "hypothesis": 0.22,
}

CONFIDENCE_NOTE = "Heuristic label score, not a calibrated probability."


class EntityIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    entity_type: str = "other"

    @field_validator("entity_type")
    @classmethod
    def _type(cls, value: str) -> str:
        cleaned = value.strip().lower().replace("-", "_").replace(" ", "_")
        return cleaned if cleaned in ENTITY_TYPES else "other"


class RelationshipIn(BaseModel):
    source: str = Field(min_length=1, max_length=80)
    target: str = Field(min_length=1, max_length=80)
    relationship_type: str = Field(min_length=1, max_length=40)
    stance: str = "explicit"

    @field_validator("stance")
    @classmethod
    def _stance(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in STANCES:
            raise ValueError("unknown stance")
        return cleaned


class ExtractedMemory(BaseModel):
    content: str = Field(min_length=3, max_length=400)
    category: str
    stance: str
    evidence: str = ""
    entities: list[EntityIn] = Field(default_factory=list)
    relationships: list[RelationshipIn] = Field(default_factory=list)

    @field_validator("category")
    @classmethod
    def _category(cls, value: str) -> str:
        cleaned = value.strip().lower()
        return cleaned if cleaned in CATEGORIES else "other"

    @field_validator("stance")
    @classmethod
    def _stance(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in STANCES:
            raise ValueError("unknown stance")
        return cleaned


class ExtractionPayload(BaseModel):
    memories: list[ExtractedMemory] = Field(default_factory=list)
