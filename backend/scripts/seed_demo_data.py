"""Seed the database with rich, realistic demo data for TransLogix AI.

Populates:
- Staff users (Admin, Reviewers) with working password logins
- Enterprise clients & API keys
- Shipment batches & batch items
- Shipments (normal, cold-chain, dangerous goods: pending, approved, rejected)
- Recipients with proper PII encryption (EncryptedText)
- Shipment dangerous-goods reviews
- Conversations & Messages with encrypted payloads (EncryptedJson)
- Handoffs (PENDING queue, ASSIGNED, CLOSED) with valid HandoffPayload contracts
- PQRS tickets (Petición, Queja, Reclamo, Siniestro)
- Quotes
- API audit logs & conversational audit events

Run:
    cd backend && uv run python -m scripts.seed_demo_data
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.common import ChannelType
from app.contracts.conversation import Sentiment
from app.contracts.escalation import (
    ColaDestino,
    EscalationAction,
    EscalationTrigger,
    HandoffPayload,
    HandoffStatus,
    HistorialItem,
    Prioridad,
)
from app.contracts.pqrs import (
    ChecklistItem,
    DocumentoCodigo,
    EstadoTicket,
    PqrsTipo,
)
from app.db.session import create_engine, create_session_factory
from app.shared.config import get_settings
from app.shared.crypto import configure as configure_pii
from app.shared.crypto import encrypt
from app.shared.passwords import hash_password
from app.shared.tokens import hash_token, new_token

BOG = timezone(timedelta(hours=-5))
NOW = datetime(2026, 11, 4, 9, 30, 0, tzinfo=BOG)


def ts(days_ago: float = 0, hours_ago: float = 0, minutes_ago: float = 0) -> datetime:
    return NOW - timedelta(days=days_ago, hours=hours_ago, minutes=minutes_ago)


def enc_json(data: dict) -> dict:
    """Format matching app.shared.crypto.EncryptedJson: {'enc': encrypt(json_str)}."""
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return {"enc": encrypt(raw)}


# ── Core Stable IDs ────────────────────────────────────────────────────────
ADMIN_ID = UUID("bed33334-8f6b-4dc7-bc78-45963c92d557")  # admin.demo@translogix.co
REVIEWER_ID = UUID("8043876f-c144-406c-9a4e-db8f75d68682")  # revisor.demo@translogix.co
REVIEWER2_ID = UUID("ea90ec72-627f-47d3-9766-ceaea8376eff")  # maria.garcia@translogix.co
ADMIN2_ID = UUID("d4e5f6a7-b8c9-4d0e-1f2a-3b4c5d6e7f8a")  # carlos.admin@translogix.co

CLIENT_DEMO_ID = UUID("257c3113-2126-4f61-99d1-93684e8b012e")  # Cliente Demo B2B S.A.S.
CLIENT_NORTE_ID = UUID("a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d")  # Logística del Norte S.A.S.
CLIENT_ANDINA_ID = UUID(
    "b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e"
)  # Distribuidora Nacional Andina S.A.
CLIENT_BIOFARM_ID = UUID("c3d4e5f6-a7b8-4c9d-0e1f-2a3b4c5d6e7a")  # BioFarmacéutica de Colombia S.A.


async def seed(session: AsyncSession) -> None:
    print("🚀 Iniciando seed de datos de prueba...")

    # ── 1. Staff Users & Passwords ──────────────────────────────────────────
    print("1/10 Creando usuarios staff adicionales...")
    staff_data = [
        (ADMIN_ID, "admin.demo@translogix.co", "Admin Demo", ["ADMIN"], "Admin2026!"),
        (REVIEWER_ID, "revisor.demo@translogix.co", "Revisor Demo", ["REVIEWER"], "Revisor2026!"),
        (REVIEWER2_ID, "maria.garcia@translogix.co", "María García", ["REVIEWER"], "Maria2026!"),
        (
            ADMIN2_ID,
            "carlos.admin@translogix.co",
            "Carlos Rodríguez",
            ["ADMIN", "REVIEWER"],
            "Carlos2026!",
        ),
    ]
    for uid, email, nombre, roles, pwd in staff_data:
        pw_hash = hash_password(pwd)
        await session.execute(
            text("""
                INSERT INTO staff_users (id, email, nombre, roles, estado, password_hash, created_at, updated_at)
                VALUES (:id, :email, :nombre, :roles, 'ACTIVE', :pw, :t, :t)
                ON CONFLICT (id) DO UPDATE SET password_hash = :pw, nombre = :nombre, roles = :roles
            """),
            {
                "id": uid,
                "email": email,
                "nombre": nombre,
                "roles": json.dumps(roles),
                "pw": pw_hash,
                "t": ts(60),
            },
        )

    # ── 2. Enterprise Clients ───────────────────────────────────────────────
    print("2/10 Creando clientes empresariales...")
    clients_data = [
        (CLIENT_DEMO_ID, "900999001-1", "Cliente Demo B2B S.A.S.", "ESTANDAR", ts(90)),
        (CLIENT_NORTE_ID, "800123456-7", "Logística del Norte S.A.S.", "PREMIUM", ts(75)),
        (CLIENT_ANDINA_ID, "900567890-3", "Distribuidora Nacional Andina S.A.", "ESTANDAR", ts(50)),
        (CLIENT_BIOFARM_ID, "901234567-8", "BioFarmacéutica de Colombia S.A.", "PREMIUM", ts(30)),
    ]
    for cid, nit, razon, tipo, created in clients_data:
        await session.execute(
            text("""
                INSERT INTO enterprise_clients (id, nit, razon_social, estado, tipo_cuenta, created_at, updated_at)
                VALUES (:id, :nit, :razon, 'ACTIVE', :tipo, :t, :t)
                ON CONFLICT (id) DO UPDATE SET razon_social = :razon, tipo_cuenta = :tipo
            """),
            {"id": cid, "nit": nit, "razon": razon, "tipo": tipo, "t": created},
        )

    # API Keys for clients (if not present)
    for cid, label in [
        (CLIENT_NORTE_ID, "Logística Norte"),
        (CLIENT_ANDINA_ID, "Andina"),
        (CLIENT_BIOFARM_ID, "BioFarmacéutica"),
    ]:
        existing = await session.execute(
            text("SELECT id FROM client_api_keys WHERE client_id = :cid"), {"cid": cid}
        )
        if not existing.scalar():
            prefix, full = new_token("tlx")
            await session.execute(
                text("""
                    INSERT INTO client_api_keys (id, client_id, key_prefix, key_hash, scopes, created_at)
                    VALUES (:id, :cid, :prefix, :hash, '["batches:create","batches:read"]', :t)
                """),
                {
                    "id": uuid4(),
                    "cid": cid,
                    "prefix": prefix,
                    "hash": hash_token(full),
                    "t": ts(30),
                },
            )
            print(f"   API Key para {label}: {full}")

    # ── 3. Shipment Batches ─────────────────────────────────────────────────
    print("3/10 Creando lotes de envíos...")
    batch_ids = {
        "NORTE_1": uuid4(),
        "NORTE_2": uuid4(),
        "ANDINA_1": uuid4(),
        "BIOFARM_1": uuid4(),
    }
    batches = [
        (
            batch_ids["NORTE_1"],
            CLIENT_NORTE_ID,
            "NORTE-2026-001",
            "COMPLETED",
            5,
            5,
            0,
            ts(12),
            ts(12),
            ts(11.9),
        ),
        (
            batch_ids["NORTE_2"],
            CLIENT_NORTE_ID,
            "NORTE-2026-002",
            "COMPLETED",
            4,
            3,
            1,
            ts(5),
            ts(5),
            ts(4.9),
        ),
        (
            batch_ids["ANDINA_1"],
            CLIENT_ANDINA_ID,
            "ANDINA-NOV-001",
            "COMPLETED",
            6,
            6,
            0,
            ts(3),
            ts(3),
            ts(2.8),
        ),
        (
            batch_ids["BIOFARM_1"],
            CLIENT_BIOFARM_ID,
            "BIOFARM-MED-01",
            "PROCESSING",
            4,
            2,
            0,
            ts(0.5),
            ts(0.4),
            None,
        ),
    ]
    for bid, cid, ref, status, total, acc, rej, c_at, s_at, f_at in batches:
        await session.execute(
            text("""
                INSERT INTO shipment_batches (id, client_id, referencia_lote, request_hash, status,
                    total_items, accepted_items, rejected_items, created_at, started_at, completed_at)
                VALUES (:id, :cid, :ref, :h, :st, :tot, :acc, :rej, :cat, :sat, :fat)
                ON CONFLICT (id) DO NOTHING
            """),
            {
                "id": bid,
                "cid": cid,
                "ref": ref,
                "h": f"hash_{ref}",
                "st": status,
                "tot": total,
                "acc": acc,
                "rej": rej,
                "cat": c_at,
                "sat": s_at,
                "fat": f_at,
            },
        )

    # ── 4. Shipments, Batch Items, Recipients & Reviews ─────────────────────
    print("4/10 Creando envíos, destinatarios y revisiones...")
    shipment_specs = [
        # Norte - Lote 1
        (
            "NORTE_1",
            CLIENT_NORTE_ID,
            "NORTE-001",
            "11001",
            "05001",
            "TLX-BOG-MED-001",
            (25, 30, 40),
            (4.5, 6.0, 6.0),
            750000,
            False,
            False,
            36500,
            "ENTREGADO",
            "NO_REQUERIDA",
            12,
        ),
        (
            "NORTE_1",
            CLIENT_NORTE_ID,
            "NORTE-002",
            "11001",
            "76001",
            "TLX-BOG-CAL-001",
            (30, 35, 50),
            (8.0, 10.5, 10.5),
            1200000,
            False,
            False,
            52000,
            "ENTREGADO",
            "NO_REQUERIDA",
            12,
        ),
        (
            "NORTE_1",
            CLIENT_NORTE_ID,
            "NORTE-003",
            "05001",
            "08001",
            "TLX-MED-BAQ-001",
            (40, 40, 60),
            (12.0, 19.2, 19.2),
            3500000,
            False,
            False,
            84000,
            "ENTREGADO",
            "NO_REQUERIDA",
            12,
        ),
        (
            "NORTE_1",
            CLIENT_NORTE_ID,
            "NORTE-004",
            "11001",
            "68001",
            "TLX-BOG-BUC-001",
            (20, 25, 30),
            (3.0, 3.0, 3.0),
            450000,
            False,
            False,
            28000,
            "ENTREGADO",
            "NO_REQUERIDA",
            12,
        ),
        (
            "NORTE_1",
            CLIENT_NORTE_ID,
            "NORTE-005",
            "76001",
            "11001",
            "TLX-CAL-BOG-001",
            (35, 45, 55),
            (9.0, 17.3, 17.3),
            2100000,
            False,
            False,
            72000,
            "ENTREGADO",
            "NO_REQUERIDA",
            12,
        ),
        # Norte - Lote 2
        (
            "NORTE_2",
            CLIENT_NORTE_ID,
            "NORTE-006",
            "11001",
            "05001",
            "TLX-BOG-MED-001",
            (50, 50, 60),
            (15.0, 30.0, 30.0),
            5000000,
            True,
            False,
            115000,
            "EN_TRANSITO",
            "NO_REQUERIDA",
            5,
        ),
        (
            "NORTE_2",
            CLIENT_NORTE_ID,
            "NORTE-007",
            "08001",
            "13001",
            "TLX-BAQ-CTG-001",
            (20, 20, 30),
            (2.5, 2.4, 2.5),
            320000,
            False,
            False,
            22000,
            "EN_TRANSITO",
            "NO_REQUERIDA",
            5,
        ),
        (
            "NORTE_2",
            CLIENT_NORTE_ID,
            "NORTE-008-ACIDO",
            "05001",
            "76001",
            "TLX-MED-CAL-001",
            (30, 30, 40),
            (6.0, 7.2, 7.2),
            1800000,
            False,
            True,
            68000,
            "EN_TRANSITO",
            "APROBADA",
            5,
        ),
        # Andina - Lote 1
        (
            "ANDINA_1",
            CLIENT_ANDINA_ID,
            "AND-101",
            "11001",
            "05001",
            "TLX-BOG-MED-001",
            (20, 30, 40),
            (5.0, 4.8, 5.0),
            600000,
            False,
            False,
            32000,
            "EN_TRANSITO",
            "NO_REQUERIDA",
            3,
        ),
        (
            "ANDINA_1",
            CLIENT_ANDINA_ID,
            "AND-102",
            "11001",
            "76001",
            "TLX-BOG-CAL-001",
            (40, 50, 50),
            (10.0, 20.0, 20.0),
            2800000,
            False,
            False,
            78000,
            "EN_TRANSITO",
            "NO_REQUERIDA",
            3,
        ),
        (
            "ANDINA_1",
            CLIENT_ANDINA_ID,
            "AND-103",
            "05001",
            "66001",
            "TLX-MED-PER-001",
            (15, 20, 25),
            (1.8, 1.5, 1.8),
            290000,
            False,
            False,
            24000,
            "REGISTRADO",
            "NO_REQUERIDA",
            3,
        ),
        (
            "ANDINA_1",
            CLIENT_ANDINA_ID,
            "AND-104-SOLVENTE",
            "11001",
            "17001",
            "TLX-BOG-MAN-001",
            (25, 30, 35),
            (5.5, 5.3, 5.5),
            1400000,
            False,
            True,
            54000,
            "REGISTRADO",
            "PENDIENTE",
            3,
        ),
        (
            "ANDINA_1",
            CLIENT_ANDINA_ID,
            "AND-105",
            "76001",
            "08001",
            "TLX-CAL-BAQ-001",
            (30, 40, 50),
            (8.5, 12.0, 12.0),
            1950000,
            False,
            False,
            65000,
            "REGISTRADO",
            "NO_REQUERIDA",
            3,
        ),
        (
            "ANDINA_1",
            CLIENT_ANDINA_ID,
            "AND-106-REACTIVO",
            "11001",
            "68001",
            "TLX-BOG-BUC-001",
            (25, 25, 30),
            (3.5, 3.8, 3.8),
            980000,
            False,
            True,
            44000,
            "REGISTRADO",
            "RECHAZADA",
            3,
        ),
        # BioFarmacéutica - Lote 1
        (
            "BIOFARM_1",
            CLIENT_BIOFARM_ID,
            "BIO-001-INSULINA",
            "11001",
            "05001",
            "TLX-BOG-MED-001",
            (30, 40, 40),
            (7.0, 9.6, 9.6),
            8500000,
            True,
            False,
            145000,
            "REGISTRADO",
            "NO_REQUERIDA",
            0.5,
        ),
        (
            "BIOFARM_1",
            CLIENT_BIOFARM_ID,
            "BIO-002-BIOLOGICO",
            "11001",
            "76001",
            "TLX-BOG-CAL-001",
            (35, 45, 50),
            (8.5, 15.8, 15.8),
            12000000,
            True,
            True,
            210000,
            "REGISTRADO",
            "PENDIENTE",
            0.5,
        ),
    ]

    recipients_info = [
        (
            "Distribuciones Médicas S.A.",
            "Calle 10 # 42-28, Medellín",
            "3104567890",
            "logistica@dismedicas.com",
        ),
        (
            "Comercializadora del Valle Ltda.",
            "Av. 4N # 23-15, Cali",
            "3158901234",
            "compras@comvalle.co",
        ),
        (
            "Almacenes del Caribe",
            "Cra. 53 # 74-89, Barranquilla",
            "3001234567",
            "recepcion@almacaribe.com",
        ),
        (
            "Suministros del Oriente",
            "Calle 35 # 18-40, Bucaramanga",
            "3176543210",
            "bodega@sumioriente.com",
        ),
        (
            "Operaciones Andinas S.A.S.",
            "Cra. 7 # 116-50, Bogotá",
            "3112345678",
            "andinas@operaciones.com",
        ),
        (
            "Cadena Fría Antioquia",
            "Autopista Norte km 15, Bello",
            "3049876543",
            "frio@cfantioquia.com",
        ),
        (
            "Logística Bolívar",
            "Zona Industrial Mamonal, Cartagena",
            "3187654321",
            "bolivar@logcartagena.co",
        ),
        (
            "Químicos del Pacífico",
            "Cra. 1 # 44-10, Cali",
            "3123456789",
            "seguridad@quimpacifico.com",
        ),
        (
            "Ferretería El Cóndor",
            "Cra. 50 # 52-20, Medellín",
            "3167890123",
            "pedidos@elcondor.com.co",
        ),
        ("Servicios del Sur", "Calle 15 # 32-05, Cali", "3145678901", "sur@serviciosdelsur.co"),
        (
            "Comercial Eje Cafetero",
            "Av. 30 de Agosto # 45-12, Pereira",
            "3134567890",
            "eje@comercialeje.com",
        ),
        (
            "Laboratorios Caldas",
            "Cra. 23 # 62-10, Manizales",
            "3201234567",
            "caldas@laboratorios.com",
        ),
        (
            "Textiles del Norte",
            "Calle 40 # 44-30, Barranquilla",
            "3219876543",
            "textiles@norte.com.co",
        ),
        (
            "BioQuímica Santander",
            "Cra. 27 # 36-14, Bucaramanga",
            "3223456789",
            "lab@bioquimicasantander.com",
        ),
        (
            "Clínica CardioVID",
            "Calle 78B # 75-21, Medellín",
            "3051234567",
            "farmacia@cardiovid.org.co",
        ),
        (
            "Hospital Universitario del Valle",
            "Calle 5 # 36-08, Cali",
            "3198765432",
            "insumos@huv.gov.co",
        ),
    ]

    guide_counter = 200010
    created_shipments = []

    for i, spec in enumerate(shipment_specs):
        (
            b_key,
            cid,
            ref,
            orig,
            dest,
            tray,
            dims,
            weights,
            val,
            frio,
            pelig,
            flete,
            estado,
            rev_status,
            d_ago,
        ) = spec
        bid = batch_ids[b_key]
        item_id = uuid4()
        ship_id = uuid4()
        guia = f"TLX-{guide_counter:06d}-CO"
        guide_counter += 1
        created_time = ts(d_ago)

        # 1. Insert Batch Item (initially without shipment_id)
        await session.execute(
            text("""
                INSERT INTO shipment_batch_items (id, batch_id, line_number, referencia_cliente, status, shipment_id, errors_json, created_at, processed_at)
                VALUES (:id, :bid, :line, :ref, 'CREATED', NULL, '[]', :t, :t)
                ON CONFLICT (id) DO NOTHING
            """),
            {"id": item_id, "bid": bid, "line": i + 1, "ref": ref, "t": created_time},
        )

        # 2. Insert Shipment
        await session.execute(
            text("""
                INSERT INTO shipments (
                    id, numero_guia, client_id, referencia_cliente, batch_item_id,
                    origen_codigo_dane, destino_codigo_dane, codigo_trayecto,
                    alto_cm, ancho_cm, largo_cm, peso_real_kg, peso_volumetrico_kg, peso_facturable_kg,
                    valor_declarado_cop, requiere_cadena_frio, mercancia_peligrosa,
                    flete_total_cop, formula_version, tarifa_version, cotizado_en,
                    estado, review_status, created_at, updated_at
                ) VALUES (
                    :id, :guia, :cid, :ref, :item_id,
                    :orig, :dest, :tray,
                    :alto, :ancho, :largo, :peso, :pvol, :pfact,
                    :valor, :frio, :pelig,
                    :flete, 'excedente-v1', 'matriz-2026-v2', :t,
                    :estado, :review_status, :t, :t
                )
                ON CONFLICT (id) DO NOTHING
            """),
            {
                "id": ship_id,
                "guia": guia,
                "cid": cid,
                "ref": ref,
                "item_id": item_id,
                "orig": orig,
                "dest": dest,
                "tray": tray,
                "alto": Decimal(dims[0]),
                "ancho": Decimal(dims[1]),
                "largo": Decimal(dims[2]),
                "peso": Decimal(weights[0]),
                "pvol": Decimal(weights[1]),
                "pfact": Decimal(weights[2]),
                "valor": val,
                "frio": frio,
                "pelig": pelig,
                "flete": flete,
                "estado": estado,
                "review_status": rev_status,
                "t": created_time,
            },
        )

        # 3. Update Batch Item link
        await session.execute(
            text("UPDATE shipment_batch_items SET shipment_id = :sid WHERE id = :iid"),
            {"sid": ship_id, "iid": item_id},
        )

        # 4. Insert Recipient with Encrypted PII
        rec_nom, rec_dir, rec_tel, rec_em = recipients_info[i % len(recipients_info)]
        await session.execute(
            text("""
                INSERT INTO shipment_recipients (
                    id, shipment_id, nombre, tipo_documento, numero_documento,
                    telefono, email, direccion, created_at, updated_at
                ) VALUES (
                    :id, :sid, :nom, 'CC', :doc, :tel, :em, :dir, :t, :t
                )
                ON CONFLICT (shipment_id) DO NOTHING
            """),
            {
                "id": uuid4(),
                "sid": ship_id,
                "nom": encrypt(rec_nom),
                "doc": encrypt(f"{1000000000 + i * 87654}"),
                "tel": encrypt(rec_tel),
                "em": encrypt(rec_em),
                "dir": encrypt(rec_dir),
                "t": created_time,
            },
        )

        # 5. Insert Review if approved/rejected
        if rev_status == "APROBADA":
            await session.execute(
                text("""
                    INSERT INTO shipment_reviews (id, shipment_id, decision, observaciones, reviewer_staff_user_id, created_at)
                    VALUES (:id, :sid, 'APROBADA', 'MSDS vigente, embalaje homologado Clase 8 verificado.', :rev, :t)
                    ON CONFLICT (shipment_id) DO NOTHING
                """),
                {
                    "id": uuid4(),
                    "sid": ship_id,
                    "rev": REVIEWER_ID,
                    "t": created_time + timedelta(hours=2),
                },
            )
        elif rev_status == "RECHAZADA":
            await session.execute(
                text("""
                    INSERT INTO shipment_reviews (id, shipment_id, decision, observaciones, reviewer_staff_user_id, created_at)
                    VALUES (:id, :sid, 'RECHAZADA', 'Falta ficha técnica ONU y certificado de embalaje Clase 3.', :rev, :t)
                    ON CONFLICT (shipment_id) DO NOTHING
                """),
                {
                    "id": uuid4(),
                    "sid": ship_id,
                    "rev": REVIEWER2_ID,
                    "t": created_time + timedelta(hours=3),
                },
            )

        created_shipments.append((ship_id, guia, ref, estado, rev_status))

    # Add 1 rejected batch item to Norte 2 (out of coverage)
    await session.execute(
        text("""
            INSERT INTO shipment_batch_items (id, batch_id, line_number, referencia_cliente, status, shipment_id, errors_json, created_at, processed_at)
            VALUES (:id, :bid, 4, 'NORTE-ERR-001', 'REJECTED', NULL, :errs, :t, :t)
            ON CONFLICT (id) DO NOTHING
        """),
        {
            "id": uuid4(),
            "bid": batch_ids["NORTE_2"],
            "errs": json.dumps(
                [
                    {
                        "code": "CIUDAD_NO_CUBIERTA",
                        "message": "No tenemos cobertura operativa en esa ciudad.",
                    }
                ]
            ),
            "t": ts(5),
        },
    )

    # ── 5. Conversations & Messages ─────────────────────────────────────────
    print("5/10 Creando conversaciones y mensajes cifrados...")
    conversations_data = [
        (uuid4(), ChannelType.WEB_CHAT, "ESCALATED", "hash_usr_vip_01", "tracking", 1.5),
        (uuid4(), ChannelType.WEB_CHAT, "ESCALATED", "hash_usr_vip_02", "pqrs", 2.0),
        (uuid4(), ChannelType.WEB_CHAT, "ACTIVE", "hash_usr_vip_03", "cotizacion", 0.8),
        (uuid4(), ChannelType.WHATSAPP, "ESCALATED", "hash_wa_usr_01", "tracking", 0.5),
        (uuid4(), ChannelType.WHATSAPP, "ACTIVE", "hash_wa_usr_02", "tracking", 0.2),
        (uuid4(), ChannelType.PBX, "ESCALATED", "hash_pbx_usr_01", "escalacion", 1.0),
        (uuid4(), ChannelType.WEB_CHAT, "CLOSED", "hash_usr_vip_04", "pqrs", 6.0),
        (uuid4(), ChannelType.WHATSAPP, "CLOSED", "hash_wa_usr_03", "cotizacion", 4.0),
    ]

    dialogues = [
        [
            (
                "IN",
                "TEXT",
                "Hola, mi paquete TLX-200016-CO lleva 3 días sin actualizarse y es urgente.",
            ),
            (
                "OUT",
                None,
                "Hola. Consulté tu guía TLX-200016-CO. Se encuentra en la bodega intermedia de Cali. Dado que indicas urgencia, voy a comunicarte con un asesor de operaciones.",
            ),
        ],
        [
            (
                "IN",
                "TEXT",
                "Buenas tardes, quiero interponer un reclamo por pérdida de mercancía valorada en $3.500.000.",
            ),
            (
                "OUT",
                None,
                "Lamento mucho la situación. He registrado el caso y por el monto de la reclamación te transferiré de inmediato con el departamento jurídico y de PQRS.",
            ),
        ],
        [
            (
                "IN",
                "TEXT",
                "Buenas, ¿cuánto cuesta enviar 15 kg de Bogotá a Medellín con entrega al día siguiente?",
            ),
            (
                "OUT",
                None,
                "Para 15 kg de Bogotá a Medellín el flete estimado es de $52.000 COP con tiempo de entrega estimado de 24 horas.",
            ),
        ],
        [
            (
                "IN",
                "TEXT",
                "Urgente, necesito ayuda con un envío refrigerado de medicamentos que está retrasado.",
            ),
            (
                "OUT",
                None,
                "Entiendo la prioridad crítica. Los envíos con cadena de frío tienen atención prioritaria. Un asesor de turno atenderá tu caso en este momento.",
            ),
        ],
        [
            ("IN", "TEXT", "Estado de la guía TLX-200010-CO por favor."),
            (
                "OUT",
                None,
                "Tu guía TLX-200010-CO fue ENTREGADA el 31 de Octubre a las 14:20 en destino a satisfacción.",
            ),
        ],
        [
            (
                "IN",
                "TEXT",
                "Llevo 20 minutos esperando que me contesten, exijo hablar con un supervisor.",
            ),
            (
                "OUT",
                None,
                "Comprendo tu molestia por el tiempo de espera. Te estoy transfiriendo de inmediato a la cola de supervisión.",
            ),
        ],
        [
            ("IN", "TEXT", "Quisiera consultar si ya cerraron el caso de avería de mi envío."),
            (
                "OUT",
                None,
                "Tu caso PQRS-2026-088 fue resuelto satisfactoriamente y la indemnización fue aprobada. ¡Gracias por comunicarte con TransLogix!",
            ),
        ],
        [
            ("IN", "TEXT", "Muchas gracias por la cotización, ya realicé la compra del servicio."),
            (
                "OUT",
                None,
                "Con gusto. Tu servicio ha sido programado. Estaremos atentos a cualquier consulta.",
            ),
        ],
    ]

    conv_refs = []
    for (cid, ch, st, uhash, intent, d_ago), turns in zip(
        conversations_data, dialogues, strict=False
    ):
        c_time = ts(d_ago)
        await session.execute(
            text("""
                INSERT INTO conversations (session_id, channel, external_user_id_hash, status, state_json, created_at, updated_at)
                VALUES (:sid, :ch, :uh, :st, :sj, :t, :t2)
                ON CONFLICT (session_id) DO NOTHING
            """),
            {
                "sid": cid,
                "ch": ch.value if hasattr(ch, "value") else str(ch),
                "uh": uhash,
                "st": st,
                "sj": json.dumps({"intent": intent, "turn_count": len(turns)}),
                "t": c_time,
                "t2": c_time + timedelta(minutes=15),
            },
        )

        in_msg_id = None
        for j, (direction, mtype, txt) in enumerate(turns):
            mid = uuid4()
            m_time = c_time + timedelta(minutes=j * 2)
            if direction == "IN":
                content = {"content": {"text": txt}}
                in_msg_id = str(mid)
            else:
                content = {
                    "reply": {
                        "session_id": str(cid),
                        "reply_id": str(mid),
                        "blocks": [{"type": "text", "text": txt}],
                    }
                }

            await session.execute(
                text("""
                    INSERT INTO messages (id, session_id, channel, direction, message_id, in_reply_to,
                        message_type, content_json, delivery_mode, created_at)
                    VALUES (:id, :sid, :ch, :dir, :mid, :in_rep, :mtype, :c_json, 'SIMULATED', :t)
                    ON CONFLICT (id) DO NOTHING
                """),
                {
                    "id": mid,
                    "sid": cid,
                    "ch": ch.value if hasattr(ch, "value") else str(ch),
                    "dir": direction,
                    "mid": str(mid),
                    "in_rep": in_msg_id if direction == "OUT" else None,
                    "mtype": mtype,
                    "c_json": json.dumps(enc_json(content)),
                    "t": m_time,
                },
            )

        conv_refs.append((cid, ch, st, c_time, turns[0][2]))

    # ── 6. Handoffs (Bandeja de Asesor) ─────────────────────────────────────
    print("6/10 Creando casos escalados (Handoffs)...")
    handoff_configs = [
        (
            0,
            HandoffStatus.PENDING,
            None,
            ColaDestino.OPERACIONES,
            Prioridad.URGENTE,
            "CRIT-005: Urgencia operativa reportada por cliente",
            "Cliente reporta paquete TLX-200016-CO sin movimiento hace 3 días y requiere entrega inmediata.",
            "TLX-200016-CO",
        ),
        (
            1,
            HandoffStatus.PENDING,
            None,
            ColaDestino.JURIDICO_PQRS,
            Prioridad.ALTA,
            "CRIT-003: Reclamación económica superior a 3 SMMLV",
            "Reclamación por presunta pérdida de mercancía valorada en $3.500.000 COP.",
            "TLX-200012-CO",
        ),
        (
            3,
            HandoffStatus.ASSIGNED,
            REVIEWER_ID,
            ColaDestino.OPERACIONES,
            Prioridad.URGENTE,
            "CRIT-005: Envío con cadena de frío en riesgo",
            "Medicamentos con refrigeración reportan demora en tránsito hacia Cali.",
            "TLX-200024-CO",
        ),
        (
            5,
            HandoffStatus.ASSIGNED,
            REVIEWER2_ID,
            ColaDestino.SUPERVISOR,
            Prioridad.ALTA,
            "CRIT-002: Solicitud explícita de supervisor / escalamiento",
            "Cliente insatisfecho con tiempos de respuesta solicita intervención de un supervisor.",
            None,
        ),
        (
            6,
            HandoffStatus.CLOSED,
            REVIEWER_ID,
            ColaDestino.SERVICIO_CLIENTE,
            Prioridad.NORMAL,
            "CRIT-001: Consulta de resolución PQRS",
            "Caso atendido y cerrado. Se confirmó desembolso de indemnización.",
            "TLX-200014-CO",
        ),
    ]

    for c_idx, h_status, assignee, cola, prior, motivo, resumen, guia in handoff_configs:
        cid, ch, _, c_time, user_msg = conv_refs[c_idx]
        hid = uuid4()

        crit_id = (
            "CRIT-005"
            if cola == ColaDestino.OPERACIONES
            else "CRIT-003"
            if cola == ColaDestino.JURIDICO_PQRS
            else "CRIT-002"
        )

        action = (
            EscalationAction.ESCALAR_URGENCIA_OPERACIONES
            if prior == Prioridad.URGENTE
            else EscalationAction.ESCALAR_PRIORIDAD_ALTA
        )

        payload = HandoffPayload(
            handoff_id=hid,
            session_id=cid,
            canal=ch,
            numero_guia_consultada=guia,
            confidence_score_promedio=0.88,
            sentimiento_detectado=Sentiment.NEGATIVO
            if prior in (Prioridad.ALTA, Prioridad.URGENTE)
            else Sentiment.NEUTRO,
            intentos_previos=1,
            resumen_contexto=resumen,
            historial=[
                HistorialItem(
                    rol="USUARIO",
                    canal=ch,
                    texto=user_msg,
                    timestamp=c_time,
                )
            ],
            prioridad=prior,
            cola_destino=cola,
            triggers=[
                EscalationTrigger(
                    id_criterio=crit_id,
                    modo="claim_context",
                    accion=action,
                    accion_raw=action.value,
                    evidencia=motivo,
                )
            ],
            tiempo_espera_estimado_segundos=45,
            status=h_status,
            created_at=c_time,
        )

        await session.execute(
            text("""
                INSERT INTO handoffs (handoff_id, session_id, channel, payload_json, status, created_at, assigned_staff_user_id)
                VALUES (:hid, :sid, :ch, :pj, :st, :t, :assigned)
                ON CONFLICT (handoff_id) DO NOTHING
            """),
            {
                "hid": hid,
                "sid": cid,
                "ch": ch.value if hasattr(ch, "value") else str(ch),
                "pj": json.dumps(payload.model_dump(mode="json")),
                "st": h_status.value,
                "t": c_time,
                "assigned": assignee,
            },
        )

    # ── 7. PQRS Tickets ─────────────────────────────────────────────────────
    print("7/10 Creando tickets PQRS...")
    pqrs_items = [
        (
            PqrsTipo.RECLAMO,
            "TLX-200012-CO",
            EstadoTicket.ESCALADO,
            2,
            "Avería no aparente al abrir el paquete en bodega destino",
            ChecklistItem(
                codigo=DocumentoCodigo.CARTA_RECLAMACION, obligatorio=True, recibido=True
            ),
        ),
        (
            PqrsTipo.QUEJA,
            "TLX-200015-CO",
            EstadoTicket.RADICADO,
            1,
            "Conductor no entregó en el piso acordado y fue descortés",
            ChecklistItem(codigo=DocumentoCodigo.GUIA, obligatorio=True, recibido=True),
        ),
        (
            PqrsTipo.SINIESTRO,
            "TLX-200017-CO",
            EstadoTicket.ESCALADO,
            4,
            "Pérdida total por volcamiento de vehículo en la ruta Bogotá - Medellín",
            ChecklistItem(
                codigo=DocumentoCodigo.FACTURA_COMERCIAL, obligatorio=True, recibido=True
            ),
        ),
        (
            PqrsTipo.PETICION,
            None,
            EstadoTicket.RADICADO,
            0.5,
            "Solicitud de paz y salvo y certificado de retención en la fuente",
            ChecklistItem(codigo=DocumentoCodigo.CEDULA_O_RUT, obligatorio=True, recibido=True),
        ),
    ]

    for tipo, guia, estado, d_ago, desc, check_item in pqrs_items:
        tid = uuid4()
        req_payload = {
            "tipo": tipo.value,
            "numero_guia": guia,
            "descripcion": desc,
            "email_contacto": "reclamos@empresasdemo.com",
            "telefono_contacto": "3159998877",
            "nombre_contacto": "Representante Legal Demo",
        }
        resp_payload = {
            "ticket_id": str(tid),
            "estado": estado.value,
            "checklist": [
                {
                    "codigo": check_item.codigo.value,
                    "obligatorio": check_item.obligatorio,
                    "recibido": check_item.recibido,
                }
            ],
            "faltantes": [],
            "elegibilidad": {
                "dentro_de_plazo": True,
                "dias_habiles_transcurridos": 2,
                "plazo_maximo_dias": 5,
            },
            "liquidacion_preliminar": None,
            "escalamiento": {
                "escalate": (estado == EstadoTicket.ESCALADO),
                "prioridad": "ALTA" if estado == EstadoTicket.ESCALADO else "NORMAL",
            },
        }

        await session.execute(
            text("""
                INSERT INTO pqrs_tickets (ticket_id, session_id, tipo, numero_guia, estado, payload_json, created_at)
                VALUES (:tid, NULL, :tipo, :guia, :st, :pj, :t)
                ON CONFLICT (ticket_id) DO NOTHING
            """),
            {
                "tid": tid,
                "tipo": tipo.value,
                "guia": guia,
                "st": estado.value,
                "pj": json.dumps({"request": req_payload, "response": resp_payload}),
                "t": ts(d_ago),
            },
        )

    # ── 8. Quotes ───────────────────────────────────────────────────────────
    print("8/10 Creando cotizaciones históricas...")
    quotes_data = [
        ("Bogotá D.C.", "Medellín", "TLX-BOG-MED-001", 12.0, 1500000, 48000, 2),
        ("Bogotá D.C.", "Cali", "TLX-BOG-CAL-001", 25.0, 3200000, 85000, 3),
        ("Medellín", "Barranquilla", "TLX-MED-BAQ-001", 8.0, 950000, 54000, 5),
        ("Cali", "Bogotá D.C.", "TLX-CAL-BOG-001", 5.0, 600000, 32000, 1),
        ("Bogotá D.C.", "Bucaramanga", "TLX-BOG-BUC-001", 18.0, 2100000, 68000, 4),
        ("Barranquilla", "Cartagena", "TLX-BAQ-CTG-001", 3.0, 300000, 21000, 6),
    ]
    for orig, dest, tray, peso, val, total, d_ago in quotes_data:
        qid = uuid4()
        req_json = {
            "origen": orig,
            "destino": dest,
            "peso_real_kg": peso,
            "alto_cm": 30.0,
            "ancho_cm": 40.0,
            "largo_cm": 50.0,
            "valor_declarado_cop": val,
            "requiere_cadena_frio": False,
            "mercancia_peligrosa": False,
        }
        resp_json = {
            "cotizacion_id": str(qid),
            "ruta": {"codigo_trayecto": tray, "origen": orig, "destino": dest},
            "calculo": {
                "flete_base_cop": int(total * 0.72),
                "sobretasa_combustible_cop": int(total * 0.08),
                "seguro_cop": int(total * 0.05),
                "iva_cop": int(total * 0.15),
                "total_cop": total,
                "peso_liquidado_kg": peso,
                "tarifa_aplicada": {"tipo": "REAL", "cop_por_kg": int(total / peso)},
            },
            "tiempo_entrega_horas": 24,
            "advertencias": [],
            "supuestos": ["formula_version=provisional-v1"],
            "formula_version": "provisional-v1",
        }
        await session.execute(
            text("""
                INSERT INTO quotes (cotizacion_id, session_id, request_json, response_json, formula_version, created_at)
                VALUES (:qid, NULL, :req, :resp, 'provisional-v1', :t)
                ON CONFLICT (cotizacion_id) DO NOTHING
            """),
            {
                "qid": qid,
                "req": json.dumps(req_json),
                "resp": json.dumps(resp_json),
                "t": ts(d_ago),
            },
        )

    # ── 9. API Audit Events ─────────────────────────────────────────────────
    print("9/10 Creando registros de auditoría administrativa...")
    audit_events_data = [
        ("auth.login", 200, None, ADMIN_ID, "auth", "admin.demo@translogix.co", 0.1),
        ("auth.login", 200, None, REVIEWER_ID, "auth", "revisor.demo@translogix.co", 0.2),
        ("auth.login", 200, None, REVIEWER2_ID, "auth", "maria.garcia@translogix.co", 0.3),
        ("auth.failed", 401, None, None, "auth", "unknown_user@external.com", 0.4),
        ("dangerous_goods.read", 200, None, REVIEWER_ID, "shipment", None, 0.5),
        (
            "dangerous_goods.review",
            201,
            None,
            REVIEWER_ID,
            "shipment",
            str(created_shipments[7][0]),
            0.6,
        ),
        (
            "dangerous_goods.review",
            201,
            None,
            REVIEWER2_ID,
            "shipment",
            str(created_shipments[13][0]),
            0.7,
        ),
        ("handoff.read", 200, None, REVIEWER_ID, "handoff", None, 0.8),
        ("handoff.update", 200, None, REVIEWER_ID, "handoff", None, 0.9),
        ("batch.create", 201, CLIENT_NORTE_ID, None, "batch", str(batch_ids["NORTE_1"]), 12.0),
        ("batch.create", 201, CLIENT_ANDINA_ID, None, "batch", str(batch_ids["ANDINA_1"]), 3.0),
        ("batch.create", 201, CLIENT_BIOFARM_ID, None, "batch", str(batch_ids["BIOFARM_1"]), 0.5),
        ("batch.read", 200, CLIENT_NORTE_ID, None, "batch", str(batch_ids["NORTE_1"]), 1.0),
        ("staff.create", 201, None, ADMIN_ID, "staff_user", str(REVIEWER2_ID), 30.0),
        ("staff.roles.update", 200, None, ADMIN_ID, "staff_user", str(ADMIN2_ID), 25.0),
        ("rag.reindex", 200, None, ADMIN_ID, "rag", None, 2.0),
        ("access.denied", 403, CLIENT_NORTE_ID, None, "route", "/api/v1/internal/staff-users", 1.5),
    ]
    for action, status, cid, suid, rtype, rid, d_ago in audit_events_data:
        await session.execute(
            text("""
                INSERT INTO api_audit_events (id, client_id, api_key_id, action, resource_type, resource_id, request_id, http_status, created_at, staff_user_id)
                VALUES (:id, :cid, NULL, :action, :rtype, :rid, :req, :status, :t, :suid)
            """),
            {
                "id": uuid4(),
                "cid": cid,
                "action": action,
                "rtype": rtype,
                "rid": rid,
                "req": str(uuid4()),
                "status": status,
                "t": ts(d_ago),
                "suid": suid,
            },
        )

    # ── 10. Conversational Audit Events ─────────────────────────────────────
    print("10/10 Creando métricas conversacionales...")
    conv_intents = ["tracking", "cotizacion", "pqrs", "saludo", "escalacion", "consulta_general"]
    for i in range(25):
        await session.execute(
            text("""
                INSERT INTO audit_events (id, trace_id, session_id, channel, intent, services_called, escalation_json, evidence_ids, llm_used, latency_ms, created_at)
                VALUES (:id, :tid, NULL, :ch, :intent, :svc, :esc, '[]', :llm, :lat, :t)
            """),
            {
                "id": uuid4(),
                "tid": str(uuid4())[:8],
                "ch": "WEB_CHAT" if i % 2 == 0 else "WHATSAPP",
                "intent": conv_intents[i % len(conv_intents)],
                "svc": json.dumps(["tms", "tariffs"]) if i % 2 == 0 else json.dumps(["rag"]),
                "esc": json.dumps({"escalated": True, "criterio": "CRIT-002"})
                if i % 5 == 0
                else None,
                "llm": i % 3 != 0,
                "lat": 180 + (i * 25) % 400,
                "t": ts(i * 0.3),
            },
        )

    await session.commit()
    print("\n✅ ¡Todos los datos ficticios fueron insertados con éxito!")


async def main() -> None:
    settings = get_settings()
    if settings.pii_encryption_keys:
        configure_pii(settings.pii_encryption_keys.get_secret_value())
    else:
        print("⚠️ Advertencia: PII_ENCRYPTION_KEYS no está configurado.")

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            await seed(session)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
