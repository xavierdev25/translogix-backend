"""PQRS (P09): deadlines, checklist, preliminary settlement and ticket. Deterministic.

Source: DOC-PQRS-2026-04. Provisional decisions: DEC-P1 (loss window), DEC-P2
(cite, never add rights), DEC-P3 (settlement is preliminary, never binding),
DEC-P4 (checklist), DEC-P5 (business days without holidays until the team
loads the calendar). The LLM never decides eligibility.
"""

import hashlib
import re
from collections.abc import Collection
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from app.contracts.attachments import AttachmentUploadResponse
from app.contracts.common import ContractError, ErrorCode
from app.contracts.conversation import Intent, Sentiment
from app.contracts.escalation import EscalationInput
from app.contracts.money import to_cop
from app.contracts.ports import AccountDirectoryPort, Clock, EscalationPort, TmsPort
from app.contracts.pqrs import (
    ChecklistItem,
    DocumentoCodigo,
    Elegibilidad,
    EstadoTicket,
    FundamentoNormativo,
    LiquidacionPreliminar,
    PqrsTicketRequest,
    PqrsTicketResponse,
    PqrsTipo,
    TipoNovedad,
)
from app.contracts.rag import DocCode
from app.contracts.timezone import to_bogota
from app.contracts.tms import TmsShipment
from app.db.repositories import AttachmentRepository, PqrsRepository
from app.shared.business_days import NO_CALENDAR_NOTE, add_business_days
from app.shared.text import fold

DEDUCTIBLE_THRESHOLD_COP = 2_000_000
DEDUCTIBLE_PCT = 5
HIDDEN_DAMAGE_DAYS = 3
LOSS_DECLARATION_DAYS = 5
LOSS_FILING_DAYS = 10
DEADLINES = FundamentoNormativo(
    doc_code=DocCode.DOC_PQRS_2026_04, section_path="3. Tiempos y plazos legales de radicación"
)
EXONERATION_CAUSES = FundamentoNormativo(
    doc_code=DocCode.DOC_PQRS_2026_04,
    section_path="6. Causales expresas de exoneración de responsabilidad",
)
_CLAIM_DOCS = (
    DocumentoCodigo.GUIA,
    DocumentoCodigo.FACTURA_COMERCIAL,
    DocumentoCodigo.CARTA_RECLAMACION,
    DocumentoCodigo.CERTIFICACION_BANCARIA,
    DocumentoCodigo.FOTOS_EMPAQUE,
    DocumentoCodigo.FOTOS_DANO,
    DocumentoCodigo.CEDULA_O_RUT,
    DocumentoCodigo.ACTA_CON_SALVEDAD,
)
_PHOTO_CASES = {TipoNovedad.AVERIA_APARENTE, TipoNovedad.AVERIA_NO_APARENTE, TipoNovedad.SAQUEO}
_NO_REMARK = re.compile(r"sin aver|intact|satisfacc|conforme|sin novedad")


def _note(*parts: str | None) -> str | None:
    text = "; ".join(p for p in parts if p)
    return text or None


def checklist_for(
    novedad: TipoNovedad | None, received: Collection[DocumentoCodigo], has_guia: bool
) -> list[ChecklistItem]:
    def required(code: DocumentoCodigo) -> bool:
        if code in (DocumentoCodigo.FOTOS_EMPAQUE, DocumentoCodigo.FOTOS_DANO):
            return novedad in _PHOTO_CASES
        if code is DocumentoCodigo.ACTA_CON_SALVEDAD:
            return novedad is TipoNovedad.AVERIA_APARENTE  # DEC-P4a
        return True

    return [
        ChecklistItem(
            codigo=code,
            obligatorio=required(code),
            recibido=code in received or (code is DocumentoCodigo.GUIA and has_guia),
        )
        for code in _CLAIM_DOCS
    ]


def eligibility(
    novedad: TipoNovedad, shipment: TmsShipment, today: date, holidays: frozenset[date]
) -> Elegibilidad | None:
    calendar = None if holidays else NO_CALENDAR_NOTE
    pod = shipment.prueba_entrega
    if novedad in (TipoNovedad.AVERIA_NO_APARENTE, TipoNovedad.SAQUEO):
        if pod is None:
            raise ContractError(
                ErrorCode.VALIDACION,
                "La guía aún no registra entrega; esta novedad se reporta tras recibir.",
            )
        delivered = to_bogota(pod.fecha_hora_entrega).date()
        limit = add_business_days(delivered, HIDDEN_DAMAGE_DAYS, holidays)
        return Elegibilidad(
            dentro_de_plazo=today <= limit,
            regla_aplicada=f"{novedad.value}_3_DIAS_HABILES",
            fecha_limite=limit,
            nota=_note(
                calendar,
                "saqueo tratado como novedad no aparente (DEC-C3, pendiente)"
                if novedad is TipoNovedad.SAQUEO
                else None,
            ),
            fundamento=DEADLINES,
        )
    if novedad is TipoNovedad.AVERIA_APARENTE:
        if pod is None:
            raise ContractError(ErrorCode.VALIDACION, "La guía aún no registra entrega.")
        remark = pod.observaciones_recibido or ""
        noted = bool(remark) and not _NO_REMARK.search(fold(remark))
        return Elegibilidad(
            dentro_de_plazo=noted,
            regla_aplicada="AVERIA_APARENTE_ANOTACION_EN_ENTREGA",
            fecha_limite=to_bogota(pod.fecha_hora_entrega).date(),
            nota=_note(
                None
                if noted
                else "la prueba de entrega no registra salvedad: opera la presunción del Art. 1028",
                calendar,
            ),
            fundamento=DEADLINES,
        )
    if novedad is TipoNovedad.PERDIDA_TOTAL:
        if pod is not None:
            raise ContractError(
                ErrorCode.VALIDACION, "La guía registra entrega; no aplica pérdida total."
            )
        if shipment.fecha_estimada_entrega is None:
            return None
        eta = to_bogota(shipment.fecha_estimada_entrega).date()
        declared = add_business_days(eta, LOSS_DECLARATION_DAYS, holidays)
        limit = add_business_days(declared, LOSS_FILING_DAYS, holidays)
        return Elegibilidad(
            dentro_de_plazo=today <= limit,
            regla_aplicada="PERDIDA_TOTAL_10_DIAS_HABILES_TRAS_DECLARACION",
            fecha_limite=limit,
            nota=_note(f"pérdida declarable desde {declared.isoformat()} (DEC-P1)", calendar),
            fundamento=DEADLINES,
        )
    return None


def preliminary_settlement(declared_cop: int, claimed_cop: int) -> LiquidacionPreliminar:
    """min(declared, proven value); the claimed amount stands in for the invoice (DEC-P3)."""
    base = min(declared_cop, claimed_cop)
    pct = DEDUCTIBLE_PCT if declared_cop > DEDUCTIBLE_THRESHOLD_COP else 0
    return LiquidacionPreliminar(
        base_cop=base, deducible_pct=pct, deducible_cop=to_cop(Decimal(base) * pct / 100)
    )


class PqrsService:
    """``PqrsPort``: builds and persists tickets; consults escalation itself (CRIT-003)."""

    def __init__(
        self,
        *,
        tms: TmsPort,
        escalation: EscalationPort,
        tickets: PqrsRepository,
        attachments: AttachmentRepository,
        clock: Clock,
        holidays: frozenset[date],
        accounts: AccountDirectoryPort | None = None,
    ) -> None:
        self._tms = tms
        self._accounts = accounts
        self._escalation = escalation
        self._tickets = tickets
        self._attachments = attachments
        self._clock = clock
        self._holidays = holidays

    async def create_ticket(
        self, request: PqrsTicketRequest, *, sentiment: Sentiment = Sentiment.NEUTRO
    ) -> PqrsTicketResponse:
        shipment = None
        if request.numero_guia:
            shipment = await self._tms.get_shipment(request.numero_guia)
            if shipment is None:
                raise ContractError(
                    ErrorCode.GUIA_NO_ENCONTRADA, details={"numero_guia": request.numero_guia}
                )

        ids = [d.attachment_id for d in request.documentos]
        unknown = sorted(set(ids) - await self._attachments.existing(ids))
        if unknown:
            raise ContractError(
                ErrorCode.VALIDACION,
                "Hay adjuntos que no existen.",
                details={"attachment_ids": unknown},
            )

        claim = request.tipo is PqrsTipo.SINIESTRO
        checklist = (
            checklist_for(
                request.tipo_novedad,
                {d.codigo for d in request.documentos},
                bool(request.numero_guia),
            )
            if claim
            else []
        )
        missing = [item.codigo for item in checklist if item.obligatorio and not item.recibido]
        today = self._clock.now().date()
        elig = (
            eligibility(request.tipo_novedad, shipment, today, self._holidays)
            if claim and shipment and request.tipo_novedad
            else None
        )
        settlement = (
            preliminary_settlement(
                shipment.especificaciones_carga.declarado_cop, request.valor_reclamado_cop
            )
            if claim and shipment and request.valor_reclamado_cop is not None
            else None
        )

        cargo = shipment.especificaciones_carga if shipment else None
        account = (
            await self._accounts.get_account(shipment.remitente.nit)
            if shipment and self._accounts
            else None
        )
        decision = self._escalation.evaluate(
            EscalationInput(
                intent=Intent.PQRS,
                user_text_normalized=request.descripcion,
                sentiment=sentiment,
                valor_declarado_cop=cargo.declarado_cop if cargo else None,
                valor_reclamacion_cop=request.valor_reclamado_cop,
                envios_mensuales_cuenta=account.envios_mensuales_cuenta if account else None,
                tipo_cuenta=account.tipo_cuenta if account else None,
                mercancia_peligrosa=cargo.mercancia_peligrosa if cargo else None,
                requiere_refrigeracion=cargo.requiere_refrigeracion if cargo else None,
                estado_guia=shipment.estado_actual if shipment else None,
                intentos_entrega_fallidos=shipment.intentos_entrega_fallidos if shipment else None,
                incident_reported=claim,
            )
        )
        if decision.escalate:
            status = EstadoTicket.ESCALADO
        elif elig is not None and not elig.dentro_de_plazo:
            status = EstadoTicket.FUERA_DE_PLAZO
        elif missing:
            status = EstadoTicket.BORRADOR
        else:
            status = EstadoTicket.RADICADO

        ticket = PqrsTicketResponse(
            ticket_id=uuid4(),
            estado=status,
            elegibilidad=elig,
            checklist=checklist,
            faltantes=missing,
            liquidacion_preliminar=settlement,
            escalamiento=decision if decision.escalate else None,
        )
        await self._tickets.add(request, ticket, self._clock.now())
        return ticket


_PDF, _JPEG = b"%PDF-", b"\xff\xd8\xff"


class AttachmentService:
    """Stores PDF/JPG uploads (P1). Type is sniffed from content; client file names are not kept."""

    def __init__(
        self, repo: AttachmentRepository, storage_dir: Path, max_bytes: int, clock: Clock
    ) -> None:
        self._repo = repo
        self._dir = storage_dir
        self._max = max_bytes
        self._clock = clock

    async def store(
        self, data: bytes, *, declared: DocumentoCodigo | None, session_id: UUID | None
    ) -> AttachmentUploadResponse:
        if not data:
            raise ContractError(ErrorCode.VALIDACION, "El archivo está vacío.")
        if len(data) > self._max:
            raise ContractError(
                ErrorCode.VALIDACION,
                "El archivo supera el tamaño máximo.",
                details={"max_bytes": self._max},
            )
        if data.startswith(_PDF):
            # 2.0.0 (SEC-06): a PDF must also end like one (``%%EOF`` near the end); a header
            # glued to anything else is rejected.
            if b"%%EOF" not in data[-1024:]:
                raise ContractError(ErrorCode.VALIDACION, "El PDF está incompleto o dañado.")
            mime, kind, extension = "application/pdf", "DOCUMENT", "pdf"
        elif data.startswith(_JPEG):
            mime, kind, extension = "image/jpeg", "IMAGE", "jpg"
        else:
            raise ContractError(ErrorCode.VALIDACION, "Solo se aceptan archivos PDF o JPG.")
        attachment_id = str(uuid4())
        name = f"{attachment_id}.{extension}"
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / name).write_bytes(data)
        response = AttachmentUploadResponse(
            attachment_id=attachment_id,
            kind=kind,  # type: ignore[arg-type]
            mime_type=mime,  # type: ignore[arg-type]
            size_bytes=len(data),
            declared_doc_code=declared,
            sha256=hashlib.sha256(data).hexdigest(),
        )
        await self._repo.add(
            response, session_id=session_id, storage_ref=name, at=self._clock.now()
        )
        return response
