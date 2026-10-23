
from __future__ import annotations
from contextlib import asynccontextmanager

from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI):
    from api.session_store import init_store
    from api.routes import sessions as sessions_mod
    from persistence.db import db_path
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    init_store()

    from persistence.db import init_db
    init_db()
    # No vector store is loaded here: each investigation indexes the cluster it collects
    # (docs/fix-plan-001.md, H40).

    cp_path = str(db_path()).replace(".db", "-checkpoints.db")
    async with AsyncSqliteSaver.from_conn_string(cp_path) as checkpointer:
        sessions_mod._graph = sessions_mod._build_graph(checkpointer)
        yield


app = FastAPI(
    title="KubeVerdict API",
    description="REST interface for the LangGraph RCA workflow — sessions, edge tracing, human-in-the-loop.",
    version="0.1.4",
    lifespan=lifespan,
)

from api.routes.health import router as health_router            # noqa: E402
from api.routes.sessions import router as sessions_router        # noqa: E402
from api.routes.webhook import router as webhook_router          # noqa: E402
from api.routes.investigate import router as investigate_router  # noqa: E402

app.include_router(health_router)
app.include_router(sessions_router)
app.include_router(webhook_router)
app.include_router(investigate_router)

import telemetry  # noqa: E402
telemetry.instrument(app)
