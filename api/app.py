from fastapi import FastAPI
from api.routes.health import router as health_router
from api.routes.sessions import router as sessions_router

app = FastAPI(
    title="KubeWhisperer API",
    description="REST interface for the LangGraph RCA workflow — sessions, edge tracing, human-in-the-loop.",
    version="0.1.0",
)

app.include_router(health_router)
app.include_router(sessions_router)
