from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import Settings
from .database import Database
from .errors import AppError
from .routes import router


def _error_payload(code: str, message: str, details: dict) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_env()
    database = Database(resolved.database_url)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        resolved.upload_dir.mkdir(parents=True, exist_ok=True)
        database.create_all()
        yield
        database.dispose()

    application = FastAPI(
        title="SAGE-Pass API",
        version="0.1.0",
        description=(
            "面向异构离线口令安全评测任务的编排 API。第一周仅提供系统骨架、"
            "任务持久化与公共数据契约，不执行真实口令恢复。"
        ),
        lifespan=lifespan,
    )
    application.state.settings = resolved
    application.state.database = database
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(router)

    @application.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(exc.code, exc.message, exc.details),
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = exc.errors()
        first = errors[0] if errors else {}
        field = ".".join(str(item) for item in first.get("loc", [])[1:])
        issues = []
        for error in errors:
            issue = {
                key: value
                for key, value in error.items()
                if key not in {"ctx", "input"}
            }
            if "ctx" in error:
                issue["ctx"] = {
                    key: str(value) for key, value in error["ctx"].items()
                }
            issues.append(issue)
        details = {"field": field, "issues": issues}
        return JSONResponse(
            status_code=422,
            content=_error_payload("INVALID_TASK", "任务输入错误", details),
        )

    @application.exception_handler(HTTPException)
    async def http_error_handler(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(
                "INTERNAL_ERROR" if exc.status_code >= 500 else "INVALID_TASK",
                str(exc.detail),
                {},
            ),
        )

    return application


app = create_app()
