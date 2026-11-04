"""Internal TMS record (``TmsShipment``), shaped like ``dataset_guias_simuladas_tms.json``.

Only the orchestrator sees it, to evaluate rules. It is never serialised to a
client and is not part of the public OpenAPI. Personal fields are excluded
from ``repr`` so they cannot leak into logs by accident.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.contracts.common import BogotaDatetime, Cop, NumeroGuia


def _pii(**kwargs: Any) -> Any:
    return Field(repr=False, json_schema_extra={"x-pii": True}, **kwargs)


class TmsModel(BaseModel):
    # Upstream system: tolerate new fields instead of failing the whole lookup.
    model_config = ConfigDict(extra="ignore")


class TmsRemitente(TmsModel):
    nombre_empresa: str = _pii()
    nit: str = _pii()
    ciudad_origen: str
    direccion_recogida: str = _pii()


class TmsDestinatario(TmsModel):
    nombre_contacto: str = _pii()
    empresa_destino: str = _pii()
    ciudad_destino: str
    departamento: str
    direccion_entrega: str = _pii()


class TmsDimensiones(TmsModel):
    alto: float
    ancho: float
    largo: float


class TmsEspecificacionesCarga(TmsModel):
    tipo_servicio: str
    peso_kg: float
    dimensiones_cm: TmsDimensiones
    peso_volumetrico_kg: float
    peso_liquidado_kg: float
    unidades: int
    declarado_cop: Cop
    requiere_refrigeracion: bool
    mercancia_peligrosa: bool
    clase_un: str | None = None
    hoja_seguridad_msds_adjunta: bool | None = None


class TmsUbicacionGps(TmsModel):
    latitud: float = _pii()
    longitud: float = _pii()
    referencia: str


class TmsEvento(TmsModel):
    timestamp: BogotaDatetime
    estado: str
    hub_ubicacion: str
    descripcion: str = _pii(description="Texto operativo crudo: puede traer placa o teléfono.")


class TmsPruebaEntrega(TmsModel):
    recibido_por: str = _pii()
    documento_identidad: str = _pii()
    fecha_hora_entrega: BogotaDatetime
    firma_digital_url: str = _pii()
    observaciones_recibido: str | None = None


class TmsShipment(TmsModel):
    numero_guia: NumeroGuia
    remitente: TmsRemitente
    destinatario: TmsDestinatario
    especificaciones_carga: TmsEspecificacionesCarga
    estado_actual: str
    ubicacion_gps_actual: TmsUbicacionGps
    fecha_despacho: BogotaDatetime | None
    fecha_estimada_entrega: BogotaDatetime | None
    historial_trazabilidad: list[TmsEvento]
    prueba_entrega: TmsPruebaEntrega | None
    # Absent from the simulated dataset; declared so INFO-INTENTOS can work once
    # the TMS reports it instead of being silently dropped by extra="ignore".
    intentos_entrega_fallidos: int | None = Field(default=None, ge=0)
