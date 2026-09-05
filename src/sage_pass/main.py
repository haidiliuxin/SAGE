from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import Settings
from .database import Database
from .errors import AppError
from .real_executor import RealExecutor
from .routes import router
from .zip_adapter import ZipHashExtractor


def _error_payload(code: str, message: str, details: dict) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_env()
    database = Database(resolved.database_url)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        resolved.upload_dir.mkdir(parents=True, exist_ok=True)
        database.create_all()
        application.state.zip_extractor = ZipHashExtractor(
            resolved.zip2john_path
        )
        application.state.real_executor = RealExecutor(
            session_factory=database.session_factory,
            settings=resolved,
            zip_extractor=application.state.zip_extractor,
        )
        yield
        application.state.real_executor.shutdown()
        database.dispose()

    application = FastAPI(
        title="SAGE-Pass API",
        version="0.2.0",
        description=(
            "面向异构离线口令安全评测任务的编排 API。支持 mock 与基于 "
            "Hashcat 的真实执行（第二周），并接入 WinZip AES（$zip2$）"
            "加密 ZIP 目标；LLM 规划、动态调度与持久化恢复属于后续周次。"
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
