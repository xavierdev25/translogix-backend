"""Tariff matrix (CSV) and coverage directory (JSON), loaded and validated once.

The CSV has a BOM (``utf-8-sig``) and a misspelled column
(``factor_divisitivas_volumetrico``), normalised here; ``porcentaje_seguro = 1.0``
means 1 %.
"""

import csv
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from app.shared.text import fold

_REQUIRED_COLUMNS = {
    "codigo_trayecto",
    "ciudad_origen",
    "ciudad_destino",
    "tarifa_base_kg_real_cop",
    "tarifa_base_kg_volumetrico_cop",
    "factor_divisitivas_volumetrico",
    "flete_minimo_cop",
    "porcentaje_seguro",
    "seguro_minimo_cop",
    "recargo_cadena_frio_pct",
    "recargo_mercancia_peligrosa_cop",
    "tiempo_entrega_estimado_horas",
}
# Spellings people use for the same city; only these, nothing guessed.
_CITY_ALIASES = {"bogota": "Bogotá D.C.", "bogota dc": "Bogotá D.C.", "bogota d c": "Bogotá D.C."}


@dataclass(frozen=True)
class Route:
    codigo: str
    origen: str
    destino: str
    tarifa_kg_real: Decimal
    tarifa_kg_volumetrico: Decimal
    divisor_volumetrico: Decimal
    flete_minimo: int
    porcentaje_seguro: Decimal  # percent: 1.0 = 1 %
    seguro_minimo: int
    recargo_frio_pct: Decimal
    recargo_peligrosa: int
    tiempo_horas: int


@dataclass(frozen=True)
class Coverage:
    codigo_dane: str
    municipio: str
    departamento: str
    tiempo_horas: int
    refrigerado: bool
    peso_maximo_kg: Decimal
    volumen_maximo_m3: Decimal


def city_key(city: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9ñ ]", " ", fold(city)).split())


class TariffData:
    def __init__(self, tariff_csv: Path, coverage_json: Path) -> None:
        self.routes = self._load_routes(tariff_csv)
        self.coverage = self._load_coverage(coverage_json)
        self._cities: dict[str, str] = {}
        for route in self.routes.values():
            self._cities[city_key(route.origen)] = route.origen
            self._cities[city_key(route.destino)] = route.destino
        for place in self.coverage.values():
            self._cities.setdefault(city_key(place.municipio), place.municipio)
        self._cities.update({city_key(a): name for a, name in _CITY_ALIASES.items()})
        # Longest names first, so "santa marta" wins over a shorter overlap.
        self._by_length = sorted(self._cities, key=len, reverse=True)

    @staticmethod
    def _load_routes(path: Path) -> dict[tuple[str, str], Route]:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            missing = _REQUIRED_COLUMNS - set(reader.fieldnames or [])
            if missing:
                raise RuntimeError(f"tariff matrix is missing columns: {sorted(missing)}")
            routes = {}
            for row in reader:
                route = Route(
                    codigo=row["codigo_trayecto"],
                    origen=row["ciudad_origen"],
                    destino=row["ciudad_destino"],
                    tarifa_kg_real=Decimal(row["tarifa_base_kg_real_cop"]),
                    tarifa_kg_volumetrico=Decimal(row["tarifa_base_kg_volumetrico_cop"]),
                    divisor_volumetrico=Decimal(row["factor_divisitivas_volumetrico"]),
                    flete_minimo=int(row["flete_minimo_cop"]),
                    porcentaje_seguro=Decimal(row["porcentaje_seguro"]),
                    seguro_minimo=int(row["seguro_minimo_cop"]),
                    recargo_frio_pct=Decimal(row["recargo_cadena_frio_pct"]),
                    recargo_peligrosa=int(row["recargo_mercancia_peligrosa_cop"]),
                    tiempo_horas=int(row["tiempo_entrega_estimado_horas"]),
                )
                routes[(city_key(route.origen), city_key(route.destino))] = route
        return routes

    @staticmethod
    def _load_coverage(path: Path) -> dict[str, Coverage]:
        places = {}
        for item in json.loads(path.read_text(encoding="utf-8")):
            place = Coverage(
                codigo_dane=str(item["codigo_dane"]),
                municipio=item["municipio"],
                departamento=item["departamento"],
                tiempo_horas=int(item["tiempo_entrega_estandar_horas"]),
                refrigerado=bool(item["servicio_refrigerado_disponible"]),
                peso_maximo_kg=Decimal(str(item["peso_maximo_paquete_kg"])),
                volumen_maximo_m3=Decimal(str(item["volumen_maximo_m3"])),
            )
            places[city_key(place.municipio)] = place
        return places

    def canonical_city(self, text: str) -> str | None:
        return self._cities.get(city_key(text))

    def find_cities(self, text: str) -> list[str]:
        """Known cities mentioned in ``text``, in order of appearance."""
        haystack = f" {city_key(text)} "
        found: list[tuple[int, str]] = []
        taken: list[range] = []
        for key in self._by_length:
            for match in re.finditer(rf" {re.escape(key)} ", haystack):
                span = range(match.start(), match.end())
                if not any(set(span) & set(other) for other in taken):
                    taken.append(span)
                    found.append((match.start(), self._cities[key]))
        names: list[str] = []
        for _, name in sorted(found):
            if name not in names:
                names.append(name)
        return names

    def route(self, origen: str, destino: str) -> Route | None:
        return self.routes.get((city_key(origen), city_key(destino)))

    def coverage_for(self, city: str) -> Coverage | None:
        return self.coverage.get(city_key(city))
