#!/usr/bin/env python3
"""
Seed script: inserts rich mock data into the TransLogix AI database
for the frontend competition. Designed to populate all tables with
realistic Colombian logistics scenarios across all channels.

Usage:
    python3 scripts/seed_mock_data.py

Requires: psycopg2-binary (or psycopg2)
"""

import asyncio
import os
import json
import uuid
import hashlib
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg  # type: ignore

# ── Config ──────────────────────────────────────────────────────────────
DB_DSN = "postgresql://postgres@localhost:54329/translogix"
COL_TZ = timezone(timedelta(hours=-5))
NOW = datetime.now(COL_TZ)
def _from_env(name: str) -> str:
    """Read a setting from the environment or the repo's .env (the repo is public: no literals)."""
    value = os.environ.get(name, "")
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if not value and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}="):
                value = line.split("=", 1)[1].strip()
    if not value:
        raise SystemExit(f"{name} no está definido (variable de entorno o .env)")
    return value


PII_SALT = _from_env("PII_HASH_SALT")

CHANNELS = ["WEB_CHAT", "WHATSAPP", "PBX"]
INTENTS = [
    "TRACKING", "COTIZACION", "CONSULTA_DOCUMENTAL",
    "PQRS", "SALUDO", "HABLAR_CON_HUMANO", "CARGA_ESPECIAL",
]
SENTIMENTS = ["POSITIVO", "NEUTRO", "NEGATIVO", "MUY_NEGATIVO"]
CONV_STATUSES = ["ACTIVE", "AWAITING_USER", "IDLE", "ESCALATED", "CLOSED"]
HANDOFF_STATUSES = ["PENDING", "ASSIGNED", "CLOSED"]
COLAS = ["SERVICIO_CLIENTE", "SUPERVISOR", "JURIDICO_PQRS", "EJECUTIVO_CUENTA", "OPERACIONES"]
PRIORIDADES = ["NORMAL", "ALTA", "URGENTE"]

GUIAS = ["TLX-984021-CO", "TLX-752190-CO", "TLX-310482-CO", "TLX-551904-CO", "TLX-118493-CO"]

RUTAS = [
    {"origen": "Bogotá D.C.", "destino": "Medellín", "codigo": "TLX-BOG-MED-001", "horas": 24, "tarifa": 1600, "min": 18500},
    {"origen": "Bogotá D.C.", "destino": "Cali", "codigo": "TLX-BOG-CAL-001", "horas": 18, "tarifa": 1400, "min": 16000},
    {"origen": "Bogotá D.C.", "destino": "Barranquilla", "codigo": "TLX-BOG-BAQ-001", "horas": 36, "tarifa": 1800, "min": 22000},
    {"origen": "Bogotá D.C.", "destino": "Bucaramanga", "codigo": "TLX-BOG-BUC-001", "horas": 14, "tarifa": 1500, "min": 17000},
    {"origen": "Bogotá D.C.", "destino": "Pereira", "codigo": "TLX-BOG-PER-001", "horas": 12, "tarifa": 1300, "min": 15000},
    {"origen": "Medellín", "destino": "Cali", "codigo": "TLX-MED-CAL-001", "horas": 14, "tarifa": 1500, "min": 17500},
    {"origen": "Medellín", "destino": "Cartagena", "codigo": "TLX-MED-CTG-001", "horas": 20, "tarifa": 1700, "min": 20000},
    {"origen": "Cali", "destino": "Pasto", "codigo": "TLX-CAL-PAS-001", "horas": 10, "tarifa": 1400, "min": 14000},
    {"origen": "Barranquilla", "destino": "Santa Marta", "codigo": "TLX-BAQ-STM-001", "horas": 6, "tarifa": 1100, "min": 12000},
    {"origen": "Bogotá D.C.", "destino": "Villavicencio", "codigo": "TLX-BOG-VVC-001", "horas": 8, "tarifa": 1200, "min": 13000},
]

# Realistic user messages for different intents
USER_MESSAGES = {
    "TRACKING": [
        "¿Dónde está mi paquete TLX-551904-CO?",
        "Necesito saber el estado de mi guía TLX-984021-CO",
        "Mi envío no ha llegado, guía TLX-310482-CO",
        "Rastrear TLX-752190-CO",
        "Hola, quiero saber dónde va mi paquete TLX-118493-CO",
        "Buenos días, ¿pueden informarme sobre el envío TLX-551904-CO?",
        "Llevo 3 días esperando mi paquete, la guía es TLX-984021-CO",
        "¿Ya salió mi envío? TLX-118493-CO",
        "Me dijeron que llegaba hoy y no ha llegado TLX-551904-CO",
        "Seguimiento de mi pedido TLX-752190-CO por favor",
    ],
    "COTIZACION": [
        "Quiero cotizar un envío de Bogotá a Medellín, 20kg",
        "¿Cuánto cuesta enviar 5 cajas a Cali?",
        "Necesito una cotización para mercancía refrigerada a Barranquilla",
        "¿Cuál es la tarifa Bogotá-Bucaramanga para 50kg?",
        "Cotizar envío Medellín a Cartagena, paquete pequeño",
        "¿Precio para enviar documentos a Pereira?",
        "Quiero saber cuánto me sale un envío grande a Pasto",
        "Cotización urgente Bogotá-Villavicencio, 15kg",
    ],
    "CONSULTA_DOCUMENTAL": [
        "¿Cuáles son los horarios de atención?",
        "¿Qué documentos necesito para un reclamo?",
        "¿Cómo funciona el seguro de mercancía?",
        "¿Cuáles son las restricciones para mercancía peligrosa?",
        "¿Cuál es la política de embalaje?",
        "¿Tienen servicio de cadena de frío?",
        "¿Qué pasa si mi paquete llega dañado?",
        "¿Cuáles son los tiempos de entrega nacionales?",
        "¿Cómo radico un siniestro?",
        "¿Cuáles son las zonas de cobertura?",
    ],
    "PQRS": [
        "Mi paquete llegó dañado, quiero poner un reclamo",
        "Quiero radicar un siniestro por pérdida total",
        "Mi envío nunca llegó y ya pasaron los días de entrega",
        "Necesito poner una queja por mal servicio",
        "El conductor fue grosero al entregar",
        "Me entregaron un paquete que no era el mío",
    ],
    "SALUDO": [
        "Hola",
        "Buenos días",
        "Buenas tardes, necesito ayuda",
        "Hola, ¿están disponibles?",
    ],
    "HABLAR_CON_HUMANO": [
        "Quiero hablar con un asesor",
        "Pásenme con alguien por favor",
        "No quiero hablar con un robot",
        "Necesito atención humana urgente",
        "Quiero hablar con un supervisor",
    ],
    "CARGA_ESPECIAL": [
        "Necesito transportar productos químicos clase 3",
        "Tengo una carga refrigerada de medicamentos",
        "¿Pueden transportar baterías de litio?",
        "Envío de alimentos perecederos Bogotá-Medellín",
    ],
}

# Bot responses by intent
BOT_RESPONSES = {
    "TRACKING": [
        "Tu envío está en camino. Aquí está el detalle:",
        "Encontré tu guía. Te comparto el estado actualizado:",
        "Aquí tienes la información de tu envío:",
    ],
    "COTIZACION": [
        "¡Listo! Aquí tienes tu cotización:",
        "Te presento el resumen de costos para tu envío:",
        "Con gusto. Esta es la cotización para tu ruta:",
    ],
    "CONSULTA_DOCUMENTAL": [
        "Según nuestros manuales de operación:",
        "Con base en la documentación oficial de TransLogix:",
        "Aquí encontré la información que necesitas:",
    ],
    "PQRS": [
        "Lamento la situación. Voy a ayudarte con tu reclamo.",
        "Entiendo tu inconformidad. Te creo un ticket de seguimiento.",
        "Registraré tu caso inmediatamente.",
    ],
    "SALUDO": [
        "¡Hola! Soy el asistente virtual de TransLogix. ¿En qué puedo ayudarte hoy?",
        "Buenos días. Estoy aquí para ayudarte con envíos, rastreo, cotizaciones y más.",
    ],
    "HABLAR_CON_HUMANO": [
        "Entiendo, te conecto con un asesor de inmediato.",
        "Te transfiero ahora con un agente humano.",
    ],
    "CARGA_ESPECIAL": [
        "Para cargas especiales necesitamos validar requisitos adicionales.",
        "Las cargas peligrosas requieren documentación especial. Te explico:",
    ],
}


def uid():
    return str(uuid.uuid4())


def hash_user(user_id: str) -> str:
    return hashlib.sha256(f"{PII_SALT}{user_id}".encode()).hexdigest()


def ts(hours_ago: float = 0, minutes_ago: float = 0) -> str:
    dt = NOW - timedelta(hours=hours_ago, minutes=minutes_ago)
    return dt.isoformat()


def make_conversation(
    session_id, channel, status, intent, sentiment, user_hash,
    guia=None, handoff_id=None, fallback_count=0,
    hours_ago=0, minutes_ago=0,
):
    created = ts(hours_ago, minutes_ago)
    updated = ts(hours_ago, max(0, minutes_ago - random.randint(1, 10)))
    state = {
        "session_id": session_id,
        "channel": channel,
        "external_user_id_hash": user_hash,
        "status": status,
        "active_intent": intent,
        "slots": {},
        "numero_guia_consultada": guia,
        "confidence_scores": [round(random.uniform(0.6, 0.98), 4)] if intent == "CONSULTA_DOCUMENTAL" else [],
        "sentiment": sentiment,
        "fallback_count": fallback_count,
        "handoff_id": handoff_id,
        "created_at": created,
        "updated_at": updated,
    }
    return (session_id, channel, user_hash, status, json.dumps(state), created, updated)


def make_inbound_message(msg_id, session_id, channel, text, created_at, msg_type="TEXT"):
    content = {
        "content": {
            "text": text,
            "attachments": [],
            "asr": None,
            "system_event": None,
            "interactive_reply_id": None,
        }
    }
    return (uid(), session_id, channel, "INBOUND", msg_id, None, msg_type,
            json.dumps(content), "SIMULATED", created_at)


def make_outbound_message(session_id, channel, in_reply_to, blocks, status, intent,
                          created_at, trace_id=None, services=None, evidence=None,
                          confidence=None, llm_used=True, escalation=None):
    reply_id = uid()
    reply = {
        "reply": {
            "session_id": session_id,
            "in_reply_to": in_reply_to,
            "reply_id": reply_id,
            "channel": channel,
            "status": status,
            "blocks": blocks,
            "meta": {
                "trace_id": trace_id or uid(),
                "intent": intent,
                "confidence_score": confidence,
                "confidence_method": "heuristic_v1" if confidence else None,
                "escalation": escalation or {
                    "escalate": False, "primary": None, "triggers": [],
                    "rules_version": "1.0.2026",
                    "config_snapshot": {
                        "CRIT003_MODE": "claim_context",
                        "CRIT005_MODE": "incident_context",
                        "CONFIDENCE_THRESHOLD": "0.75",
                        "PRIORITY_ORDER": "CRIT-005>CRIT-003>CRIT-002>CRIT-004>CRIT-001>INFO-INTENTOS",
                    },
                    "mensaje_cliente": None,
                },
                "services_called": services or ["rag", "escalation"],
                "evidence_ids": evidence or [],
                "llm_used": llm_used,
                "delivery_mode": "SIMULATED",
            },
            "created_at": created_at,
        }
    }
    return (uid(), session_id, channel, "OUTBOUND", reply_id, in_reply_to, None,
            json.dumps(reply), "SIMULATED", created_at)


def make_tracking_blocks(guia):
    estados = {
        "TLX-984021-CO": ("EN_TRANSITO", "En tránsito", "Tu envío va en carretera hacia la ciudad de destino."),
        "TLX-752190-CO": ("ENTREGADO", "Entregado", "Tu envío fue entregado exitosamente."),
        "TLX-310482-CO": ("RETENIDO_NOVEDAD", "Retenido por novedad", "Tu envío tiene una novedad que estamos resolviendo."),
        "TLX-551904-CO": ("EN_REPARTO", "En reparto", "Tu envío está en el vehículo de reparto."),
        "TLX-118493-CO": ("EN_BODEGA_ORIGEN", "En bodega de origen", "Tu envío está siendo preparado para despacho."),
    }
    codigo, etiqueta, explicacion = estados.get(guia, ("EN_TRANSITO", "En tránsito", "En camino."))
    return [
        {"type": "TEXT", "text": f"Guía {guia}: {etiqueta}. {explicacion}"},
        {
            "type": "TRACKING_CARD",
            "numero_guia": guia,
            "estado": {"codigo": codigo, "etiqueta": etiqueta, "explicacion": explicacion},
            "eta": ts(hours_ago=-random.randint(2, 24)),
            "ubicacion_referencia": random.choice([
                "Hub Central Bogotá", "Corredor Bogotá-Medellín",
                "Hub Eje Cafetero", "Ruta urbana Cali",
                "Centro de distribución Barranquilla",
            ]),
            "eventos": [
                {
                    "estado": "RECOGIDO_EN_ORIGEN", "etiqueta": "Recogido en origen",
                    "timestamp": ts(hours_ago=48), "ubicacion": "Bogotá D.C.",
                    "descripcion_cliente": "Paquete recogido e ingresado al sistema.",
                },
                {
                    "estado": "EN_BODEGA_ORIGEN", "etiqueta": "En bodega de origen",
                    "timestamp": ts(hours_ago=36), "ubicacion": "Hub Central Fontibón",
                    "descripcion_cliente": "Clasificación y consolidación completada.",
                },
                {
                    "estado": codigo, "etiqueta": etiqueta,
                    "timestamp": ts(hours_ago=random.randint(1, 12)),
                    "ubicacion": random.choice(["Peaje Chicoral", "Hub Itagüí", "Pereira"]),
                    "descripcion_cliente": explicacion,
                },
            ],
            "flags": {
                "requiere_refrigeracion": guia == "TLX-984021-CO",
                "mercancia_peligrosa": guia == "TLX-310482-CO",
            },
        },
        {
            "type": "QUICK_REPLIES",
            "options": [
                {"id": "cotizar", "title": "Cotizar un envío"},
                {"id": "asesor", "title": "Hablar con un asesor"},
            ],
        },
    ]


def make_quote_blocks(cotizacion_id, ruta):
    peso = round(random.uniform(2, 80), 1)
    flete_base = max(ruta["min"], int(peso * ruta["tarifa"]))
    seguro = random.choice([2500, 3500, 5000, 12500])
    total = flete_base + seguro
    return [
        {"type": "TEXT", "text": f"Tu cotización de {ruta['origen']} a {ruta['destino']}: ${total:,} COP."},
        {
            "type": "QUOTE_SUMMARY",
            "cotizacion_id": cotizacion_id,
            "codigo_trayecto": ruta["codigo"],
            "origen": ruta["origen"],
            "destino": ruta["destino"],
            "peso_facturable_kg": peso,
            "flete_total_cop": total,
            "tiempo_entrega_horas": ruta["horas"],
            "formula_version": "provisional-v1",
            "advertencias": [],
        },
        {
            "type": "QUICK_REPLIES",
            "options": [
                {"id": "rastrear", "title": "Rastrear un envío"},
                {"id": "asesor", "title": "Hablar con un asesor"},
            ],
        },
    ]


def make_escalation_decision(trigger_id, action, evidence, cola, prioridad, mensaje):
    trigger = {
        "id_criterio": trigger_id,
        "accion": action,
        "accion_raw": action,
        "evidencia": evidence,
        "modo": "literal",
    }
    return {
        "escalate": True,
        "primary": trigger,
        "triggers": [trigger],
        "mensaje_cliente": mensaje,
        "rules_version": "1.0.2026",
        "config_snapshot": {
            "CRIT003_MODE": "claim_context",
            "CRIT005_MODE": "incident_context",
            "CONFIDENCE_THRESHOLD": "0.75",
            "PRIORITY_ORDER": "CRIT-005>CRIT-003>CRIT-002>CRIT-004>CRIT-001>INFO-INTENTOS",
        },
    }


def make_handoff_payload(handoff_id, session_id, channel, guia, cola, prioridad,
                         trigger_id, action, evidence, sentiment, created_at, history_text):
    trigger = {
        "id_criterio": trigger_id,
        "accion": action,
        "accion_raw": action,
        "evidencia": evidence,
        "modo": "literal",
    }
    return {
        "handoff_id": handoff_id,
        "session_id": session_id,
        "canal": channel,
        "numero_guia_consultada": guia,
        "confidence_score_promedio": round(random.uniform(0.5, 0.74), 4) if trigger_id == "CRIT-001" else None,
        "sentimiento_detectado": sentiment,
        "intentos_previos": random.randint(0, 3),
        "resumen_contexto": f"Intención: {random.choice(INTENTS)}. Canal: {channel}. {evidence}",
        "historial": [
            {"rol": "USUARIO", "texto": history_text, "timestamp": created_at, "canal": channel},
        ],
        "prioridad": prioridad,
        "cola_destino": cola,
        "triggers": [trigger],
        "tiempo_espera_estimado_segundos": 45,
        "status": "PENDING",
        "created_at": created_at,
    }


def make_quote_record(cotizacion_id, session_id, ruta, created_at):
    peso_real = round(random.uniform(2, 80), 1)
    alto = round(random.uniform(10, 80), 1)
    ancho = round(random.uniform(10, 80), 1)
    largo = round(random.uniform(10, 100), 1)
    peso_vol = round((alto * ancho * largo) / 4000, 1)
    peso_fact = max(peso_real, peso_vol)
    tarifa_tipo = "VOLUMETRICO" if peso_vol > peso_real else "REAL"
    flete_calc = int(peso_fact * ruta["tarifa"])
    flete_minimo_aplicado = flete_calc < ruta["min"]
    flete_base = max(ruta["min"], flete_calc)
    valor_declarado = random.choice([150000, 500000, 1200000, 3500000, 8000000, 15000000])
    seguro = max(2500, int(valor_declarado * 0.01))
    recargos = []
    cadena_frio = random.random() < 0.15
    mercancias_p = random.random() < 0.1
    if cadena_frio:
        r_val = int(flete_base * 0.25)
        recargos.append({"codigo": "CADENA_FRIO", "descripcion": "Recargo cadena de frío", "valor_cop": r_val})
    if mercancias_p:
        r_val = int(flete_base * 0.30)
        recargos.append({"codigo": "MERCANCIA_PELIGROSA", "descripcion": "Recargo mercancía peligrosa", "valor_cop": r_val})

    flete_total = flete_base + seguro + sum(r["valor_cop"] for r in recargos)

    request_json = {
        "origen": ruta["origen"], "destino": ruta["destino"],
        "alto_cm": alto, "ancho_cm": ancho, "largo_cm": largo,
        "peso_real_kg": peso_real, "valor_declarado_cop": valor_declarado,
        "requiere_cadena_frio": cadena_frio, "mercancia_peligrosa": mercancias_p,
    }
    response_json = {
        "cotizacion_id": cotizacion_id,
        "ruta": {"origen": ruta["origen"], "destino": ruta["destino"], "codigo_trayecto": ruta["codigo"]},
        "calculo": {
            "peso_volumetrico_kg": peso_vol, "peso_facturable_kg": peso_fact,
            "tarifa_aplicada": {"tipo": tarifa_tipo, "cop_por_kg": ruta["tarifa"]},
            "flete_minimo_cop": ruta["min"], "flete_minimo_aplicado": flete_minimo_aplicado,
            "flete_base_cop": flete_base, "seguro_cop": seguro,
            "recargos": recargos, "flete_total_cop": flete_total,
        },
        "tiempo_entrega_horas": ruta["horas"],
        "advertencias": ["Tarifa sujeta a cambio sin previo aviso."] if random.random() < 0.3 else [],
        "supuestos": ["formula_version=provisional-v1"],
        "formula_version": "provisional-v1",
    }
    return (cotizacion_id, session_id, json.dumps(request_json), json.dumps(response_json), "provisional-v1", created_at)


def make_pqrs_record(ticket_id, session_id, tipo, guia, estado, created_at):
    tipo_novedad = random.choice(["AVERIA_APARENTE", "AVERIA_NO_APARENTE", "PERDIDA_TOTAL", "SAQUEO"]) if tipo == "SINIESTRO" else None
    valor_reclamado = random.choice([500000, 1500000, 3000000, 4000000, 8000000]) if tipo == "SINIESTRO" else None
    descripciones = {
        "PETICION": "Solicito información detallada del estado de mi envío y la fecha exacta de entrega.",
        "QUEJA": "El servicio al cliente fue muy demorado y no me dieron solución a mi problema.",
        "RECLAMO": "Mi paquete tiene un retraso de más de 5 días hábiles sin justificación.",
        "SINIESTRO": "Mi mercancía llegó con daños evidentes. Solicito indemnización.",
    }

    checklist = [
        {"codigo": "GUIA", "obligatorio": True, "recibido": True},
        {"codigo": "FACTURA_COMERCIAL", "obligatorio": True, "recibido": random.random() < 0.5},
        {"codigo": "CARTA_RECLAMACION", "obligatorio": True, "recibido": random.random() < 0.3},
        {"codigo": "CERTIFICACION_BANCARIA", "obligatorio": True, "recibido": random.random() < 0.2},
        {"codigo": "FOTOS_EMPAQUE", "obligatorio": True, "recibido": random.random() < 0.4},
        {"codigo": "FOTOS_DANO", "obligatorio": True, "recibido": random.random() < 0.3},
        {"codigo": "CEDULA_O_RUT", "obligatorio": True, "recibido": random.random() < 0.5},
        {"codigo": "ACTA_CON_SALVEDAD", "obligatorio": False, "recibido": False},
    ]
    faltantes = [c["codigo"] for c in checklist if c["obligatorio"] and not c["recibido"]]

    esc = None
    if estado == "ESCALADO":
        esc = make_escalation_decision(
            "CRIT-003", "ESCALAR_A_DEPARTAMENTO_JURIDICO_Y_PQRS",
            f"valor_declarado_cop > 5000000 (contexto de reclamación)",
            "JURIDICO_PQRS", "ALTA",
            "Un gestor senior de PQRS tomará tu caso.",
        )

    liq = None
    if tipo == "SINIESTRO" and valor_reclamado:
        ded_pct = 5
        ded_cop = int(valor_reclamado * ded_pct / 100)
        liq = {
            "estado": "PRELIMINAR_NO_VINCULANTE",
            "base_cop": valor_reclamado,
            "deducible_pct": ded_pct,
            "deducible_cop": ded_cop,
            "flete_devuelto": "PENDIENTE_REVISION_HUMANA",
        }

    payload = {
        "request": {
            "session_id": session_id,
            "tipo": tipo,
            "numero_guia": guia,
            "tipo_novedad": tipo_novedad,
            "descripcion": descripciones[tipo],
            "valor_reclamado_cop": valor_reclamado,
            "documentos": [],
        },
        "response": {
            "ticket_id": ticket_id,
            "estado": estado,
            "elegibilidad": {
                "dentro_de_plazo": True,
                "regla_aplicada": f"{tipo_novedad}_3_DIAS_HABILES" if tipo_novedad else "GENERAL_15_DIAS_HABILES",
                "fecha_limite": (NOW + timedelta(days=random.randint(3, 15))).strftime("%Y-%m-%d"),
                "nota": "días hábiles sin considerar festivos",
                "fundamento": {
                    "doc_code": "DOC-PQRS-2026-04",
                    "section_path": "3. Tiempos y plazos legales de radicación",
                },
            } if tipo == "SINIESTRO" else None,
            "checklist": checklist if tipo == "SINIESTRO" else [],
            "faltantes": faltantes if tipo == "SINIESTRO" else [],
            "liquidacion_preliminar": liq,
            "escalamiento": esc,
        },
    }

    return (ticket_id, session_id, tipo, guia, estado, json.dumps(payload), created_at)


def make_audit_event(session_id, channel, intent, services, evidence, llm_used, created_at, escalation=None):
    return (
        uid(), uid(), session_id, channel, intent,
        json.dumps(services), json.dumps(escalation) if escalation else None,
        json.dumps(evidence), llm_used, random.randint(80, 6500), created_at,
    )


def make_attachment(att_id, session_id, kind, mime, size, doc_code, created_at):
    sha = hashlib.sha256(att_id.encode()).hexdigest()
    return (att_id, session_id, kind, mime, size, sha, doc_code, f"local://{att_id}", created_at)


# ── MAIN ────────────────────────────────────────────────────────────────
def main():
    asyncio.run(_async_main())


async def _async_main():
    conn = await asyncpg.connect(DB_DSN)

    conversations = []
    messages = []
    handoffs = []
    quotes_data = []
    pqrs_data = []
    audit_events = []
    attachments = []

    fake_users = [f"usuario-demo-{i:03d}@translogix.co" for i in range(1, 31)]
    user_hashes = {u: hash_user(u) for u in fake_users}

    print("🏗️  Generando data mock...")

    # ── 1. CONVERSATIONS WITH MESSAGES ───────────────────────────────

    # Group A: Tracking conversations (15 across all channels)
    for i in range(15):
        sid = uid()
        channel = CHANNELS[i % 3]
        user = fake_users[i]
        guia = GUIAS[i % len(GUIAS)]
        status = random.choice(["AWAITING_USER", "CLOSED", "ACTIVE"])
        hours = random.uniform(0.5, 72)

        conversations.append(make_conversation(
            sid, channel, status, "TRACKING", random.choice(["NEUTRO", "POSITIVO"]),
            user_hashes[user], guia=guia, hours_ago=hours,
        ))

        msg_id = uid()
        text = random.choice(USER_MESSAGES["TRACKING"])
        created = ts(hours, random.randint(5, 30))
        messages.append(make_inbound_message(msg_id, sid, channel, text, created))

        blocks = make_tracking_blocks(guia)
        messages.append(make_outbound_message(
            sid, channel, msg_id, blocks, status, "TRACKING", ts(hours, random.randint(0, 4)),
            services=["tms", "escalation"], evidence=[guia], llm_used=False,
        ))

        audit_events.append(make_audit_event(
            sid, channel, "TRACKING", ["tms", "escalation"], [guia], False, created,
        ))

    # Group B: Cotización conversations (12)
    for i in range(12):
        sid = uid()
        channel = CHANNELS[i % 3]
        user = fake_users[15 + (i % 15)]
        ruta = RUTAS[i % len(RUTAS)]
        status = random.choice(["AWAITING_USER", "CLOSED"])
        hours = random.uniform(1, 96)

        conversations.append(make_conversation(
            sid, channel, status, "COTIZACION", "NEUTRO",
            user_hashes[user], hours_ago=hours,
        ))

        msg_id = uid()
        text = random.choice(USER_MESSAGES["COTIZACION"])
        created = ts(hours, random.randint(5, 20))
        messages.append(make_inbound_message(msg_id, sid, channel, text, created))

        cot_id = uid()
        blocks = make_quote_blocks(cot_id, ruta)
        messages.append(make_outbound_message(
            sid, channel, msg_id, blocks, status, "COTIZACION",
            ts(hours, random.randint(0, 4)),
            services=["quotation", "escalation"], evidence=[cot_id], llm_used=False,
        ))

        quotes_data.append(make_quote_record(cot_id, sid, ruta, created))

        audit_events.append(make_audit_event(
            sid, channel, "COTIZACION", ["quotation", "escalation"], [cot_id], False, created,
        ))

    # Group C: Consulta documental (RAG) conversations (10)
    for i in range(10):
        sid = uid()
        channel = CHANNELS[i % 3]
        user = fake_users[i % len(fake_users)]
        confidence = round(random.uniform(0.55, 0.98), 4)
        escalated = confidence < 0.75
        status = "ESCALATED" if escalated else random.choice(["AWAITING_USER", "CLOSED"])
        hours = random.uniform(0.5, 48)
        handoff_id = uid() if escalated else None

        conversations.append(make_conversation(
            sid, channel, status, "CONSULTA_DOCUMENTAL",
            random.choice(SENTIMENTS), user_hashes[user],
            handoff_id=handoff_id, hours_ago=hours,
        ))

        msg_id = uid()
        text = random.choice(USER_MESSAGES["CONSULTA_DOCUMENTAL"])
        created = ts(hours, random.randint(5, 20))
        messages.append(make_inbound_message(msg_id, sid, channel, text, created))

        esc_decision = None
        if escalated:
            esc_decision = make_escalation_decision(
                "CRIT-001", "ESCALAR_A_HUMANO",
                f"confidence_score={confidence:.2f} < 0.75",
                "SERVICIO_CLIENTE", "NORMAL",
                "Te conecto con un asesor para darte una respuesta más precisa.",
            )
            handoffs.append((
                handoff_id, sid, channel,
                json.dumps(make_handoff_payload(
                    handoff_id, sid, channel, None, "SERVICIO_CLIENTE", "NORMAL",
                    "CRIT-001", "ESCALAR_A_HUMANO", f"confidence_score={confidence:.2f} < 0.75",
                    "NEUTRO", created, text,
                )),
                random.choice(HANDOFF_STATUSES), created,
            ))
            blocks = [
                {"type": "HANDOFF_NOTICE", "handoff_id": handoff_id,
                 "prioridad": "NORMAL", "cola": "SERVICIO_CLIENTE",
                 "mensaje_cliente": "Te conecto con un asesor para darte una respuesta más precisa.",
                 "tiempo_espera_estimado_segundos": 45},
            ]
        else:
            evidence_ids = [f"COBERTURA:{random.randint(1,50):04d}", f"PQRS:{random.randint(1,30):04d}"]
            blocks = [
                {"type": "TEXT", "text": random.choice(BOT_RESPONSES["CONSULTA_DOCUMENTAL"]) + " " + text.lower()},
                {"type": "CITATIONS", "items": [
                    {"doc_code": "DOC-COBERTURA-2026-01", "doc_title": "Directorio de Cobertura y Tiempos",
                     "section_path": "2. Cobertura Nacional", "chunk_id": evidence_ids[0],
                     "excerpt": "TransLogix cubre más de 1100 municipios en Colombia..."},
                ]},
                {"type": "QUICK_REPLIES", "options": [
                    {"id": "cotizar", "title": "Cotizar un envío"},
                    {"id": "asesor", "title": "Hablar con un asesor"},
                ]},
            ]

        messages.append(make_outbound_message(
            sid, channel, msg_id, blocks, status, "CONSULTA_DOCUMENTAL",
            ts(hours, random.randint(0, 4)),
            services=["rag", "escalation"],
            evidence=[f"COBERTURA:{random.randint(1,50):04d}"],
            confidence=confidence, llm_used=True, escalation=esc_decision,
        ))

        audit_events.append(make_audit_event(
            sid, channel, "CONSULTA_DOCUMENTAL", ["rag", "escalation"],
            [f"COBERTURA:{random.randint(1,50):04d}"], True, created, esc_decision,
        ))

    # Group D: PQRS conversations (8)
    pqrs_tipos = ["SINIESTRO", "SINIESTRO", "SINIESTRO", "RECLAMO", "QUEJA", "PETICION", "SINIESTRO", "RECLAMO"]
    pqrs_estados = ["ESCALADO", "ESCALADO", "RADICADO", "BORRADOR", "ESCALADO", "BORRADOR", "ESCALADO", "BORRADOR"]
    for i in range(8):
        sid = uid()
        channel = CHANNELS[i % 3]
        user = fake_users[(i + 5) % len(fake_users)]
        guia = GUIAS[i % len(GUIAS)]
        hours = random.uniform(1, 120)
        tipo = pqrs_tipos[i]
        estado_pqrs = pqrs_estados[i]

        # For RADICADO, no escalation, so conversation is AWAITING_USER
        if estado_pqrs == "ESCALADO":
            conv_status = "ESCALATED"
        else:
            conv_status = random.choice(["AWAITING_USER", "ACTIVE"])

        handoff_id = uid() if conv_status == "ESCALATED" else None
        conversations.append(make_conversation(
            sid, channel, conv_status, "PQRS",
            random.choice(["NEGATIVO", "MUY_NEGATIVO"]),
            user_hashes[user], guia=guia, handoff_id=handoff_id,
            hours_ago=hours,
        ))

        msg_id = uid()
        text = random.choice(USER_MESSAGES["PQRS"])
        created = ts(hours, random.randint(5, 30))
        messages.append(make_inbound_message(msg_id, sid, channel, text, created))

        ticket_id = uid()
        pqrs_data.append(make_pqrs_record(ticket_id, sid, tipo, guia, estado_pqrs, created))

        blocks = [{"type": "TEXT", "text": random.choice(BOT_RESPONSES["PQRS"])}]

        esc_decision = None
        if conv_status == "ESCALATED":
            esc_decision = make_escalation_decision(
                "CRIT-003", "ESCALAR_A_DEPARTAMENTO_JURIDICO_Y_PQRS",
                "Reclamo que requiere evaluación del departamento jurídico.",
                "JURIDICO_PQRS", "ALTA",
                "Un gestor senior de PQRS tomará tu caso.",
            )
            handoffs.append((
                handoff_id, sid, channel,
                json.dumps(make_handoff_payload(
                    handoff_id, sid, channel, guia, "JURIDICO_PQRS", "ALTA",
                    "CRIT-003", "ESCALAR_A_DEPARTAMENTO_JURIDICO_Y_PQRS",
                    "Reclamo alto valor", random.choice(["NEGATIVO", "MUY_NEGATIVO"]),
                    created, text,
                )),
                random.choice(["PENDING", "ASSIGNED"]), created,
            ))
            blocks.append({
                "type": "HANDOFF_NOTICE", "handoff_id": handoff_id,
                "prioridad": "ALTA", "cola": "JURIDICO_PQRS",
                "mensaje_cliente": "Un gestor senior de PQRS tomará tu caso.",
                "tiempo_espera_estimado_segundos": 45,
            })
        else:
            blocks.append({
                "type": "PQRS_TICKET", "ticket_id": ticket_id,
                "estado": estado_pqrs,
                "faltantes": ["FACTURA_COMERCIAL", "CARTA_RECLAMACION"] if tipo == "SINIESTRO" else [],
            })

        messages.append(make_outbound_message(
            sid, channel, msg_id, blocks, conv_status, "PQRS",
            ts(hours, random.randint(0, 4)),
            services=["pqrs", "escalation"], evidence=[ticket_id],
            llm_used=True, escalation=esc_decision,
        ))

        audit_events.append(make_audit_event(
            sid, channel, "PQRS", ["pqrs", "escalation"], [ticket_id], True, created, esc_decision,
        ))

    # Group E: Escalated by human request / sentiment (5)
    for i in range(5):
        sid = uid()
        channel = CHANNELS[i % 3]
        user = fake_users[(i + 10) % len(fake_users)]
        hours = random.uniform(0.5, 24)
        handoff_id = uid()

        conversations.append(make_conversation(
            sid, channel, "ESCALATED", "HABLAR_CON_HUMANO", "NEGATIVO",
            user_hashes[user], handoff_id=handoff_id, hours_ago=hours,
        ))

        msg_id = uid()
        text = random.choice(USER_MESSAGES["HABLAR_CON_HUMANO"])
        created = ts(hours, random.randint(3, 15))
        messages.append(make_inbound_message(msg_id, sid, channel, text, created))

        esc_decision = make_escalation_decision(
            "CRIT-002", "ESCALAR_PRIORIDAD_ALTA",
            "El usuario solicitó hablar con un humano.",
            "SUPERVISOR", "ALTA",
            "Te conecto con un supervisor de inmediato.",
        )
        handoffs.append((
            handoff_id, sid, channel,
            json.dumps(make_handoff_payload(
                handoff_id, sid, channel, None, "SUPERVISOR", "ALTA",
                "CRIT-002", "ESCALAR_PRIORIDAD_ALTA",
                "solicitud explícita de humano", "NEGATIVO", created, text,
            )),
            random.choice(HANDOFF_STATUSES), created,
        ))

        blocks = [
            {"type": "HANDOFF_NOTICE", "handoff_id": handoff_id,
             "prioridad": "ALTA", "cola": "SUPERVISOR",
             "mensaje_cliente": "Te conecto con un supervisor de inmediato.",
             "tiempo_espera_estimado_segundos": 45},
        ]
        messages.append(make_outbound_message(
            sid, channel, msg_id, blocks, "ESCALATED", "HABLAR_CON_HUMANO",
            ts(hours, random.randint(0, 2)),
            services=["escalation"], evidence=[], llm_used=False,
            escalation=esc_decision,
        ))
        audit_events.append(make_audit_event(
            sid, channel, "HABLAR_CON_HUMANO", ["escalation"], [], False, created, esc_decision,
        ))

    # Group F: CRIT-005 Urgency Operations (3) — hazmat/incident
    for i in range(3):
        sid = uid()
        channel = CHANNELS[i % 3]
        user = fake_users[(i + 20) % len(fake_users)]
        guia = "TLX-310482-CO"
        hours = random.uniform(0.2, 6)
        handoff_id = uid()

        conversations.append(make_conversation(
            sid, channel, "ESCALATED", "TRACKING", "MUY_NEGATIVO",
            user_hashes[user], guia=guia, handoff_id=handoff_id, hours_ago=hours,
        ))

        msg_id = uid()
        text = f"La carga peligrosa guía {guia} está retenida y hay un problema grave"
        created = ts(hours, random.randint(2, 10))
        messages.append(make_inbound_message(msg_id, sid, channel, text, created))

        esc_decision = make_escalation_decision(
            "CRIT-005", "ESCALAR_URGENCIA_OPERACIONES",
            "mercancia_peligrosa=true; estado_guia=RETENIDO_NOVEDAD",
            "OPERACIONES", "URGENTE",
            "Tu caso ha sido escalado al equipo de operaciones con prioridad urgente.",
        )
        handoffs.append((
            handoff_id, sid, channel,
            json.dumps(make_handoff_payload(
                handoff_id, sid, channel, guia, "OPERACIONES", "URGENTE",
                "CRIT-005", "ESCALAR_URGENCIA_OPERACIONES",
                "mercancia_peligrosa=true", "MUY_NEGATIVO", created, text,
            )),
            "PENDING", created,
        ))

        blocks = [
            {"type": "TEXT", "text": f"Guía {guia}: Retenido por novedad."},
            {"type": "HANDOFF_NOTICE", "handoff_id": handoff_id,
             "prioridad": "URGENTE", "cola": "OPERACIONES",
             "mensaje_cliente": "Tu caso ha sido escalado al equipo de operaciones con prioridad urgente.",
             "tiempo_espera_estimado_segundos": 45},
        ]
        messages.append(make_outbound_message(
            sid, channel, msg_id, blocks, "ESCALATED", "TRACKING",
            ts(hours, random.randint(0, 1)),
            services=["tms", "escalation"], evidence=[guia], llm_used=False,
            escalation=esc_decision,
        ))
        audit_events.append(make_audit_event(
            sid, channel, "TRACKING", ["tms", "escalation"], [guia], False, created, esc_decision,
        ))

    # Group G: Multi-turn conversations (5) — greeting → tracking → follow-up
    for i in range(5):
        sid = uid()
        channel = CHANNELS[i % 3]
        user = fake_users[(i + 25) % len(fake_users)]
        guia = GUIAS[i % len(GUIAS)]
        hours = random.uniform(1, 36)
        status = "AWAITING_USER"

        conversations.append(make_conversation(
            sid, channel, status, "TRACKING", "POSITIVO",
            user_hashes[user], guia=guia, hours_ago=hours,
        ))

        # Turn 1: greeting
        msg1_id = uid()
        t1 = ts(hours, 20)
        messages.append(make_inbound_message(msg1_id, sid, channel, "Hola, buenos días", t1))
        blocks_saludo = [
            {"type": "TEXT", "text": "¡Hola! Soy el asistente virtual de TransLogix. ¿En qué puedo ayudarte hoy?"},
            {"type": "QUICK_REPLIES", "options": [
                {"id": "rastrear", "title": "Rastrear envío"},
                {"id": "cotizar", "title": "Cotizar envío"},
                {"id": "asesor", "title": "Hablar con asesor"},
            ]},
        ]
        messages.append(make_outbound_message(
            sid, channel, msg1_id, blocks_saludo, "AWAITING_USER", "SALUDO",
            ts(hours, 19), services=["escalation"], evidence=[], llm_used=False,
        ))

        # Turn 2: tracking
        msg2_id = uid()
        t2 = ts(hours, 15)
        messages.append(make_inbound_message(msg2_id, sid, channel, f"Quiero rastrear {guia}", t2))
        blocks_track = make_tracking_blocks(guia)
        messages.append(make_outbound_message(
            sid, channel, msg2_id, blocks_track, "AWAITING_USER", "TRACKING",
            ts(hours, 14), services=["tms", "escalation"], evidence=[guia], llm_used=False,
        ))

        # Turn 3: follow-up question
        msg3_id = uid()
        t3 = ts(hours, 10)
        messages.append(make_inbound_message(msg3_id, sid, channel, "¿A qué hora llega?", t3))
        blocks_followup = [
            {"type": "TEXT", "text": f"Según la información más reciente, tu envío {guia} tiene una entrega estimada para hoy."},
            {"type": "QUICK_REPLIES", "options": [
                {"id": "cotizar", "title": "Cotizar un envío"},
                {"id": "asesor", "title": "Hablar con un asesor"},
            ]},
        ]
        messages.append(make_outbound_message(
            sid, channel, msg3_id, blocks_followup, "AWAITING_USER", "TRACKING",
            ts(hours, 9), services=["tms", "escalation"], evidence=[guia], llm_used=True,
        ))

        audit_events.append(make_audit_event(sid, channel, "SALUDO", ["escalation"], [], False, t1))
        audit_events.append(make_audit_event(sid, channel, "TRACKING", ["tms", "escalation"], [guia], False, t2))
        audit_events.append(make_audit_event(sid, channel, "TRACKING", ["tms", "escalation"], [guia], True, t3))

    # Group H: Saludos simples / idle / closed (5)
    for i in range(5):
        sid = uid()
        channel = CHANNELS[i % 3]
        user = fake_users[i % len(fake_users)]
        status = random.choice(["IDLE", "CLOSED"])
        hours = random.uniform(24, 168)

        conversations.append(make_conversation(
            sid, channel, status, "SALUDO", "NEUTRO",
            user_hashes[user], hours_ago=hours,
        ))
        msg_id = uid()
        text = random.choice(USER_MESSAGES["SALUDO"])
        created = ts(hours, 5)
        messages.append(make_inbound_message(msg_id, sid, channel, text, created))
        blocks = [
            {"type": "TEXT", "text": "¡Hola! Soy el asistente virtual de TransLogix. ¿En qué puedo ayudarte?"},
            {"type": "QUICK_REPLIES", "options": [
                {"id": "rastrear", "title": "Rastrear envío"},
                {"id": "cotizar", "title": "Cotizar envío"},
            ]},
        ]
        messages.append(make_outbound_message(
            sid, channel, msg_id, blocks, status, "SALUDO",
            ts(hours, 4), services=["escalation"], evidence=[], llm_used=False,
        ))
        audit_events.append(make_audit_event(sid, channel, "SALUDO", ["escalation"], [], False, created))

    # Group I: Carga especial (4)
    for i in range(4):
        sid = uid()
        channel = CHANNELS[i % 3]
        user = fake_users[(i + 7) % len(fake_users)]
        hours = random.uniform(2, 48)
        status = random.choice(["AWAITING_USER", "ESCALATED"])
        handoff_id = uid() if status == "ESCALATED" else None

        conversations.append(make_conversation(
            sid, channel, status, "CARGA_ESPECIAL",
            random.choice(["NEUTRO", "NEGATIVO"]),
            user_hashes[user], handoff_id=handoff_id, hours_ago=hours,
        ))

        msg_id = uid()
        text = random.choice(USER_MESSAGES["CARGA_ESPECIAL"])
        created = ts(hours, 10)
        messages.append(make_inbound_message(msg_id, sid, channel, text, created))

        if status == "ESCALATED":
            esc_decision = make_escalation_decision(
                "CRIT-001", "ESCALAR_A_HUMANO",
                "confidence_score=0.65 < 0.75",
                "SERVICIO_CLIENTE", "NORMAL",
                "Te conecto con un especialista en carga especial.",
            )
            handoffs.append((
                handoff_id, sid, channel,
                json.dumps(make_handoff_payload(
                    handoff_id, sid, channel, None, "SERVICIO_CLIENTE", "NORMAL",
                    "CRIT-001", "ESCALAR_A_HUMANO", "confidence_score=0.65",
                    "NEUTRO", created, text,
                )),
                "PENDING", created,
            ))
            blocks = [
                {"type": "HANDOFF_NOTICE", "handoff_id": handoff_id,
                 "prioridad": "NORMAL", "cola": "SERVICIO_CLIENTE",
                 "mensaje_cliente": "Te conecto con un especialista en carga especial.",
                 "tiempo_espera_estimado_segundos": 45},
            ]
        else:
            blocks = [
                {"type": "TEXT", "text": random.choice(BOT_RESPONSES["CARGA_ESPECIAL"])},
                {"type": "QUICK_REPLIES", "options": [
                    {"id": "cotizar", "title": "Cotizar envío"},
                    {"id": "asesor", "title": "Hablar con un asesor"},
                ]},
            ]

        messages.append(make_outbound_message(
            sid, channel, msg_id, blocks, status, "CARGA_ESPECIAL",
            ts(hours, 9), services=["rag", "escalation"],
            evidence=[f"HAZMAT:{random.randint(1,20):04d}"], llm_used=True,
        ))
        audit_events.append(make_audit_event(
            sid, channel, "CARGA_ESPECIAL", ["rag", "escalation"],
            [f"HAZMAT:{random.randint(1,20):04d}"], True, created,
        ))

    # ── 2. EXTRA STANDALONE QUOTES (to fill the table) ──────────────
    for i in range(15):
        cot_id = uid()
        ruta = RUTAS[i % len(RUTAS)]
        created = ts(random.uniform(1, 200), random.randint(0, 60))
        quotes_data.append(make_quote_record(cot_id, None, ruta, created))

    # ── 3. EXTRA ATTACHMENTS ────────────────────────────────────────
    doc_codes = ["FACTURA_COMERCIAL", "FOTOS_DANO", "FOTOS_EMPAQUE", "CARTA_RECLAMACION",
                 "CERTIFICACION_BANCARIA", "CEDULA_O_RUT"]
    mimes = ["application/pdf", "image/jpeg", "image/png"]
    for i in range(10):
        att_id = uid()
        sess = conversations[random.randint(0, len(conversations) - 1)][0] if random.random() < 0.7 else None
        attachments.append(make_attachment(
            att_id, sess,
            random.choice(["claim_doc", "photo", "pdf"]),
            random.choice(mimes),
            random.randint(5000, 5000000),
            random.choice(doc_codes),
            ts(random.uniform(1, 100)),
        ))

    # ── INSERT ───────────────────────────────────────────────────────
    cur = conn  # alias for readability

    print(f"  📦 {len(conversations)} conversations")
    print(f"  💬 {len(messages)} messages")
    print(f"  🔄 {len(handoffs)} handoffs")
    print(f"  📋 {len(pqrs_data)} PQRS tickets")
    print(f"  💰 {len(quotes_data)} quotes")
    print(f"  📎 {len(attachments)} attachments")
    print(f"  📊 {len(audit_events)} audit events")

    async with conn.transaction():
        for c in conversations:
            await cur.execute("""
                INSERT INTO conversations (session_id, channel, external_user_id_hash, status, state_json, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (session_id) DO NOTHING
            """, uuid.UUID(c[0]), c[1], c[2], c[3], c[4], datetime.fromisoformat(c[5]), datetime.fromisoformat(c[6]))

        for m in messages:
            await cur.execute("""
                INSERT INTO messages (id, session_id, channel, direction, message_id, in_reply_to, message_type, content_json, delivery_mode, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT DO NOTHING
            """, uuid.UUID(m[0]), uuid.UUID(m[1]), m[2], m[3], m[4], m[5], m[6], m[7], m[8], datetime.fromisoformat(m[9]))

        for h in handoffs:
            await cur.execute("""
                INSERT INTO handoffs (handoff_id, session_id, channel, payload_json, status, created_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (handoff_id) DO NOTHING
            """, uuid.UUID(h[0]), uuid.UUID(h[1]), h[2], h[3], h[4], datetime.fromisoformat(h[5]))

        for q in quotes_data:
            sess = uuid.UUID(q[1]) if q[1] else None
            await cur.execute("""
                INSERT INTO quotes (cotizacion_id, session_id, request_json, response_json, formula_version, created_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (cotizacion_id) DO NOTHING
            """, uuid.UUID(q[0]), sess, q[2], q[3], q[4], datetime.fromisoformat(q[5]))

        for p in pqrs_data:
            await cur.execute("""
                INSERT INTO pqrs_tickets (ticket_id, session_id, tipo, numero_guia, estado, payload_json, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (ticket_id) DO NOTHING
            """, uuid.UUID(p[0]), uuid.UUID(p[1]), p[2], p[3], p[4], p[5], datetime.fromisoformat(p[6]))

        for a in audit_events:
            sess = uuid.UUID(a[2]) if a[2] else None
            await cur.execute("""
                INSERT INTO audit_events (id, trace_id, session_id, channel, intent, services_called, escalation_json, evidence_ids, llm_used, latency_ms, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT (id) DO NOTHING
            """, uuid.UUID(a[0]), a[1], sess, a[3], a[4], a[5], a[6], a[7], a[8], a[9], datetime.fromisoformat(a[10]))

        for att in attachments:
            sess = uuid.UUID(att[1]) if att[1] else None
            await cur.execute("""
                INSERT INTO attachments (attachment_id, session_id, kind, mime_type, size_bytes, sha256, declared_doc_code, storage_ref, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (attachment_id) DO NOTHING
            """, att[0], sess, att[2], att[3], att[4], att[5], att[6], att[7], datetime.fromisoformat(att[8]))

    print("\n✅ Mock data inserted successfully!")

    # ── SUMMARY ──────────────────────────────────────────────────────
    rows = await conn.fetch("""
        SELECT 'conversations' as tbl, count(*) FROM conversations
        UNION ALL SELECT 'messages', count(*) FROM messages
        UNION ALL SELECT 'handoffs', count(*) FROM handoffs
        UNION ALL SELECT 'pqrs_tickets', count(*) FROM pqrs_tickets
        UNION ALL SELECT 'quotes', count(*) FROM quotes
        UNION ALL SELECT 'attachments', count(*) FROM attachments
        UNION ALL SELECT 'audit_events', count(*) FROM audit_events
        ORDER BY 1;
    """)
    print("\n📊 Totals after seeding:")
    for row in rows:
        print(f"   {row['tbl']:20s} → {row['count']}")

    await conn.close()


if __name__ == "__main__":
    main()

