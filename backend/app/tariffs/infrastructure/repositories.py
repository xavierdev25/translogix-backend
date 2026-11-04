from collections.abc import Sequence
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.tariffs import TipoPerfil
from app.tariffs.domain.engine import ProfileStatus, TariffProfile, TariffRule
from app.tariffs.infrastructure.tables import TariffProfileRow, TariffQuotationRow, TariffRuleRow


def _profile(row: TariffProfileRow) -> TariffProfile:
    return TariffProfile(
        id=row.id,
        name=row.name,
        version=row.version,
        environment_type=TipoPerfil(row.environment_type),
        status=ProfileStatus(row.status),
        iva_pct=row.iva_pct,
        volumetric_divisor=row.volumetric_divisor,
        effective_from=row.effective_from,
        effective_until=row.effective_until,
    )


def _rule(row: TariffRuleRow) -> TariffRule:
    return TariffRule(
        destination_city_code=row.destination_city_code,
        base_price_cop=row.base_price_cop,
        included_weight_kg=row.included_weight_kg,
        excess_price_per_kg_cop=row.excess_price_per_kg_cop,
    )


class TariffRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def active(self, environment_type: TipoPerfil) -> TariffProfile | None:
        row = await self._session.scalar(
            select(TariffProfileRow).where(
                TariffProfileRow.environment_type == environment_type.value,
                TariffProfileRow.status == ProfileStatus.ACTIVE.value,
            )
        )
        return _profile(row) if row else None

    async def get(self, profile_id: UUID) -> TariffProfile | None:
        row = await self._session.get(TariffProfileRow, profile_id)
        return _profile(row) if row else None

    async def get_by_version(self, name: str, version: int) -> TariffProfile | None:
        row = await self._session.scalar(
            select(TariffProfileRow).where(
                TariffProfileRow.name == name, TariffProfileRow.version == version
            )
        )
        return _profile(row) if row else None

    async def all(self) -> list[TariffProfile]:
        rows = await self._session.scalars(
            select(TariffProfileRow).order_by(TariffProfileRow.name, TariffProfileRow.version)
        )
        return [_profile(row) for row in rows]

    async def rule(self, profile_id: UUID, destination_city_code: str) -> TariffRule | None:
        row = await self._session.scalar(
            select(TariffRuleRow).where(
                TariffRuleRow.tariff_profile_id == profile_id,
                TariffRuleRow.destination_city_code == destination_city_code,
            )
        )
        return _rule(row) if row else None

    async def rules(self, profile_id: UUID) -> list[TariffRule]:
        rows = await self._session.scalars(
            select(TariffRuleRow)
            .where(TariffRuleRow.tariff_profile_id == profile_id)
            .order_by(TariffRuleRow.destination_city_code)
        )
        return [_rule(row) for row in rows]

    async def add(self, profile: TariffProfile, rules: Sequence[TariffRule], at: datetime) -> None:
        """A new version with its rules, flushed in FK order (no ORM relationships)."""
        self._session.add(
            TariffProfileRow(
                id=profile.id,
                name=profile.name,
                version=profile.version,
                environment_type=profile.environment_type.value,
                status=profile.status.value,
                iva_pct=profile.iva_pct,
                volumetric_divisor=profile.volumetric_divisor,
                created_at=at,
                effective_from=profile.effective_from,
                effective_until=profile.effective_until,
            )
        )
        await self._session.flush()
        self._session.add_all(
            TariffRuleRow(
                id=uuid4(),
                tariff_profile_id=profile.id,
                destination_city_code=rule.destination_city_code,
                base_price_cop=rule.base_price_cop,
                included_weight_kg=rule.included_weight_kg,
                excess_price_per_kg_cop=rule.excess_price_per_kg_cop,
                created_at=at,
            )
            for rule in rules
        )
        await self._session.flush()

    async def set_status(self, profile_id: UUID, status: ProfileStatus) -> None:
        await self._session.execute(
            update(TariffProfileRow)
            .where(TariffProfileRow.id == profile_id)
            .values(status=status.value)
        )
        await self._session.flush()


class TariffQuotationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, row: TariffQuotationRow) -> None:
        self._session.add(row)
        await self._session.flush()

    async def get(self, quotation_id: UUID) -> TariffQuotationRow | None:
        return await self._session.get(TariffQuotationRow, quotation_id)
