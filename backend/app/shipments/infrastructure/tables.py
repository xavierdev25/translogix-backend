from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, Numeric, Sequence, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base
from app.shipments.domain.models import GUIDE_MAX, GUIDE_MIN, GUIDE_SEQUENCE_NAME

GUIDE_SEQUENCE = Sequence(
    GUIDE_SEQUENCE_NAME,
    start=GUIDE_MIN,
    minvalue=GUIDE_MIN,
    maxvalue=GUIDE_MAX,
    metadata=Base.metadata,
)
_KG = Numeric(10, 2)


class ShipmentRow(Base):
    """Operational shipment, without personal data (that lives in ``shipment_recipients``)."""

    __tablename__ = "shipments"
    __table_args__ = (UniqueConstraint("client_id", "referencia_cliente"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    numero_guia: Mapped[str] = mapped_column(String(13), unique=True)
    client_id: Mapped[UUID] = mapped_column(ForeignKey("enterprise_clients.id"))
    referencia_cliente: Mapped[str] = mapped_column(String(64))
    batch_item_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("shipment_batch_items.id"), index=True
    )
    origen_codigo_dane: Mapped[str] = mapped_column(String(8))
    destino_codigo_dane: Mapped[str] = mapped_column(String(8))
    codigo_trayecto: Mapped[str] = mapped_column(String(20))
    alto_cm: Mapped[Decimal] = mapped_column(_KG)
    ancho_cm: Mapped[Decimal] = mapped_column(_KG)
    largo_cm: Mapped[Decimal] = mapped_column(_KG)
    peso_real_kg: Mapped[Decimal] = mapped_column(_KG)
    peso_volumetrico_kg: Mapped[Decimal] = mapped_column(_KG)
    peso_facturable_kg: Mapped[Decimal] = mapped_column(_KG)
    valor_declarado_cop: Mapped[int] = mapped_column(BigInteger)
    requiere_cadena_frio: Mapped[bool]
    mercancia_peligrosa: Mapped[bool]
    cotizacion_id: Mapped[UUID | None] = mapped_column(ForeignKey("quotes.cotizacion_id"))
    flete_total_cop: Mapped[int | None] = mapped_column(BigInteger)
    formula_version: Mapped[str | None] = mapped_column(String(32))
    tarifa_version: Mapped[str | None] = mapped_column(String(80))
    cotizado_en: Mapped[datetime | None]
    estado: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class ShipmentRecipientRow(Base):
    """PII of the recipient. Read only by the use cases that need it (none in P0)."""

    __tablename__ = "shipment_recipients"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    shipment_id: Mapped[UUID] = mapped_column(ForeignKey("shipments.id"), unique=True)
    nombre: Mapped[str] = mapped_column(String(120))
    tipo_documento: Mapped[str | None] = mapped_column(String(10))
    numero_documento: Mapped[str | None] = mapped_column(String(20))
    telefono: Mapped[str] = mapped_column(String(16))
    email: Mapped[str | None] = mapped_column(String(120))
    direccion: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
