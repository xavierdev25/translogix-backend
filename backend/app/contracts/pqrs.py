"""PQRS ticket facade (``POST /api/v1/pqrs/crear-ticket``) and its vocabulary."""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self

from pydantic import UUID4, Field, model_validator

from app.contracts.common import ContractModel, Cop, NumeroGuia
from app.contracts.escalation import EscalationDecision
from app.contracts.money import to_cop
from app.contracts.rag import DocCode


class PqrsTipo(StrEnum):
    PETICION = "PETICION"
    QUEJA = "QUEJA"
    RECLAMO = "RECLAMO"
    SINIESTRO = "SINIESTRO"


class TipoNovedad(StrEnum):
    AVERIA_APARENTE = "AVERIA_APARENTE"
    AVERIA_NO_APARENTE = "AVERIA_NO_APARENTE"
    PERDIDA_TOTAL = "PERDIDA_TOTAL"
    SAQUEO = "SAQUEO"  # added in 1.0.0: the photo rule (DEC-P4) refers to it
    OTRO = "OTRO"


class DocumentoCodigo(StrEnum):
    """Claim checklist codes (DEC-P4)."""

    GUIA = "GUIA"
    FACTURA_COMERCIAL = "FACTURA_COMERCIAL"
    CARTA_RECLAMACION = "CARTA_RECLAMACION"
    CERTIFICACION_BANCARIA = "CERTIFICACION_BANCARIA"
    FOTOS_EMPAQUE = "FOTOS_EMPAQUE"
    FOTOS_DANO = "FOTOS_DANO"
    CEDULA_O_RUT = "CEDULA_O_RUT"
    ACTA_CON_SALVEDAD = "ACTA_CON_SALVEDAD"  # conditional


class EstadoTicket(StrEnum):
    BORRADOR = "BORRADOR"
    RADICADO = "RADICADO"
    FUERA_DE_PLAZO = "FUERA_DE_PLAZO"  # added in 1.2.0 (P01 prompt, P09)
    ESCALADO = "ESCALADO"


class DocumentoAdjunto(ContractModel):
    codigo: DocumentoCodigo
    attachment_id: str = Field(min_length=1, max_length=64)


class PqrsTicketRequest(ContractModel):
    session_id: UUID4 | None = None
    tipo: PqrsTipo
    numero_guia: NumeroGuia | None = None
    tipo_novedad: TipoNovedad | None = None
    descripcion: str = Field(min_length=1, max_length=4000)
    valor_reclamado_cop: Cop | None = None
    documentos: list[DocumentoAdjunto] = Field(default_factory=list)

    @model_validator(mode="after")
    def _claims_are_complete(self) -> Self:
        if self.tipo is PqrsTipo.SINIESTRO:
            # The claimed amount may come later (chat flow, S14): without it there is no
            # preliminary settlement, but the ticket, deadline and checklist still exist.
            missing = [
                name for name in ("numero_guia", "tipo_novedad") if getattr(self, name) is None
            ]
            if missing:
                raise ValueError(f"a SINIESTRO requires: {', '.join(missing)}")
        elif self.tipo_novedad is not None:
            raise ValueError("'tipo_novedad' only applies to SINIESTRO")
        return self


class FundamentoNormativo(ContractModel):
    """Where the applied rule comes from (DEC-P2: cite, never add rights of our own)."""

    doc_code: DocCode
    section_path: str = Field(min_length=1, examples=["3. Tiempos y plazos legales de radicación"])


class Elegibilidad(ContractModel):
    dentro_de_plazo: bool
    regla_aplicada: str = Field(
        pattern=r"^[A-Z0-9_]+$", examples=["AVERIA_NO_APARENTE_3_DIAS_HABILES"]
    )
    fecha_limite: date | None
    nota: str | None = None
    fundamento: FundamentoNormativo | None = None


class ChecklistItem(ContractModel):
    codigo: DocumentoCodigo
    obligatorio: bool
    recibido: bool


class LiquidacionPreliminar(ContractModel):
    base_cop: Cop
    deducible_pct: int = Field(ge=0, le=100)
    deducible_cop: Cop
    flete_devuelto: Literal["PENDIENTE_REVISION_HUMANA"] = "PENDIENTE_REVISION_HUMANA"
    estado: Literal["PRELIMINAR_NO_VINCULANTE"] = "PRELIMINAR_NO_VINCULANTE"

    @model_validator(mode="after")
    def _deductible_matches_rate(self) -> Self:
        expected = to_cop(Decimal(self.base_cop) * self.deducible_pct / 100)
        if self.deducible_cop != expected:
            raise ValueError(f"deducible_cop must be {expected} (base * pct / 100, half-up)")
        return self


class PqrsTicketResponse(ContractModel):
    ticket_id: UUID4
    estado: EstadoTicket
    elegibilidad: Elegibilidad | None
    checklist: list[ChecklistItem]
    faltantes: list[DocumentoCodigo]
    liquidacion_preliminar: LiquidacionPreliminar | None
    escalamiento: EscalationDecision | None

    @model_validator(mode="after")
    def _status_is_consistent(self) -> Self:
        expected = [
            item.codigo for item in self.checklist if item.obligatorio and not item.recibido
        ]
        if self.faltantes != expected:
            raise ValueError("'faltantes' must list the required, not received items in order")
        if self.estado is EstadoTicket.RADICADO and self.faltantes:
            raise ValueError("a ticket cannot be RADICADO while documents are missing")
        escalated = self.escalamiento is not None and self.escalamiento.escalate
        if (self.estado is EstadoTicket.ESCALADO) != escalated:
            raise ValueError("estado ESCALADO if and only if escalamiento.escalate is true")
        # Escalation wins; otherwise an expired deadline is always visible as such.
        late = self.elegibilidad is not None and not self.elegibilidad.dentro_de_plazo
        if not escalated and (self.estado is EstadoTicket.FUERA_DE_PLAZO) != late:
            raise ValueError("estado FUERA_DE_PLAZO if and only if the deadline has passed")
        return self
