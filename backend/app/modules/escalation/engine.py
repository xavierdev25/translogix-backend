"""Escalation policy engine (P07): a pure, explainable function of ``EscalationInput``.

Everything comes from the official ``reglas_escalacion_humana.json``
(thresholds, keywords, actions, customer messages); nothing is duplicated
here. Provisional decisions are configuration and are echoed in
``config_snapshot`` (rule 4 of the common block):

* ``CRIT003_MODE`` (DEC-E2): ``claim_context`` evaluates CRIT-003 only when the
  intent is PQRS; ``literal`` whenever the amounts are known.
* ``CRIT005_MODE`` (DEC-E3): ``incident_context`` needs an incident (guía with a
  novelty status, a PQRS, or one reported by the user); ``literal`` fires on any
  dangerous-goods shipment.
* A rule whose data is missing is skipped and listed, never assumed.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.contracts.conversation import Intent, Sentiment
from app.contracts.escalation import (
    DEFAULT_PRIORITY_ORDER,
    RULES_VERSION,
    CriterionId,
    EscalationAction,
    EscalationDecision,
    EscalationInput,
    EscalationTrigger,
    parse_action,
)
from app.shared.text import tokens

INCIDENT_STATUSES = frozenset({"RETENIDO_NOVEDAD", "EN_INVESTIGACION_SINIESTRO"})

type Fire = Callable[[CriterionId, str, str], None]


@dataclass(frozen=True)
class Criterion:
    id: CriterionId
    action: EscalationAction
    action_raw: str
    message: str


@dataclass(frozen=True)
class EscalationRules:
    version: str
    confidence_min: float
    keywords: tuple[tuple[str, ...], ...]
    claim_limit_cop: int
    corporate_shipments: int
    corporate_account_type: str
    max_failed_deliveries: int
    criteria: dict[str, Criterion]

    @classmethod
    def load(cls, path: Path) -> "EscalationRules":
        raw = json.loads(path.read_text(encoding="utf-8"))
        by_id = {c["id_criterio"]: c for c in raw["criterios_escalacion"]}
        crit003 = by_id["CRIT-003"]["regla_evaluacion"]
        crit004 = by_id["CRIT-004"]["regla_evaluacion"]
        return cls(
            version=raw["version_reglas"],
            confidence_min=float(raw["parametros_generales"]["confidence_score_minimo"]),
            keywords=tuple(tuple(tokens(k)) for k in by_id["CRIT-002"]["keywords_disparadoras"]),
            claim_limit_cop=int(re.findall(r"\d+", crit003)[0]),
            corporate_shipments=int(re.findall(r"envios_mensuales_cuenta\s*>\s*(\d+)", crit004)[0]),
            corporate_account_type=re.findall(r"tipo_cuenta\s*==\s*'([A-Z_]+)'", crit004)[0],
            max_failed_deliveries=int(
                raw["parametros_generales"]["maximo_intentos_fallidos_entregados"]
            ),
            criteria={
                cid: Criterion(
                    id=cid,
                    action=parse_action(c["accion"]),
                    action_raw=c["accion"],
                    message=c["mensaje_cliente"],
                )
                for cid, c in by_id.items()
            },
        )


def _contains_phrase(words: list[str], phrase: tuple[str, ...]) -> bool:
    size = len(phrase)
    return any(tuple(words[i : i + size]) == phrase for i in range(len(words) - size + 1))


class EscalationEngine:
    """``EscalationPort``. Pure: no I/O, no LLM, no clock."""

    def __init__(
        self,
        rules: EscalationRules,
        *,
        confidence_threshold: float | None = None,
        crit003_mode: Literal["claim_context", "literal"] = "claim_context",
        crit005_mode: Literal["incident_context", "literal"] = "incident_context",
        priority_order: tuple[CriterionId, ...] = DEFAULT_PRIORITY_ORDER,
    ) -> None:
        self._rules = rules
        self._threshold = (
            rules.confidence_min if confidence_threshold is None else confidence_threshold
        )
        self._crit003_mode = crit003_mode
        self._crit005_mode = crit005_mode
        self._order = priority_order

    def evaluate(self, data: EscalationInput) -> EscalationDecision:
        triggers: list[EscalationTrigger] = []
        skipped: list[str] = []

        def fire(cid: CriterionId, evidence: str, mode: str) -> None:
            if cid == "INFO-INTENTOS":
                action, raw = (
                    EscalationAction.OFRECER_HANDOFF,
                    EscalationAction.OFRECER_HANDOFF.value,
                )
            else:
                criterion = self._rules.criteria[cid]
                action, raw = criterion.action, criterion.action_raw
            triggers.append(
                EscalationTrigger(
                    id_criterio=cid, accion=action, accion_raw=raw, evidencia=evidence, modo=mode
                )
            )

        # CRIT-001: only when a RAG answer produced a score (None never fires).
        if data.confidence_score is None:
            skipped.append("CRIT-001:sin_respuesta_rag")
        elif data.confidence_score < self._threshold:
            fire(
                "CRIT-001",
                f"confidence_score={data.confidence_score:.2f} < {self._threshold}",
                "literal",
            )

        # CRIT-002: whole-token keywords from the JSON, strong negative sentiment, explicit request.
        words = tokens(data.user_text_normalized)
        hits = [" ".join(k) for k in self._rules.keywords if _contains_phrase(words, k)]
        if hits:
            fire("CRIT-002", "palabras clave: " + ", ".join(hits), "literal")
        elif data.sentiment is Sentiment.MUY_NEGATIVO:
            fire("CRIT-002", "sentimiento=MUY_NEGATIVO", "sentiment")
        elif data.intent is Intent.HABLAR_CON_HUMANO:
            fire("CRIT-002", "solicitud explícita de asesor", "intent")

        self._crit003(data, fire, skipped)

        # CRIT-004
        if data.envios_mensuales_cuenta is None and data.tipo_cuenta is None:
            skipped.append("CRIT-004:sin_datos_cuenta")
        elif (data.envios_mensuales_cuenta or 0) > self._rules.corporate_shipments:
            fire(
                "CRIT-004",
                f"envios_mensuales_cuenta > {self._rules.corporate_shipments}",
                "literal",
            )
        elif data.tipo_cuenta == self._rules.corporate_account_type:
            fire("CRIT-004", f"tipo_cuenta={data.tipo_cuenta}", "literal")

        self._crit005(data, fire, skipped)

        # INFO-INTENTOS (DEC-E4): non-binding offer.
        if data.intentos_entrega_fallidos is None:
            skipped.append("INFO-INTENTOS:sin_dato")
        elif data.intentos_entrega_fallidos >= self._rules.max_failed_deliveries:
            fire(
                "INFO-INTENTOS",
                f"intentos_entrega_fallidos={data.intentos_entrega_fallidos}",
                "literal",
            )

        binding = [t for t in triggers if t.accion is not EscalationAction.OFRECER_HANDOFF]
        binding.sort(key=lambda t: self._order.index(t.id_criterio))
        primary = binding[0] if binding else None
        return EscalationDecision(
            escalate=primary is not None,
            primary=primary,
            triggers=sorted(triggers, key=lambda t: self._order.index(t.id_criterio)),
            mensaje_cliente=self._rules.criteria[primary.id_criterio].message if primary else None,
            rules_version=self._rules.version or RULES_VERSION,
            config_snapshot={
                "CRIT003_MODE": self._crit003_mode,
                "CRIT005_MODE": self._crit005_mode,
                "CONFIDENCE_THRESHOLD": str(self._threshold),
                "PRIORITY_ORDER": ">".join(self._order),
                "omitidas": ",".join(skipped),
            },
        )

    def _crit003(self, data: EscalationInput, fire: Fire, skipped: list[str]) -> None:
        amounts = [
            v for v in (data.valor_reclamacion_cop, data.valor_declarado_cop) if v is not None
        ]
        if not amounts:
            skipped.append("CRIT-003:sin_valores")
            return
        if self._crit003_mode == "claim_context" and data.intent is not Intent.PQRS:
            skipped.append("CRIT-003:fuera_de_contexto_de_reclamo")
            return
        limit = self._rules.claim_limit_cop
        if max(amounts) > limit:
            field = (
                "valor_reclamacion_cop"
                if (data.valor_reclamacion_cop or 0) > limit
                else "valor_declarado_cop"
            )
            context = (
                "contexto de reclamación"
                if self._crit003_mode == "claim_context"
                else "regla literal"
            )
            fire("CRIT-003", f"{field} > {limit} ({context})", self._crit003_mode)

    def _crit005(self, data: EscalationInput, fire: Fire, skipped: list[str]) -> None:
        if data.mercancia_peligrosa is None and data.requiere_refrigeracion is None:
            skipped.append("CRIT-005:sin_datos_de_carga")
            return
        retained = data.estado_guia == "RETENIDO_NOVEDAD"
        cold_chain_branch = bool(data.requiere_refrigeracion) and retained
        if self._crit005_mode == "literal":
            dangerous_branch = bool(data.mercancia_peligrosa)
        else:
            incident = (
                data.estado_guia in INCIDENT_STATUSES
                or data.intent is Intent.PQRS
                or data.incident_reported
            )
            dangerous_branch = bool(data.mercancia_peligrosa) and incident
        if dangerous_branch:
            fire(
                "CRIT-005",
                f"mercancia_peligrosa=true; estado_guia={data.estado_guia or 'desconocido'}",
                self._crit005_mode,
            )
        elif cold_chain_branch:
            fire("CRIT-005", "requiere_refrigeracion=true; estado_guia=RETENIDO_NOVEDAD", "literal")
