from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from app.models import entities as m
from app.services.config import queue_revision


async def test_repeated_saves_reuse_pending_snapshot():
    pending = SimpleNamespace(id=uuid4())
    db = SimpleNamespace(execute=AsyncMock(), scalar=AsyncMock(return_value=pending), add=Mock(), flush=AsyncMock())
    assert await queue_revision(db) is pending
    db.add.assert_not_called()
    sql = str(db.scalar.call_args.args[0].compile(compile_kwargs={"literal_binds": True}))
    assert "BUILD_REVISION" in sql and "PENDING" in sql
    assert "RUNNING" not in sql  # Running work might already have read old desired state.


async def test_save_queues_durable_global_target_without_rendering():
    db = SimpleNamespace(execute=AsyncMock(), scalar=AsyncMock(return_value=None), add=Mock(), flush=AsyncMock())
    async def flush():
        db.add.call_args_list[0].args[0].id = uuid4()
    db.flush.side_effect = flush
    job = await queue_revision(db)
    values = [call.args[0] for call in db.add.call_args_list]
    assert job.kind == "BUILD_REVISION"
    assert isinstance(values[1], m.JobTarget)
    assert values[1].job_id == job.id and values[1].agent_id is None
    assert isinstance(values[2], m.JobEvent)
