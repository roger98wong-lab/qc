"""QC system FastAPI entrypoint."""
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config import BACKUP_DIR, REPORT_DIR, STATIC_DIR, UPLOAD_DIR
from database import init_db
from routers import (
    admin,
    analysis,
    assignments,
    auth,
    dashboard,
    dictionaries,
    knowledge_pool,
    reports,
    review_processing,
    workbench,
)


def create_app():
    app = FastAPI(title="QC System", version="1.0.0")
    for directory in (UPLOAD_DIR, REPORT_DIR, BACKUP_DIR):
        Path(directory).mkdir(parents=True, exist_ok=True)
    init_db()
    try:
        analysis.reconcile_stale_batches()
    except Exception:
        pass

    app.include_router(auth.router)
    app.include_router(analysis.router)
    app.include_router(reports.router)
    app.include_router(admin.router)
    app.include_router(assignments.router)
    app.include_router(workbench.router)
    app.include_router(knowledge_pool.router)
    app.include_router(review_processing.router)
    app.include_router(dashboard.router)
    app.include_router(dictionaries.router)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    index_file = Path(STATIC_DIR) / "index.html"

    def serve_spa_page():
        return FileResponse(index_file)

    if index_file.exists():
        for route in (
            "/dashboard",
            "/dashboard/review-workload",
            "/upload",
            "/report",
            "/history",
            "/admin",
            "/admin/audit",
            "/admin/reviews",
            "/my-reviews",
            "/login",
        ):
            app.add_api_route(route, serve_spa_page, methods=["GET"], include_in_schema=False)
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    host = os.getenv("QC_HOST", "0.0.0.0")
    port = int(os.getenv("QC_PORT", "8010"))
    uvicorn.run(app, host=host, port=port)
