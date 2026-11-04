"""Quote slots collected over several turns (P03 ``QuoteSlots``, P08 extraction).

Extraction is deterministic: cities from the coverage directory, dimensions as
``alto x ancho x largo``, weight with a ``kg`` unit, declared value next to
"valor", "$" or a money unit. A bare number only fills the slot just asked.
"""

import re
from typing import Any

from pydantic import BaseModel

from app.contracts.quotation import CotizacionRequest
from app.modules.conversation.intents import CityFinder
from app.shared.text import fold, parse_cop, parse_number_es

_N = r"\d+(?:[.,]\d+)*"
_DIMS = re.compile(rf"({_N})\s*(?:cm)?\s*[x\u00d7*]\s*({_N})\s*(?:cm)?\s*[x\u00d7*]\s*({_N})")
_WEIGHT = re.compile(rf"({_N})\s*(?:kg|kgs|kilo|kilos|kilogramos)\b")
_VALUE = re.compile(
    rf"(?:valor(?:\s+declarado)?(?:\s+(?:es|de|del|son))*\s*:?\s*\$?\s*({_N}\s*(?:millones|millon|mil)?))"
    rf"|(?:\$\s*({_N}\s*(?:millones|millon|mil)?))"
    rf"|(?:({_N}\s*(?:de\s+)?(?:millones|millon|mil|pesos|cop))\b)"
)
_BARE_NUMBER = re.compile(rf"^\s*\$?\s*({_N})\s*(?:millones|mil|pesos|kg|kilos)?\s*\.?\s*$")
ORDER = ("origen", "destino", "dimensiones", "peso_real_kg", "valor_declarado_cop")
QUESTIONS = {
    "origen": "¿Desde qué ciudad sale el envío?",
    "destino": "¿A qué ciudad va el envío?",
    "dimensiones": (
        "¿Cuáles son las medidas del paquete? Escríbelas como alto x ancho x largo en "
        "centímetros, por ejemplo 40x50x60."
    ),
    "peso_real_kg": "¿Cuánto pesa el paquete en kilogramos?",
    "valor_declarado_cop": "¿Cuál es el valor declarado de la mercancía en pesos?",
}


class QuoteSlots(BaseModel):
    origen: str | None = None
    destino: str | None = None
    alto_cm: float | None = None
    ancho_cm: float | None = None
    largo_cm: float | None = None
    peso_real_kg: float | None = None
    valor_declarado_cop: int | None = None
    requiere_cadena_frio: bool = False
    mercancia_peligrosa: bool = False
    pending: str | None = None

    def missing(self) -> list[str]:
        values = {
            "origen": self.origen,
            "destino": self.destino,
            "dimensiones": self.alto_cm and self.ancho_cm and self.largo_cm,
            "peso_real_kg": self.peso_real_kg,
            "valor_declarado_cop": self.valor_declarado_cop,
        }
        return [name for name in ORDER if not values[name]]

    def to_request(self) -> CotizacionRequest:
        return CotizacionRequest.model_validate(
            self.model_dump(exclude={"pending"}) | {"valor_declarado_cop": self.valor_declarado_cop}
        )

    def update_from(self, text: str, cities: CityFinder) -> list[str]:
        """Fill slots from ``text``; returns the names that changed."""
        changed: list[str] = []
        folded = fold(text)

        found = cities.find_cities(text)
        if len(found) >= 2:
            self.origen, self.destino = found[0], found[1]
            changed += ["origen", "destino"]
        elif len(found) == 1:
            target = (
                self.pending
                if self.pending in ("origen", "destino")
                else ("origen" if not self.origen else "destino")
            )
            setattr(self, target, found[0])
            changed.append(target)

        dims = _DIMS.search(folded)
        if dims:
            alto, ancho, largo = (parse_number_es(g) for g in dims.groups())
            if alto and ancho and largo:
                self.alto_cm, self.ancho_cm, self.largo_cm = float(alto), float(ancho), float(largo)
                changed.append("dimensiones")

        weight = _WEIGHT.search(folded)
        if weight and (value := parse_number_es(weight.group(1))):
            self.peso_real_kg = float(value)
            changed.append("peso_real_kg")

        money = _VALUE.search(folded)
        if money:
            amount = parse_cop(next(g for g in money.groups() if g))
            if amount is not None:
                self.valor_declarado_cop = amount
                changed.append("valor_declarado_cop")

        bare = _BARE_NUMBER.match(folded)
        if bare and not changed and self.pending in ("peso_real_kg", "valor_declarado_cop"):
            if self.pending == "peso_real_kg" and (value := parse_number_es(bare.group(1))):
                self.peso_real_kg = float(value)
            elif self.pending == "valor_declarado_cop":
                self.valor_declarado_cop = parse_cop(bare.group(1))
            changed.append(self.pending)

        if re.search(r"cadena de frio|refrigerad", folded):
            self.requiere_cadena_frio = True
            changed.append("requiere_cadena_frio")
        if re.search(r"mercancia peligrosa|inflamable|corrosiv", folded):
            self.mercancia_peligrosa = True
            changed.append("mercancia_peligrosa")
        return changed

    def dump(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
