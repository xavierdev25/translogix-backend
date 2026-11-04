"""Two turns of one conversation run one after the other (audit 2026-11-04, CON-01).

Without the row lock both turns read the same state and the later commit overwrote the
earlier one's slot (measured against the running API). The in-process HTTP client runs
requests one at a time, so the lock is checked here with two database sessions.
"""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI

from app.contracts.common import ChannelType
from app.contracts.conversation import ConversationState, ConversationStatus
from app.db.repositories import ConversationRepository


async def stored_conversation(app: FastAPI) -> ConversationState:
    now = datetime.now(UTC)
    state = ConversationState(
        session_id=uuid.uuid4(),
        channel=ChannelType.WEB_CHAT,
        external_user_id_hash="0" * 64,
        status=ConversationStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )
    async with app.state.container.sessions() as session:
        await ConversationRepository(session).save(state)
        await session.commit()
    return state


@pytest.mark.parametrize("locked", [True, False])
async def test_a_turn_waits_for_the_one_holding_the_conversation(
    app: FastAPI, locked: bool
) -> None:
    state = await stored_conversation(app)
    sessions = app.state.container.sessions
    async with sessions() as first, sessions() as second:
        await ConversationRepository(first).get(state.session_id, for_update=True)
        reader = asyncio.create_task(
            ConversationRepository(second).get(state.session_id, for_update=locked)
        )
        await asyncio.sleep(0.3)

        assert reader.done() is not locked  # only a locking read has to wait
        await first.commit()
        assert (await asyncio.wait_for(reader, timeout=5)) is not None
