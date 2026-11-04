"""Attachment upload (``POST /api/v1/attachments``, P1).

Added in 1.2.0: chat and PQRS reference ``attachment_id`` and no endpoint
created one (DEC-C11). Only PDF and JPG are accepted (DOC-PQRS-2026-04 §4:
"PDF o JPG claro"); the type is checked on the content, not on the file name.
"""

from typing import Literal, Self

from pydantic import Field, model_validator

from app.contracts.common import ContractModel
from app.contracts.pqrs import DocumentoCodigo

ACCEPTED_MIME_TYPES = ("application/pdf", "image/jpeg")
_KIND_BY_MIME = {"application/pdf": "DOCUMENT", "image/jpeg": "IMAGE"}


class AttachmentUploadResponse(ContractModel):
    attachment_id: str = Field(min_length=1, max_length=64, description="Id aleatorio.")
    kind: Literal["IMAGE", "DOCUMENT"]
    mime_type: Literal["application/pdf", "image/jpeg"] = Field(
        description="Tipo detectado por contenido."
    )
    size_bytes: int = Field(gt=0)
    declared_doc_code: DocumentoCodigo | None
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _kind_matches_type(self) -> Self:
        if _KIND_BY_MIME[self.mime_type] != self.kind:
            raise ValueError("kind must be DOCUMENT for PDF and IMAGE for JPG")
        return self
