"""TMS adapter over the simulated dataset (P04) and the deterministic tracking narrative.

Statuses, locations, dates and ETA always come from the TMS; nothing is
recalculated (DEC-T1) and the LLM is never involved.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.contracts.common import ContractError, ErrorCode
from app.contracts.ports import Clock
from app.contracts.tms import TmsEvento, TmsPruebaEntrega, TmsShipment
from app.contracts.tracking import (
    EstadoVista,
    EventoVista,
    FuenteConsulta,
    PruebaEntregaVista,
    TrackingFlags,
    TrackingResponse,
)
from app.modules.tracking.sanitizer import sanitize_place, sanitize_sentence
from app.shared.text import format_date_es, format_time_es

_TEMPERATURE = re.compile(r"(-?\d+(?:[.,]\d+)?)\s*°\s*C")


@dataclass(frozen=True)
class StateLabel:
    etiqueta: str
    explicacion: str


class StateMap:
    def __init__(self, path: Path) -> None:
        raw = json.loads(path.read_text(encoding="utf-8"))["estados"]
        self._labels = {code: StateLabel(**value) for code, value in raw.items()}

    def label(self, code: str) -> StateLabel:
        known = self._labels.get(code)
        if known:
            return known
        # Unknown status: literal code and an offer of help, never an invented meaning.
        return StateLabel(
            etiqueta=f"Estado {code}",
            explicacion=(
                "Este estado no tiene una explicación configurada; "
                "un asesor puede darte el detalle."
            ),
        )


def _initials(full_name: str) -> str:
    words = [w for w in re.split(r"\s+", full_name.strip()) if w and not w.endswith(".")]
    return " ".join(f"{w[0].upper()}." for w in words[:4]) or "N. D."


def last_temperature(events: list[TmsEvento]) -> float | None:
    for event in reversed(events):
        match = _TEMPERATURE.search(event.descripcion)
        if match:
            return float(match.group(1).replace(",", "."))
    return None


def _pod_view(pod: TmsPruebaEntrega) -> PruebaEntregaVista:
    notes = pod.observaciones_recibido
    return PruebaEntregaVista(
        recibido_por_iniciales=_initials(pod.recibido_por),
        fecha_hora_entrega=pod.fecha_hora_entrega,
        observaciones=sanitize_sentence(notes, fallback="") or None if notes else None,
    )


class DatasetTmsAdapter:
    """``TmsPort`` over ``dataset_guias_simuladas_tms.json`` (read once, validated)."""

    def __init__(self, dataset: Path, state_map: StateMap, clock: Clock) -> None:
        try:
            records = json.loads(dataset.read_text(encoding="utf-8"))
            shipments = [TmsShipment.model_validate(record) for record in records]
        except (OSError, ValueError) as error:  # unreadable or invalid dataset: fail at startup
            raise RuntimeError(f"TMS dataset not usable: {dataset}") from error
        self._shipments = {s.numero_guia: s for s in shipments}
        self._states = state_map
        self._clock = clock

    @property
    def guias(self) -> list[str]:
        return sorted(self._shipments)

    async def get_shipment(self, numero_guia: str) -> TmsShipment | None:
        return self._shipments.get(numero_guia)

    async def get_tracking_view(self, numero_guia: str) -> TrackingResponse | None:
        shipment = self._shipments.get(numero_guia)
        return self._view(shipment) if shipment else None

    def _view(self, s: TmsShipment) -> TrackingResponse:
        state = self._states.label(s.estado_actual)
        events = sorted(s.historial_trazabilidad, key=lambda e: e.timestamp)
        pod = s.prueba_entrega
        try:
            return TrackingResponse(
                numero_guia=s.numero_guia,
                estado_actual=EstadoVista(
                    codigo=s.estado_actual, etiqueta=state.etiqueta, explicacion=state.explicacion
                ),
                origen_ciudad=s.remitente.ciudad_origen,
                destino_ciudad=s.destinatario.ciudad_destino,
                tipo_servicio=s.especificaciones_carga.tipo_servicio,
                fecha_despacho=s.fecha_despacho,
                fecha_estimada_entrega=s.fecha_estimada_entrega,
                ubicacion_referencia=self._reference(s),
                eventos=[self._event(e) for e in events],
                prueba_entrega=_pod_view(pod) if pod else None,
                flags=TrackingFlags(
                    requiere_refrigeracion=s.especificaciones_carga.requiere_refrigeracion,
                    mercancia_peligrosa=s.especificaciones_carga.mercancia_peligrosa,
                ),
                ultima_temperatura_c=last_temperature(events),
                fuente=FuenteConsulta(sistema="TMS_SIMULADO", consultado_en=self._clock.now()),
            )
        except ValueError as error:  # the contract refused the view (e.g. PII slipped through)
            raise ContractError(ErrorCode.ERROR_INTERNO) from error

    @staticmethod
    def _reference(s: TmsShipment) -> str:
        city = s.destinatario.ciudad_destino
        # While out for delivery the vehicle is next to the recipient: show the city only.
        if s.estado_actual == "EN_REPARTO":
            return f"Ruta de reparto urbano - {city}"
        return sanitize_place(s.ubicacion_gps_actual.referencia, fallback=city)

    def _event(self, event: TmsEvento) -> EventoVista:
        state = self._states.label(event.estado)
        return EventoVista(
            timestamp=event.timestamp,
            estado=event.estado,
            etiqueta=state.etiqueta,
            ubicacion=sanitize_place(event.hub_ubicacion, fallback="") or None,
            descripcion_cliente=sanitize_sentence(event.descripcion, fallback=state.explicacion),
        )


def _sentence(text: str) -> str:
    return text if text.endswith((".", "!", "?", "»")) else text + "."


def render_tracking_text(view: TrackingResponse, clock: Clock) -> str:
    """Deterministic customer text built only from TMS fields."""
    state = view.estado_actual
    parts = [f"Guía {view.numero_guia}: {state.etiqueta}.", _sentence(state.explicacion)]
    pod = view.prueba_entrega
    if pod:
        parts.append(
            f"Entregada el {format_date_es(pod.fecha_hora_entrega)} a las "
            f"{format_time_es(pod.fecha_hora_entrega)} Recibió: {pod.recibido_por_iniciales}"
        )
        if pod.observaciones:
            parts.append(f"Observaciones: «{pod.observaciones}»")
    else:
        if view.ubicacion_referencia:
            parts.append(_sentence(f"Ubicación de referencia: {view.ubicacion_referencia}"))
        eta = view.fecha_estimada_entrega
        if eta:
            when = f"{format_date_es(eta)}, {format_time_es(eta)}"
            if eta < clock.now() and state.codigo != "ENTREGADO":
                parts.append(
                    f"La fecha estimada de entrega ({when}) ya pasó y el TMS no reporta la entrega."
                )
            else:
                parts.append(_sentence(f"Entrega estimada: {when}"))
    if view.ultima_temperatura_c is not None:
        temperature = str(view.ultima_temperatura_c).replace(".", ",")
        parts.append(f"Última temperatura reportada: {temperature} °C.")
    return " ".join(parts)
