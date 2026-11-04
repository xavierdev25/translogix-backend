"""RAG contract: chunk metadata, citations, confidence and the grounded answer."""

from enum import StrEnum
from typing import Any, Final, Literal, Self

from pydantic import Field, field_validator, model_validator

from app.contracts.common import BogotaDatetime, ContractModel, Score

CONFIDENCE_METHOD: Final = "heuristic_v1"
CONFIDENCE_COMPONENTS = frozenset({"top_sim", "margin", "term_coverage", "answerable"})


class DocCode(StrEnum):
    DOC_OPS_2026_03 = "DOC-OPS-2026-03"
    DOC_PQRS_2026_04 = "DOC-PQRS-2026-04"
    COBERTURA = "COBERTURA"
    TARIFAS = "TARIFAS"
    REGLAS_ESCALACION = "REGLAS_ESCALACION"


class ChunkTipo(StrEnum):
    NORMATIVA_CARGA = "normativa_carga"
    REGLAMENTO_PQRS = "reglamento_pqrs"
    EMBALAJE = "embalaje"
    COBERTURA = "cobertura"
    TARIFA = "tarifa"
    POLITICA = "politica"


class ChunkMetadata(ContractModel):
    doc_code: DocCode
    doc_title: str
    source_file: str
    section_path: str = Field(examples=["2. Clasificación > Clase 8: Sustancias Corrosivas"])
    tipo: ChunkTipo
    zona: str | None = Field(default=None, description="Derivada del departamento (DEC-R2).")
    clase_un: str | None = None
    aliases: list[str] = Field(default_factory=list)
    chunk_index: int = Field(ge=0)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$", description="SHA-256 del contenido.")
    ingested_at: BogotaDatetime


class Citation(ContractModel):
    doc_code: DocCode
    doc_title: str
    section_path: str
    chunk_id: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    score: Score


class Confidence(ContractModel):
    """Heuristic score in [0, 1]. It is explicitly *not* a probability."""

    score: Score
    method: Literal["heuristic_v1"] = CONFIDENCE_METHOD
    components: dict[str, Score]
    is_probability: Literal[False] = False

    @field_validator("components")
    @classmethod
    def _known_components(cls, components: dict[str, float]) -> dict[str, float]:
        if set(components) != CONFIDENCE_COMPONENTS:
            raise ValueError(f"components must be exactly {sorted(CONFIDENCE_COMPONENTS)}")
        return components


class RagAnswer(ContractModel):
    answer_text: str | None
    grounded: bool = Field(description="False: no se genera texto afirmativo.")
    citations: list[Citation]
    confidence: Confidence
    retrieval: dict[str, Any] = Field(
        description="top_k, scores y filtros usados.",
        examples=[{"top_k": 5, "scores": [0.82, 0.71], "filters": {"tipo": "normativa_carga"}}],
    )

    @model_validator(mode="after")
    def _grounding_rules(self) -> Self:
        if not self.grounded and self.answer_text is not None:
            raise ValueError("an ungrounded answer must not carry text (answer_text=null)")
        if self.grounded and (not self.answer_text or not self.citations):
            raise ValueError("a grounded answer needs text and at least one citation")
        return self
