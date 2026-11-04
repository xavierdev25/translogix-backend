"""Profile quotations (CR-003): resolve cities, pick the active profile, compute, persist.

Prices come only from the active profile of ``TARIFF_ENVIRONMENT`` (TEST_ONLY by
default); a TEST_ONLY profile never yields a commercial price. Profiles are loaded
and activated by ``python -m app.tariffs.cli``, never edited in place.
"""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.contracts.common import ContractError, ErrorCode
from app.contracts.ports import Clock
from app.contracts.tariffs import (
    FORMULA_EXCEDENTE,
    CiudadRef,
    CotizacionPerfilRequest,
    CotizacionPerfilResponse,
    TarifaPerfilAplicada,
    TipoPerfil,
)
from app.modules.quotation.data import Coverage, TariffData
from app.tariffs.domain.engine import (
    ProfileStatus,
    TariffProfile,
    TariffRule,
    compute,
    is_effective,
)
from app.tariffs.infrastructure.repositories import TariffQuotationRepository, TariffRepository
from app.tariffs.infrastructure.tables import TariffQuotationRow


class TariffQuotationService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        tariffs: TariffData,
        clock: Clock,
        *,
        environment: TipoPerfil,
    ) -> None:
        self._sessions = sessions
        self._tariffs = tariffs
        self._clock = clock
        self._environment = environment

    def _place(self, field: str, name: str) -> Coverage:
        canonical = self._tariffs.canonical_city(name)
        place = self._tariffs.coverage_for(canonical) if canonical else None
        if canonical is None:
            raise ContractError(
                ErrorCode.CIUDAD_NO_RECONOCIDA, details={"campo": field, "ciudad": name}
            )
        if place is None:
            raise ContractError(
                ErrorCode.CIUDAD_NO_CUBIERTA, details={"campo": field, "ciudad": name}
            )
        return place

    async def quote(self, request: CotizacionPerfilRequest) -> CotizacionPerfilResponse:
        origin = self._place("origen", request.origen)
        destination = self._place("destino", request.destino)
        now = self._clock.now()
        async with self._sessions() as session:
            repo = TariffRepository(session)
            profile = await repo.active(self._environment)
            if profile is None or not is_effective(profile, now):
                raise ContractError(
                    ErrorCode.NO_CONFIGURADO,
                    "No hay un perfil tarifario activo y vigente.",
                    details={"tipo_perfil": self._environment.value},
                )
            rule = await repo.rule(profile.id, destination.codigo_dane)
            if rule is None:
                raise ContractError(
                    ErrorCode.TARIFA_NO_DISPONIBLE,
                    details={"campo": "destino", "ciudad": destination.municipio},
                )
            result = compute(
                rule,
                profile,
                peso_real_kg=request.peso_real_kg,
                alto_cm=request.alto_cm,
                ancho_cm=request.ancho_cm,
                largo_cm=request.largo_cm,
            )
            response = CotizacionPerfilResponse(
                cotizacion_id=uuid4(),
                formula_version=FORMULA_EXCEDENTE,
                precio_comercial=profile.environment_type is TipoPerfil.COMMERCIAL,
                origen=CiudadRef(codigo_dane=origin.codigo_dane, municipio=origin.municipio),
                destino=CiudadRef(
                    codigo_dane=destination.codigo_dane, municipio=destination.municipio
                ),
                peso_real_kg=request.peso_real_kg,
                peso_volumetrico_kg=result.peso_volumetrico_kg,
                peso_facturable_kg=result.peso_facturable_kg,
                peso_excedente_kg=result.peso_excedente_kg,
                tarifa=TarifaPerfilAplicada(
                    perfil_id=profile.id,
                    perfil_nombre=profile.name,
                    perfil_version=profile.version,
                    tipo_perfil=profile.environment_type,
                    base_cop=rule.base_price_cop,
                    peso_incluido_kg=rule.included_weight_kg,
                    precio_kg_excedente_cop=rule.excess_price_per_kg_cop,
                    divisor_volumetrico=profile.volumetric_divisor,
                    iva_pct=profile.iva_pct,
                ),
                flete_cop=result.flete_cop,
                iva_cop=result.iva_cop,
                total_cop=result.total_cop,
                advertencias=_coverage_warnings(destination, request, result.peso_facturable_kg),
                calculado_en=now,
            )
            await TariffQuotationRepository(session).add(_snapshot(request, response, now))
            await session.commit()
        return response


def _coverage_warnings(
    place: Coverage, request: CotizacionPerfilRequest, billable: Decimal
) -> list[str]:
    """Same policy as DEC-Q4: warn, do not block (validated by operations)."""
    warnings = []
    if billable > place.peso_maximo_kg:
        warnings.append(
            f"El peso facturable supera el máximo por paquete en {place.municipio} "
            f"({place.peso_maximo_kg.normalize()} kg); requiere validación operativa."
        )
    volume_m3 = request.alto_cm * request.ancho_cm * request.largo_cm / 1_000_000
    if volume_m3 > place.volumen_maximo_m3:
        warnings.append(
            f"El volumen supera el máximo en {place.municipio} "
            f"({place.volumen_maximo_m3.normalize()} m³); requiere validación operativa."
        )
    return warnings


def _snapshot(
    request: CotizacionPerfilRequest, response: CotizacionPerfilResponse, at: datetime
) -> TariffQuotationRow:
    rate = response.tarifa
    return TariffQuotationRow(
        id=response.cotizacion_id,
        origen_codigo_dane=response.origen.codigo_dane,
        destino_codigo_dane=response.destino.codigo_dane,
        peso_real_kg=request.peso_real_kg,
        alto_cm=request.alto_cm,
        ancho_cm=request.ancho_cm,
        largo_cm=request.largo_cm,
        peso_volumetrico_kg=response.peso_volumetrico_kg,
        peso_facturable_kg=response.peso_facturable_kg,
        peso_excedente_kg=response.peso_excedente_kg,
        tariff_profile_id=rate.perfil_id,
        profile_name=rate.perfil_nombre,
        profile_version=rate.perfil_version,
        profile_type=rate.tipo_perfil.value,
        base_price_cop=rate.base_cop,
        included_weight_kg=rate.peso_incluido_kg,
        excess_price_per_kg_cop=rate.precio_kg_excedente_cop,
        volumetric_divisor=rate.divisor_volumetrico,
        iva_pct=rate.iva_pct,
        flete_cop=response.flete_cop,
        iva_cop=response.iva_cop,
        total_cop=response.total_cop,
        formula_version=response.formula_version,
        precio_comercial=response.precio_comercial,
        calculated_at=at,
    )


# --- administration (CLI) ---------------------------------------------------------------


class TariffRuleDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination_city_code: str = Field(pattern=r"^\d{5}$")
    base_price_cop: int = Field(ge=0, strict=True)
    included_weight_kg: Decimal = Field(ge=0, decimal_places=2)
    excess_price_per_kg_cop: int = Field(ge=0, strict=True)


class TariffProfileDefinition(BaseModel):
    """JSON accepted by ``python -m app.tariffs.cli load``."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    version: int = Field(ge=1)
    environment_type: TipoPerfil
    iva_pct: Decimal = Field(ge=0, le=100, decimal_places=2)
    volumetric_divisor: int = Field(default=4000, gt=0)
    effective_from: AwareDatetime | None = None
    effective_until: AwareDatetime | None = None
    rules: list[TariffRuleDefinition] = Field(min_length=1)


class TariffAdminService:
    """Loads new versions (always INACTIVE) and activates them; the caller commits."""

    def __init__(self, session: AsyncSession, tariffs: TariffData, clock: Clock) -> None:
        self._repo = TariffRepository(session)
        self._known_codes = {place.codigo_dane for place in tariffs.coverage.values()}
        self._clock = clock

    async def load(self, definition: TariffProfileDefinition) -> TariffProfile:
        codes = [r.destination_city_code for r in definition.rules]
        if len(codes) != len(set(codes)):
            raise ContractError(ErrorCode.VALIDACION, "Hay destinos repetidos en las reglas.")
        unknown = sorted(set(codes) - self._known_codes)
        if unknown:
            raise ContractError(
                ErrorCode.VALIDACION,
                "Hay códigos DANE que no están en el directorio de cobertura.",
                details={"codigos": unknown},
            )
        if await self._repo.get_by_version(definition.name, definition.version):
            raise ContractError(ErrorCode.VALIDACION, "Esa versión del perfil ya existe.")
        now = self._clock.now()
        profile = TariffProfile(
            id=uuid4(),
            name=definition.name,
            version=definition.version,
            environment_type=definition.environment_type,
            status=ProfileStatus.INACTIVE,
            iva_pct=definition.iva_pct,
            volumetric_divisor=definition.volumetric_divisor,
            effective_from=definition.effective_from or now,
            effective_until=definition.effective_until,
        )
        rules = [TariffRule(**r.model_dump()) for r in definition.rules]
        await self._repo.add(profile, rules, now)
        return profile

    async def activate(self, profile_id: UUID) -> TariffProfile:
        """Activates a version and retires the active one of the same type, atomically."""
        profile = await self._repo.get(profile_id)
        if profile is None:
            raise ContractError(ErrorCode.NO_ENCONTRADO, details={"recurso": "tariff_profile"})
        current = await self._repo.active(profile.environment_type)
        if current is not None and current.id != profile.id:
            await self._repo.set_status(current.id, ProfileStatus.INACTIVE)
        await self._repo.set_status(profile.id, ProfileStatus.ACTIVE)
        return profile

    async def deactivate(self, profile_id: UUID) -> None:
        if await self._repo.get(profile_id) is None:
            raise ContractError(ErrorCode.NO_ENCONTRADO, details={"recurso": "tariff_profile"})
        await self._repo.set_status(profile_id, ProfileStatus.INACTIVE)

    async def listing(self) -> Sequence[tuple[TariffProfile, list[TariffRule]]]:
        return [(p, await self._repo.rules(p.id)) for p in await self._repo.all()]
