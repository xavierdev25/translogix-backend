"""Global conventions of the integration contract (owner: P01).

Every other contract module builds on these types, so the conventions are
enforced by construction rather than by review:

* datetimes must carry an offset and are always serialised in Bogotá time;
* money is an integer number of COP;
* guía numbers match ``TLX-dddddd-CO``;
* every error travels in the same envelope with a stable ``ErrorCode``.
"""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    Strict,
    StringConstraints,
)

from app.contracts.normalizers import NUMERO_GUIA_PATTERN
from app.contracts.pii import find_pii
from app.contracts.timezone import to_bogota

CONTRACT_VERSION = "1.7.0"
REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_PATTERN = r"^[A-Za-z0-9._-]{1,128}$"
LOCALE = "es-CO"


class ContractModel(BaseModel):
    """Base of every contract model.

    Unknown fields are rejected in both directions: a client cannot smuggle
    fields in, and a service cannot leak extra (possibly sensitive) fields out.
    """

    # Serialisation schemas mark defaulted fields as required: a response always
    # carries them, so generated client types must not make them optional.
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


def _ensure_client_safe(value: str) -> str:
    found = find_pii(value)
    if found:
        kinds = ", ".join(sorted(found))
        raise ValueError(f"client-facing text must not contain personal data ({kinds})")
    return value


BogotaDatetime = Annotated[AwareDatetime, AfterValidator(to_bogota)]
NumeroGuia = Annotated[
    str, StringConstraints(pattern=NUMERO_GUIA_PATTERN), Field(examples=["TLX-984021-CO"])
]
# Strict: 125000.0 or "125000" are rejected, so a float or a string never passes for money.
Cop = Annotated[int, Strict(), Field(ge=0, description="Pesos colombianos (COP), entero.")]
Score = Annotated[float, Field(ge=0, le=1)]
ClientSafeText = Annotated[
    str,
    StringConstraints(min_length=1, max_length=1000),
    AfterValidator(_ensure_client_safe),
    Field(description="Texto visible al cliente; no puede contener datos personales (DEC-S1)."),
]
RequestId = Annotated[str, StringConstraints(pattern=REQUEST_ID_PATTERN)]


class ChannelType(StrEnum):
    WHATSAPP = "WHATSAPP"
    WEB_CHAT = "WEB_CHAT"
    PBX = "PBX"


class DeliveryMode(StrEnum):
    REAL = "REAL"
    SIMULATED = "SIMULATED"


class ErrorCode(StrEnum):
    VALIDACION = "VALIDACION"
    GUIA_FORMATO_INVALIDO = "GUIA_FORMATO_INVALIDO"
    GUIA_NO_ENCONTRADA = "GUIA_NO_ENCONTRADA"
    RUTA_SIN_TARIFA = "RUTA_SIN_TARIFA"
    CIUDAD_NO_RECONOCIDA = "CIUDAD_NO_RECONOCIDA"
    SERVICIO_NO_DISPONIBLE = "SERVICIO_NO_DISPONIBLE"
    EXCEDE_LIMITE_COBERTURA = "EXCEDE_LIMITE_COBERTURA"
    LLM_NO_DISPONIBLE = "LLM_NO_DISPONIBLE"
    RAG_SIN_EVIDENCIA = "RAG_SIN_EVIDENCIA"
    FIRMA_INVALIDA = "FIRMA_INVALIDA"
    LIMITE_DE_TASA = "LIMITE_DE_TASA"
    NO_ENCONTRADO = "NO_ENCONTRADO"
    ERROR_INTERNO = "ERROR_INTERNO"
    # Added in 1.0.0 (see contracts/CHANGELOG.md): used by NullAsr/NullTts and
    # by the admin and webhook-verification endpoints.
    NO_CONFIGURADO = "NO_CONFIGURADO"
    NO_AUTORIZADO = "NO_AUTORIZADO"
    # Added in 1.3.0: bulk shipments for enterprise clients (B2B API).
    NO_AUTENTICADO = "NO_AUTENTICADO"
    IDEMPOTENCIA_CONFLICTO = "IDEMPOTENCIA_CONFLICTO"
    LOTE_DEMASIADO_GRANDE = "LOTE_DEMASIADO_GRANDE"
    REFERENCIA_DUPLICADA = "REFERENCIA_DUPLICADA"
    CIUDAD_NO_CUBIERTA = "CIUDAD_NO_CUBIERTA"
    TARIFA_NO_DISPONIBLE = "TARIFA_NO_DISPONIBLE"
    ARCHIVO_INVALIDO = "ARCHIVO_INVALIDO"  # reserved for the CSV/Excel upload (P1)
    # 1.6.0 (CR-008): any request body over the size limit, cut before it is buffered.
    CUERPO_DEMASIADO_GRANDE = "CUERPO_DEMASIADO_GRANDE"


# One HTTP status per code, so every service answers the same failure the same way.
ERROR_HTTP_STATUS: Mapping[ErrorCode, int] = MappingProxyType(
    {
        ErrorCode.VALIDACION: 422,
        ErrorCode.GUIA_FORMATO_INVALIDO: 422,
        ErrorCode.GUIA_NO_ENCONTRADA: 404,
        ErrorCode.RUTA_SIN_TARIFA: 422,
        ErrorCode.CIUDAD_NO_RECONOCIDA: 422,
        ErrorCode.SERVICIO_NO_DISPONIBLE: 422,
        ErrorCode.EXCEDE_LIMITE_COBERTURA: 422,
        ErrorCode.LLM_NO_DISPONIBLE: 503,
        ErrorCode.RAG_SIN_EVIDENCIA: 422,
        ErrorCode.FIRMA_INVALIDA: 401,
        ErrorCode.LIMITE_DE_TASA: 429,
        ErrorCode.NO_ENCONTRADO: 404,
        ErrorCode.ERROR_INTERNO: 500,
        ErrorCode.NO_CONFIGURADO: 503,
        ErrorCode.NO_AUTORIZADO: 403,
        ErrorCode.NO_AUTENTICADO: 401,
        ErrorCode.IDEMPOTENCIA_CONFLICTO: 409,
        ErrorCode.LOTE_DEMASIADO_GRANDE: 413,
        # Item-level codes: they travel inside a batch result, not as an HTTP response.
        ErrorCode.REFERENCIA_DUPLICADA: 422,
        ErrorCode.CIUDAD_NO_CUBIERTA: 422,
        ErrorCode.TARIFA_NO_DISPONIBLE: 422,
        ErrorCode.ARCHIVO_INVALIDO: 422,
        ErrorCode.CUERPO_DEMASIADO_GRANDE: 413,
    }
)


# Default user-facing text (es-CO). Services may send a more specific message.
DEFAULT_ERROR_MESSAGES: Mapping[ErrorCode, str] = MappingProxyType(
    {
        ErrorCode.VALIDACION: "Revisa los datos enviados: hay campos inválidos o incompletos.",
        ErrorCode.GUIA_FORMATO_INVALIDO: (
            "El número de guía no tiene un formato válido. Debe verse así: TLX-123456-CO."
        ),
        ErrorCode.GUIA_NO_ENCONTRADA: (
            "No encontramos una guía con ese número. Verifica que esté bien escrita."
        ),
        ErrorCode.RUTA_SIN_TARIFA: "No tengo tarifa para esa ruta en la matriz disponible.",
        ErrorCode.CIUDAD_NO_RECONOCIDA: (
            "No reconozco esa ciudad en el directorio de cobertura disponible."
        ),
        ErrorCode.SERVICIO_NO_DISPONIBLE: (
            "Ese servicio no está disponible para el destino indicado."
        ),
        ErrorCode.EXCEDE_LIMITE_COBERTURA: (
            "El envío supera el peso o el volumen máximo que atendemos en ese destino."
        ),
        ErrorCode.LLM_NO_DISPONIBLE: (
            "El asistente no está disponible en este momento. Intenta de nuevo en unos minutos."
        ),
        ErrorCode.RAG_SIN_EVIDENCIA: (
            "No encontramos información suficiente para responder con seguridad."
        ),
        ErrorCode.FIRMA_INVALIDA: "La firma de la solicitud no es válida.",
        ErrorCode.LIMITE_DE_TASA: (
            "Recibimos demasiadas solicitudes seguidas. Espera un momento e intenta de nuevo."
        ),
        ErrorCode.NO_ENCONTRADO: "No encontramos el recurso solicitado.",
        ErrorCode.ERROR_INTERNO: "Ocurrió un error inesperado. Intenta de nuevo más tarde.",
        ErrorCode.NO_CONFIGURADO: "Esta función no está configurada en este entorno.",
        ErrorCode.NO_AUTORIZADO: "No tienes autorización para realizar esta operación.",
        ErrorCode.NO_AUTENTICADO: "Falta la credencial de acceso o no es válida.",
        ErrorCode.IDEMPOTENCIA_CONFLICTO: (
            "Ya existe un lote con esa referencia y un contenido diferente."
        ),
        ErrorCode.LOTE_DEMASIADO_GRANDE: (
            "El lote supera el número máximo de envíos o el tamaño permitido."
        ),
        ErrorCode.REFERENCIA_DUPLICADA: "La referencia del envío ya fue utilizada.",
        ErrorCode.CIUDAD_NO_CUBIERTA: "No tenemos cobertura operativa en esa ciudad.",
        ErrorCode.TARIFA_NO_DISPONIBLE: "No hay una tarifa disponible para esa ruta.",
        ErrorCode.ARCHIVO_INVALIDO: "El archivo no tiene un formato válido.",
        ErrorCode.CUERPO_DEMASIADO_GRANDE: "La solicitud supera el tamaño máximo permitido.",
    }
)


class ErrorBody(ContractModel):
    code: ErrorCode
    message: str = Field(min_length=1, description="Mensaje en español para el usuario final.")
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: RequestId


class ErrorEnvelope(ContractModel):
    """The single error shape returned by every endpoint."""

    error: ErrorBody


class ContractError(Exception):
    """Raised by services and adapters; the HTTP layer turns it into an ``ErrorEnvelope``."""

    def __init__(
        self,
        code: ErrorCode,
        message: str | None = None,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message or DEFAULT_ERROR_MESSAGES[code]
        self.details = dict(details or {})
        super().__init__(self.message)

    @property
    def http_status(self) -> int:
        return ERROR_HTTP_STATUS[self.code]

    def to_envelope(self, request_id: str) -> ErrorEnvelope:
        return ErrorEnvelope(
            error=ErrorBody(
                code=self.code, message=self.message, details=self.details, request_id=request_id
            )
        )
