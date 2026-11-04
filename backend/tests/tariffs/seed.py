"""The TEST_ONLY matrix of spec 1.1 §6.2, as the tests expect it (the migration seeds it)."""

from decimal import Decimal

from app.tariffs.domain.engine import TariffRule

SEED_NAME, SEED_VERSION = "matriz-pruebas", 1
DANE = {
    "Barranquilla": "08001",
    "Bucaramanga": "68001",
    "Cali": "76001",
    "Cartagena": "13001",
    "Cúcuta": "54001",
}
SEED_RULES = {
    city: TariffRule(DANE[city], base, Decimal(included), excess)
    for city, base, included, excess in (
        ("Barranquilla", 28_000, "5.5", 5_000),
        ("Bucaramanga", 19_300, "5", 3_600),
        ("Cali", 19_800, "5", 3_700),
        ("Cartagena", 26_900, "5.5", 4_800),
        ("Cúcuta", 23_800, "5", 4_400),
    )
}
