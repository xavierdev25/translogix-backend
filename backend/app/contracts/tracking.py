"""Client-safe tracking view (DEC-S1) for ``GET /api/v1/tracking/{guia}``.

Free-text fields are ``ClientSafeText``: building a response that contains a
phone number, plate, ID document, NIT, exact address, GPS position, URL or a
person's name fails validation. The full TMS record lives in ``tms.py`` and is
only seen by the orchestrator.
"""

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from app.contracts.common import BogotaDatetime, ClientSafeText, ContractModel, NumeroGuia

STATUS_CODE_PATTERN = r"^[A-Z][A-Z0-9_]*$"


class EstadoGuia(StrEnum):
    """TMS statuses known today.

    Response fields keep ``codigo`` as an open string (DEC-C7): a new TMS status
    must not break clients; it is shown with its label and explanation.
    """

    SOLICITUD_GENERADA = "SOLICITUD_GENERADA"
    RECOGIDO_EN_ORIGEN = "RECOGIDO_EN_ORIGEN"
    EN_BODEGA_ORIGEN = "EN_BODEGA_ORIGEN"
    RELECTURA_HUB_FONTIBON = "RELECTURA_HUB_FONTIBON"
    EN_TRANSITO = "EN_TRANSITO"
    EN_BODEGA_DESTINO = "EN_BODEGA_DESTINO"
    EN_REPARTO = "EN_REPARTO"
    ENTREGADO = "ENTREGADO"
    RETENIDO_NOVEDAD = "RETENIDO_NOVEDAD"
    EN_INVESTIGACION_SINIESTRO = "EN_INVESTIGACION_SINIESTRO"


class EstadoVista(ContractModel):
    codigo: str = Field(pattern=STATUS_CODE_PATTERN, examples=["EN_TRANSITO"])
    etiqueta: ClientSafeText
    explicacion: ClientSafeText


class EventoVista(ContractModel):
    timestamp: BogotaDatetime
    estado: str = Field(pattern=STATUS_CODE_PATTERN)
    etiqueta: ClientSafeText
    ubicacion: ClientSafeText | None
    descripcion_cliente: ClientSafeText


class PruebaEntregaVista(ContractModel):
    recibido_por_iniciales: str = Field(
        pattern=r"^(?:[A-ZÁÉÍÓÚÑ]\. ?){1,4}$",
        examples=["R. B."],
        description="Solo iniciales; nunca nombre completo, cédula ni URL de firma.",
    )
    fecha_hora_entrega: BogotaDatetime
    observaciones: ClientSafeText | None


class TrackingFlags(ContractModel):
    requiere_refrigeracion: bool
    mercancia_peligrosa: bool


class FuenteConsulta(ContractModel):
    sistema: Literal["TMS_SIMULADO", "TMS"]
    consultado_en: BogotaDatetime


class TrackingResponse(ContractModel):
    numero_guia: NumeroGuia
    estado_actual: EstadoVista
    origen_ciudad: str
    destino_ciudad: str
    tipo_servicio: str
    fecha_despacho: BogotaDatetime | None
    fecha_estimada_entrega: BogotaDatetime | None
    ubicacion_referencia: ClientSafeText | None
    eventos: list[EventoVista] = Field(description="En orden cronológico.")
    prueba_entrega: PruebaEntregaVista | None
    flags: TrackingFlags
    ultima_temperatura_c: float | None = Field(
        default=None, description="Solo si el TMS la reporta en los eventos."
    )
    fuente: FuenteConsulta

    @model_validator(mode="after")
    def _events_are_chronological(self) -> Self:
        stamps = [event.timestamp for event in self.eventos]
        if stamps != sorted(stamps):
            raise ValueError("'eventos' must be in chronological order")
        return self
