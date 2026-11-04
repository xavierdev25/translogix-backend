from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import QuoteRepository
from app.shipments.domain.models import ShipmentDraft, ShipmentStatus, format_guia
from app.shipments.infrastructure.tables import (
    GUIDE_SEQUENCE,
    ShipmentRecipientRow,
    ShipmentRow,
)


class ShipmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def reference_exists(self, client_id: UUID, referencia_cliente: str) -> bool:
        return bool(
            await self._session.scalar(
                select(
                    exists().where(
                        ShipmentRow.client_id == client_id,
                        ShipmentRow.referencia_cliente == referencia_cliente,
                    )
                )
            )
        )

    async def next_guia(self) -> str:
        number = await self._session.scalar(select(GUIDE_SEQUENCE.next_value()))
        if number is None:
            raise RuntimeError("the guide sequence returned no value")
        return format_guia(int(number))

    async def add(
        self, draft: ShipmentDraft, numero_guia: str, batch_item_id: UUID | None, at: datetime
    ) -> UUID:
        """Quote snapshot, shipment and recipient, flushed in FK order (no ORM relationships)."""
        await QuoteRepository(self._session).add(draft.request, draft.quote, None, at)
        calc = draft.quote.calculo
        shipment_id = uuid4()
        self._session.add(
            ShipmentRow(
                id=shipment_id,
                numero_guia=numero_guia,
                client_id=draft.client_id,
                referencia_cliente=draft.referencia_cliente,
                batch_item_id=batch_item_id,
                origen_codigo_dane=draft.origen_codigo_dane,
                destino_codigo_dane=draft.destino_codigo_dane,
                codigo_trayecto=draft.codigo_trayecto,
                alto_cm=draft.decimal(draft.request.alto_cm),
                ancho_cm=draft.decimal(draft.request.ancho_cm),
                largo_cm=draft.decimal(draft.request.largo_cm),
                peso_real_kg=draft.decimal(draft.request.peso_real_kg),
                peso_volumetrico_kg=draft.decimal(calc.peso_volumetrico_kg),
                peso_facturable_kg=draft.decimal(calc.peso_facturable_kg),
                valor_declarado_cop=draft.request.valor_declarado_cop,
                requiere_cadena_frio=draft.request.requiere_cadena_frio,
                mercancia_peligrosa=draft.request.mercancia_peligrosa,
                cotizacion_id=draft.quote.cotizacion_id,
                flete_total_cop=calc.flete_total_cop,
                formula_version=draft.quote.formula_version,
                tarifa_version=draft.tarifa_version,
                cotizado_en=draft.cotizado_en,
                estado=ShipmentStatus.REGISTRADO.value,
                created_at=at,
                updated_at=at,
            )
        )
        await self._session.flush()
        recipient = draft.recipient
        self._session.add(
            ShipmentRecipientRow(
                id=uuid4(),
                shipment_id=shipment_id,
                nombre=recipient.nombre,
                tipo_documento=recipient.tipo_documento,
                numero_documento=recipient.numero_documento,
                telefono=recipient.telefono,
                email=recipient.email,
                direccion=recipient.direccion,
                created_at=at,
                updated_at=at,
            )
        )
        await self._session.flush()
        return shipment_id
