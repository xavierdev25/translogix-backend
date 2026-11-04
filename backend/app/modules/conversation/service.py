"""``ConversationService`` (P08): session resolution, idempotency, persistence and audit.

One instance per request, bound to one database session; the orchestrator holds
the dialogue logic. A turn is committed atomically: inbound message, reply,
conversation state and audit event.
"""

import hashlib
import hmac
from collections.abc import Callable
from datetime import timedelta
from time import perf_counter
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.common import ChannelType, ContractError, ErrorCode
from app.contracts.conversation import ConversationState, ConversationStatus
from app.contracts.escalation import HistorialItem
from app.contracts.inbound import InboundMessage
from app.contracts.ports import AuditEvent, Clock
from app.contracts.reply import ConversationReply, ReplyMeta, TextBlock
from app.db.repositories import (
    AuditRepository,
    ConversationRepository,
    DuplicateMessageError,
    MessageRepository,
)
from app.modules.conversation.orchestrator import DialogueOrchestrator
from app.shared.http import SlidingWindowRateLimiter

HISTORY_FOR_HANDOFF = 10


def hash_user(salt: str, channel: ChannelType, external_user_id: str) -> str:
    """External ids are persisted only as an HMAC with ``PII_HASH_SALT``."""
    message = f"{channel.value}:{external_user_id}".encode()
    return hmac.new(salt.encode(), message, hashlib.sha256).hexdigest()


def reply_text(reply: ConversationReply) -> str:
    return " ".join(b.text for b in reply.blocks if isinstance(b, TextBlock))


class ConversationService:
    def __init__(
        self,
        session: AsyncSession,
        orchestrator: Callable[[AsyncSession], DialogueOrchestrator],
        *,
        clock: Clock,
        salt: str,
        ttl_minutes: dict[ChannelType, int],
        limiter: SlidingWindowRateLimiter,
    ) -> None:
        self._session = session
        self._orchestrator = orchestrator(session)
        self._conversations = ConversationRepository(session)
        self._messages = MessageRepository(session)
        self._audit = AuditRepository(session)
        self._clock = clock
        self._salt = salt
        self._ttl = ttl_minutes
        self._limiter = limiter

    async def handle(
        self, msg: InboundMessage, *, trace_id: str | None = None
    ) -> ConversationReply:
        started = perf_counter()
        existing = await self._messages.find_reply(msg.channel, msg.message_id)
        if existing is not None:  # idempotent resend: same reply, nothing reprocessed
            return existing
        user_hash = hash_user(self._salt, msg.channel, msg.external_user_id)
        self._limiter.check(f"{msg.channel.value}:{user_hash}")
        now = self._clock.now()
        state = await self._resolve(msg, user_hash)
        await self._conversations.save(state)  # the session row must exist before its messages
        try:
            await self._messages.add_inbound(msg, state.session_id, now)
        except DuplicateMessageError:  # concurrent resend: the savepoint was rolled back
            again = await self._messages.find_reply(msg.channel, msg.message_id)
            if again is not None:
                return again
            raise ContractError(
                ErrorCode.VALIDACION, "Mensaje duplicado en proceso; reintenta en unos segundos."
            ) from None

        history = await self._history(state.session_id)
        outcome = await self._orchestrator.respond(state, msg, history)
        if state.status not in (ConversationStatus.ESCALATED, ConversationStatus.CLOSED):
            state.status = ConversationStatus.AWAITING_USER
        state.updated_at = now
        services = list(dict.fromkeys(outcome.services))
        reply = ConversationReply(
            session_id=state.session_id,
            in_reply_to=msg.message_id,
            reply_id=uuid4(),
            channel=msg.channel,
            status=state.status,
            blocks=outcome.blocks,
            meta=ReplyMeta(
                trace_id=trace_id or str(uuid4()),
                intent=outcome.intent,
                confidence_score=outcome.confidence,
                confidence_method="heuristic_v1" if outcome.confidence is not None else None,
                escalation=outcome.escalation,
                services_called=services,
                evidence_ids=outcome.evidence,
                llm_used=outcome.llm_used,
                delivery_mode=msg.delivery_mode,
            ),
            created_at=now,
        )
        await self._messages.add_outbound(reply)
        await self._conversations.save(state)
        await self._audit.record(
            AuditEvent(
                id=uuid4(),
                trace_id=reply.meta.trace_id,
                session_id=state.session_id,
                channel=msg.channel,
                intent=outcome.intent,
                services_called=services,
                escalation=outcome.escalation,
                evidence_ids=outcome.evidence,
                llm_used=outcome.llm_used,
                latency_ms=int((perf_counter() - started) * 1000),
                created_at=now,
            )
        )
        await self._session.commit()
        return reply

    async def _resolve(self, msg: InboundMessage, user_hash: str) -> ConversationState:
        moment = self._clock.now()
        if msg.session_id is not None:
            state = await self._conversations.get(msg.session_id, for_update=True)
            # Another user's or another channel's session is reported as missing (no oracle).
            if (
                state is None
                or state.channel is not msg.channel
                or state.external_user_id_hash != user_hash
            ):
                raise ContractError(ErrorCode.NO_ENCONTRADO, details={"recurso": "session"})
            expired = state.updated_at < moment - timedelta(minutes=self._ttl[msg.channel])
            if state.status is not ConversationStatus.CLOSED and not expired:
                return state
        else:
            since = moment - timedelta(minutes=self._ttl[msg.channel])
            state = await self._conversations.latest_open(
                msg.channel, user_hash, since, for_update=True
            )
            if state is not None:
                return state
        return ConversationState(
            session_id=uuid4(),
            channel=msg.channel,
            external_user_id_hash=user_hash,
            status=ConversationStatus.ACTIVE,
            created_at=moment,
            updated_at=moment,
        )

    async def _history(self, session_id: UUID) -> list[HistorialItem]:
        items: list[HistorialItem] = []
        for row in await self._messages.history(session_id):
            if "reply" in row.content_json:
                reply = ConversationReply.model_validate(row.content_json["reply"])
                text, role = reply_text(reply), "ASISTENTE"
            else:
                text, role = row.content_json["content"].get("text") or "", "USUARIO"
            if text:
                items.append(
                    HistorialItem(
                        rol=role,  # type: ignore[arg-type]
                        texto=text,
                        timestamp=row.created_at,
                        canal=ChannelType(row.channel),
                    )
                )
        return items[-HISTORY_FOR_HANDOFF:]
