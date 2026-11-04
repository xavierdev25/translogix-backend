"""Phase 1 (CR-011): ObjectStorage local/S3, upload and download through the API, authorization,
compensation and migration of existing files. S3 is simulated with moto."""

import hashlib
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text

from app.contracts.staff import Role
from app.db.models import AttachmentRow, AttachmentStorageMigrationRow
from app.shared.storage import (
    LocalObjectStorage,
    ObjectConflictError,
    S3ObjectStorage,
    object_key,
    sha256_hex,
    validate_key,
)
from app.storage import migration
from tests.auth.test_rbac import staff
from tests.aws.conftest import FakeAws
from tests.db import AppFactory, http, scalar
from tests.shipments.support import auth, new_client

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
KEY = "attachments/2026/11/0b6f3c1e-8a4d-4f2b-9e7a-5c3d2b1a0f9e.pdf"
TABLES = (
    "attachment_storage_migrations, attachments, audit_events, handoffs, messages, conversations"
)


# --- adapters ----------------------------------------------------------------------------------


async def test_local_adapter_is_atomic_idempotent_and_never_overwrites(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path)
    digest = sha256_hex(PDF)

    await storage.put(KEY, PDF, content_type="application/pdf", sha256=digest)
    await storage.put(KEY, PDF, content_type="application/pdf", sha256=digest)  # retry: no-op
    with pytest.raises(ObjectConflictError):
        await storage.put(KEY, b"%PDF-otro%%EOF", content_type="application/pdf", sha256="0" * 64)
    info = await storage.head(KEY)

    assert await storage.get(KEY) == PDF
    assert info is not None
    assert (info.size, info.sha256) == (len(PDF), digest)
    assert (
        await storage.presigned_get(KEY, filename="a.pdf", content_type="application/pdf", ttl=60)
        is None
    )
    await storage.delete(KEY)
    assert await storage.head(KEY) is None
    assert not list(tmp_path.rglob("*.tmp"))  # noqa: ASYNC240 - test inspection


@pytest.mark.parametrize(
    "key",
    [
        "../../etc/passwd",
        "attachments/2026/11/x.pdf",
        "/abs/0b6f3c1e-8a4d-4f2b-9e7a-5c3d2b1a0f9e.pdf",
        "a.exe",
    ],
)
def test_object_keys_from_outside_are_rejected(key: str) -> None:
    with pytest.raises(ValueError, match="clave"):
        validate_key(key)


async def test_s3_adapter_encrypts_with_kms_and_never_overwrites(fake_aws: FakeAws) -> None:
    storage = S3ObjectStorage(fake_aws.s3, fake_aws.bucket, fake_aws.kms_key_id)
    digest = sha256_hex(PDF)

    await storage.put(KEY, PDF, content_type="application/pdf", sha256=digest)
    await storage.put(KEY, PDF, content_type="application/pdf", sha256=digest)  # idempotent retry
    with pytest.raises(ObjectConflictError):
        await storage.put(
            KEY,
            b"%PDF-otro%%EOF",
            content_type="application/pdf",
            sha256=sha256_hex(b"%PDF-otro%%EOF"),
        )
    head = fake_aws.s3.head_object(Bucket=fake_aws.bucket, Key=KEY)
    url = await storage.presigned_get(KEY, filename="a.pdf", content_type="application/pdf", ttl=60)
    objects = fake_aws.s3.list_objects_v2(Bucket=fake_aws.bucket)["KeyCount"]

    assert head["ServerSideEncryption"] == "aws:kms"
    assert fake_aws.kms_key_id in head["SSEKMSKeyId"]
    assert head["Metadata"] == {"sha256": digest}
    assert await storage.get(KEY) == PDF
    assert url is not None
    assert "X-Amz-Signature" in url
    assert "X-Amz-Expires=60" in url
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in url  # SigV4: required for SSE-KMS
    assert objects == 1  # the retry did not duplicate
    await storage.delete(KEY)
    assert await storage.head(KEY) is None


# --- through the API ---------------------------------------------------------------------------


async def chat(c: httpx.AsyncClient, words: str = "Hola") -> tuple[str, str]:
    client_id = str(uuid.uuid4())
    body = {"client_id": client_id, "message_id": str(uuid.uuid4()), "text": words}
    reply = (await c.post("/api/v1/chat/messages", json=body)).json()["reply"]
    return client_id, reply["session_id"]


async def upload(
    c: httpx.AsyncClient, client_id: str, session: str, data: bytes = PDF
) -> httpx.Response:
    return await c.post(
        "/api/v1/attachments",
        files={"file": ("x.pdf", data, "application/pdf")},
        data={"session_id": session},
        headers={"X-Client-Id": client_id},
    )


async def _clean(app: FastAPI) -> None:
    async with app.state.container.sessions() as session:
        await session.execute(text(f"TRUNCATE {TABLES} CASCADE"))
        await session.commit()


@pytest.fixture
async def s3_app(make_app: AppFactory, fake_aws: FakeAws, tmp_path: Path) -> AsyncIterator[FastAPI]:
    async with make_app(
        attachments_storage="s3",
        s3_bucket=fake_aws.bucket,
        s3_kms_key_id=fake_aws.kms_key_id,
        attachments_dir=tmp_path,
        aws_region="us-east-1",
    ) as app:
        await _clean(app)
        yield app


async def test_upload_and_download_through_s3_with_ownership(
    s3_app: FastAPI,
    fake_aws: FakeAws,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    _, reviewer = await staff(s3_app, "rev.s3@translogix.co", Role.REVIEWER)
    _, admin = await staff(s3_app, "adm.s3@translogix.co", Role.ADMIN)
    _, _, api_key = await new_client(s3_app, nit="902000001-1")
    async with http(s3_app) as c:
        me, session = await chat(c)
        stranger, _ = await chat(c)
        stored = await upload(c, me, session)
        attachment = stored.json()["attachment_id"]
        content = f"/api/v1/attachments/{attachment}/content"
        mine = await c.get(content, headers={"X-Client-Id": me})
        link = await c.get(f"/api/v1/attachments/{attachment}/link", headers={"X-Client-Id": me})
        other = await c.get(content, headers={"X-Client-Id": stranger})
        anonymous = await c.get(content)
        by_admin = await c.get(content, headers=auth(admin))
        by_reviewer = await c.get(content, headers=auth(reviewer))
        by_client = await c.get(content, headers=auth(api_key))
        arbitrary = await c.get(
            "/api/v1/attachments/..%2F..%2Fetc%2Fpasswd/content", headers={"X-Client-Id": me}
        )
    key = await scalar(
        s3_app, "SELECT storage_ref FROM attachments WHERE attachment_id = :a", a=attachment
    )
    backend = await scalar(
        s3_app, "SELECT storage_backend FROM attachments WHERE attachment_id = :a", a=attachment
    )
    head = fake_aws.s3.head_object(Bucket=fake_aws.bucket, Key=key)

    assert stored.status_code == 201
    assert backend == "s3"
    assert key == object_key(attachment, "pdf", _created(key))
    assert head["ServerSideEncryption"] == "aws:kms"
    assert (mine.status_code, mine.content) == (200, PDF)
    assert mine.headers["cache-control"] == "no-store"
    assert link.status_code == 200
    assert "X-Amz-Signature" in link.json()["url"]
    assert (other.status_code, anonymous.status_code) == (404, 401)
    assert by_admin.status_code == 200
    assert by_reviewer.status_code == 404  # no case of that session in the reviewer's inbox
    assert by_client.status_code == 403
    assert arbitrary.status_code == 404
    logs = caplog.text + capsys.readouterr().out
    assert me not in logs  # neither the client id...
    assert PDF.decode() not in logs  # ...nor the document reach logs or metrics


def _created(key: str) -> Any:
    _, year, month, _ = key.split("/")
    return datetime(int(year), int(month), 1, tzinfo=UTC)


async def test_local_storage_serves_content_but_issues_no_links(
    app: FastAPI, tmp_path: Path
) -> None:
    await _clean(app)
    async with http(app) as c:
        me, session = await chat(c)
        attachment = (await upload(c, me, session)).json()["attachment_id"]
        content = await c.get(
            f"/api/v1/attachments/{attachment}/content", headers={"X-Client-Id": me}
        )
        link = await c.get(f"/api/v1/attachments/{attachment}/link", headers={"X-Client-Id": me})

    assert (content.status_code, content.content) == (200, PDF)
    assert link.json()["error"]["code"] == "NO_CONFIGURADO"


async def test_a_failed_row_write_deletes_the_object(
    s3_app: FastAPI, fake_aws: FakeAws, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.db.repositories import AttachmentRepository

    async def broken(*_: Any, **__: Any) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(AttachmentRepository, "add", broken)
    async with http(s3_app) as c:
        me, session = await chat(c)
        with pytest.raises(RuntimeError):
            await upload(c, me, session)

    assert fake_aws.s3.list_objects_v2(Bucket=fake_aws.bucket)["KeyCount"] == 0  # compensated


async def test_an_unreachable_bucket_answers_503(s3_app: FastAPI, fake_aws: FakeAws) -> None:
    fake_aws.s3.delete_bucket(Bucket=fake_aws.bucket)
    async with http(s3_app) as c:
        me, session = await chat(c)
        response = await upload(c, me, session)

    assert (response.status_code, response.json()["error"]["code"]) == (
        503,
        "ALMACENAMIENTO_NO_DISPONIBLE",
    )


# --- migration of existing files ---------------------------------------------------------------


async def _legacy_attachment(app: FastAPI, root: Path, data: bytes = PDF) -> str:
    """A row as written before 2.1.0: bare file name, local backend."""
    attachment_id = str(uuid.uuid4())
    (root / f"{attachment_id}.pdf").write_bytes(data)
    async with app.state.container.sessions() as session:
        await session.execute(
            text(
                "INSERT INTO attachments (attachment_id, kind, mime_type, size_bytes, sha256,"
                " storage_ref, created_at, storage_backend) VALUES (:a, 'DOCUMENT',"
                " 'application/pdf', :n, :h, :r, now(), 'local')"
            ),
            {
                "a": attachment_id,
                "n": len(PDF),
                "h": hashlib.sha256(PDF).hexdigest(),
                "r": f"{attachment_id}.pdf",
            },
        )
        await session.commit()
    return attachment_id


async def test_migration_copies_verifies_resumes_and_keeps_originals(
    s3_app: FastAPI, fake_aws: FakeAws, tmp_path: Path
) -> None:
    good = await _legacy_attachment(s3_app, tmp_path)
    corrupt = await _legacy_attachment(s3_app, tmp_path)
    (tmp_path / f"{corrupt}.pdf").write_bytes(b"%PDF-alterado%%EOF")  # no longer matches its row
    missing = await _legacy_attachment(s3_app, tmp_path)
    (tmp_path / f"{missing}.pdf").unlink()
    source = LocalObjectStorage(tmp_path)
    target = s3_app.state.container.storage
    now = s3_app.state.container.clock.now()

    async with s3_app.state.container.sessions() as session:
        first = await migration.copy(session, source, target, now=now)
        again = await migration.copy(session, source, target, now=now)  # re-run: resumes
        checked = await migration.verify(session, source, target, now=now)
        counted = await migration.cleanup(session, source, confirm=False)
        records = {
            r.attachment_id: r
            for r in (await session.scalars(select(AttachmentStorageMigrationRow))).all()
        }
        rows = {r.attachment_id: r for r in (await session.scalars(select(AttachmentRow))).all()}

    assert (first.copiados, first.fallidos) == (1, 2)
    assert first.errores == {"SOURCE_HASH_MISMATCH": 1, "SOURCE_MISSING": 1}
    assert (again.copiados, again.fallidos) == (0, 2)  # the copied one is not processed again
    assert fake_aws.s3.list_objects_v2(Bucket=fake_aws.bucket)["KeyCount"] == 1
    assert checked.verificados == 1
    assert (records[good].status, records[corrupt].status, records[missing].status) == (
        "VERIFIED",
        "FAILED",
        "FAILED",
    )
    assert records[corrupt].attempts == 2
    assert rows[good].storage_backend == "s3"
    assert rows[corrupt].storage_backend == "local"  # untouched
    assert (tmp_path / f"{good}.pdf").exists()  # originals kept until cleanup --confirm
    assert (counted.procesados, counted.eliminados_locales) == (1, 0)

    async with s3_app.state.container.sessions() as session:
        removed = await migration.cleanup(session, source, confirm=True)
    assert removed.eliminados_locales == 1
    assert not (tmp_path / f"{good}.pdf").exists()
    assert (tmp_path / f"{corrupt}.pdf").exists()  # never touched: it was not verified
