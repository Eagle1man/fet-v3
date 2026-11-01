"""Веб-приложение для ВМ вуза: просмотр, правка, сборка, публикация.

Один процесс FastAPI без сборки фронтенда: Jinja2 + немного CSS/JS.
Долгие операции (сборка, проверка FET, экспорт) выполняются в фоне,
страница показывает статус задания.
"""

from __future__ import annotations

import datetime as dt
import json
import mimetypes
import secrets
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .. import db, journal, pipeline, sitepub
from ..config import Settings
from ..normalize import split_groups

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES = WEB_DIR / "templates"
STATIC = WEB_DIR / "static"


@dataclass
class Job:
    id: str
    name: str
    status: str = "running"
    message: str = ""
    started: str = ""
    finished: str = ""
    log: list[str] = field(default_factory=list)


JOBS: dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()


def _now() -> str:
    return dt.datetime.now().strftime("%d.%m.%Y %H:%M:%S")


def _start_job(name: str, action: Callable[[], str]) -> Job:
    with _JOBS_LOCK:
        if any(job.status == "running" for job in JOBS.values()):
            raise RuntimeError("Уже выполняется другая операция")
        job = Job(id=uuid.uuid4().hex[:8], name=name, started=_now())
        JOBS[job.id] = job
        if len(JOBS) > 40:
            for key in sorted(JOBS, key=lambda k: JOBS[k].started)[:10]:
                JOBS.pop(key, None)

    def runner() -> None:
        try:
            job.message = action()
            job.status = "ok"
        except Exception as exc:  # noqa: BLE001 - статус задания должен быть виден
            job.status = "error"
            job.message = str(exc)
        finally:
            job.finished = _now()

    threading.Thread(target=runner, daemon=True).start()
    return job


def _recent_jobs(limit: int = 10) -> list[Job]:
    return sorted(JOBS.values(), key=lambda job: job.started, reverse=True)[:limit]


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="FET v3 — расписание", docs_url=None, redoc_url=None)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.web_secret,
        session_cookie="fetv3_session",
        max_age=12 * 3600,
        same_site="lax",
        https_only=False,
    )
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=["GET"],
            allow_headers=["*"],
        )
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
    templates = Jinja2Templates(directory=str(TEMPLATES))

    def auth_required() -> bool:
        return bool(settings.web_password)

    def is_admin(request: Request) -> bool:
        if not auth_required():
            return True
        return bool(request.session.get("auth"))

    def csrf(request: Request) -> str:
        token = request.session.get("csrf")
        if not token:
            token = secrets.token_urlsafe(24)
            request.session["csrf"] = token
        return str(token)

    def guard(request: Request, *, write: bool = False) -> Response | None:
        if write:
            if not is_admin(request):
                return RedirectResponse("/login", status_code=303)
            return None
        if is_admin(request):
            return None
        if settings.public_read:
            return None
        return RedirectResponse("/login", status_code=303)

    def check_csrf(request: Request, token: str) -> bool:
        expected = str(request.session.get("csrf") or "")
        return bool(expected) and secrets.compare_digest(expected, token)

    def base_context(request: Request, **extra: Any) -> dict[str, Any]:
        return {
            "request": request,
            "school": settings.school,
            "is_admin": is_admin(request),
            "auth_required": auth_required(),
            "csrf": csrf(request),
            "jobs": _recent_jobs(),
            **extra,
        }

    def connect() -> Any:
        conn = db.connect(settings.db_path)
        db.init_schema(conn)
        journal.init_journal(conn)
        return conn

    # ---------- служебные ----------

    @app.get("/healthz", response_class=PlainTextResponse)
    def healthz() -> str:
        conn = connect()
        try:
            count = db.lesson_count(conn)
        finally:
            conn.close()
        return f"ok lessons={count}"

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request) -> Response:
        if not auth_required():
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context=base_context(request, error=""),
        )

    @app.post("/login")
    def login(
        request: Request,
        password: str = Form(""),
        next_url: str = Form("/"),
    ) -> Response:
        if not auth_required() or secrets.compare_digest(password, settings.web_password):
            request.session["auth"] = True
            target = next_url if next_url.startswith("/") else "/"
            return RedirectResponse(target, status_code=303)
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context=base_context(request, error="Неверный пароль"),
            status_code=401,
        )

    @app.get("/logout")
    def logout(request: Request) -> Response:
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    # ---------- страницы ----------

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> Response:
        blocked = guard(request)
        if blocked:
            return blocked
        conn = connect()
        try:
            periods = db.list_periods(conn)
            lessons = db.lesson_count(conn)
            teachers = conn.execute("SELECT COUNT(DISTINCT teacher_key) AS n FROM lessons").fetchone()["n"]
            rooms = conn.execute("SELECT COUNT(DISTINCT room) AS n FROM lessons WHERE room <> ''").fetchone()[
                "n"
            ]
        finally:
            conn.close()

        builds_path = settings.out_dir / "build_summary.json"
        validation_path = settings.validation_dir / "summary.json"
        builds = []
        if builds_path.exists():
            builds = json.loads(builds_path.read_text(encoding="utf-8-sig")).get("builds", [])
        validation = {}
        if validation_path.exists():
            validation = json.loads(validation_path.read_text(encoding="utf-8-sig"))
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context=base_context(
                request,
                periods=periods,
                lessons=lessons,
                teachers=teachers,
                rooms=rooms,
                builds=builds,
                validation=validation,
                exports=sitepub.list_exports(settings)[:5],
                auth_warning=not auth_required(),
            ),
        )

    @app.get("/lessons", response_class=HTMLResponse)
    def lessons(
        request: Request,
        group: str = "",
        teacher: str = "",
        room: str = "",
        date_from: str = "",
        date_to: str = "",
        period: str = "",
        row: str = "",
        msg: str = "",
    ) -> Response:
        blocked = guard(request)
        if blocked:
            return blocked
        conn = connect()
        try:
            rows = journal.search(
                conn,
                group=group or None,
                teacher=teacher or None,
                room=room or None,
                date_from=date_from or None,
                date_to=date_to or None,
                limit=300,
            )
            groups: set[str] = set()
            for item in conn.execute("SELECT groups_raw FROM lessons"):
                groups.update(split_groups(item["groups_raw"]))
            teachers = [
                item["teacher"]
                for item in conn.execute(
                    "SELECT teacher, MIN(teacher_key) AS k FROM lessons GROUP BY teacher_key ORDER BY k"
                )
            ]
            rooms = [
                item["room"]
                for item in conn.execute(
                    "SELECT DISTINCT room FROM lessons WHERE room <> '' ORDER BY room"
                )
            ]
            selected = None
            if period and row.isdigit():
                found = conn.execute(
                    "SELECT * FROM lessons WHERE period = ? AND source_row = ?",
                    (period, int(row)),
                ).fetchone()
                selected = dict(found) if found else None
        finally:
            conn.close()
        return templates.TemplateResponse(
            request=request,
            name="lessons.html",
            context=base_context(
                request,
                rows=rows,
                groups=sorted(groups),
                teachers=teachers,
                rooms=rooms,
                filters={
                    "group": group,
                    "teacher": teacher,
                    "room": room,
                    "date_from": date_from,
                    "date_to": date_to,
                },
                selected=selected,
                msg=msg,
            ),
        )

    @app.post("/lessons/edit")
    def edit_lesson(
        request: Request,
        csrf_token: str = Form(""),
        period: str = Form(...),
        source_row: int = Form(...),
        iso_date: str = Form(...),
        pair: int = Form(...),
        room: str = Form(""),
        teacher: str = Form(...),
        subject: str = Form(...),
        actor: str = Form("сотрудник"),
    ) -> Response:
        blocked = guard(request, write=True)
        if blocked:
            return blocked
        if not check_csrf(request, csrf_token):
            return RedirectResponse("/lessons?msg=CSRF", status_code=303)
        conn = connect()
        try:
            journal.apply_update(
                conn,
                period=period,
                source_row=source_row,
                changes={
                    "iso_date": iso_date,
                    "pair": pair,
                    "room": room,
                    "teacher": teacher,
                    "subject": subject,
                },
                actor=actor or "сотрудник",
            )
        except journal.JournalError as exc:
            return RedirectResponse(f"/lessons?period={period}&row={source_row}&msg={exc}", status_code=303)
        finally:
            conn.close()
        return RedirectResponse("/lessons?msg=Сохранено", status_code=303)

    @app.get("/week", response_class=HTMLResponse)
    def week(
        request: Request,
        group: str = "",
        week: str = "",
    ) -> Response:
        blocked = guard(request)
        if blocked:
            return blocked
        conn = connect()
        try:
            groups: set[str] = set()
            for item in conn.execute("SELECT groups_raw FROM lessons"):
                groups.update(split_groups(item["groups_raw"]))
            all_groups = sorted(groups)
            if not group:
                group = all_groups[0] if all_groups else ""
            rows = [
                item
                for item in db.load_lessons(conn)
                if group in split_groups(item.groups_raw)
            ]
        finally:
            conn.close()

        weeks: list[str] = []
        meta: dict[str, Any] | None = None
        grid: dict[str, dict[str, list[dict[str, Any]]]] = {}
        if rows:
            payload = sitepub.group_schedule(rows, sitepub._scope_label(rows))
            weeks = sorted(payload[1], key=lambda value: int(value))
            if week not in weeks:
                week = weeks[-1] if weeks else ""
            data = payload[1].get(week)
            if data:
                meta, grid = data[0], data[1]
        return templates.TemplateResponse(
            request=request,
            name="week.html",
            context=base_context(
                request,
                groups=all_groups,
                group=group,
                week=week,
                weeks=weeks,
                meta=meta,
                grid=grid,
                hours=settings.hours,
            ),
        )

    @app.get("/conflicts", response_class=HTMLResponse)
    def conflicts(request: Request) -> Response:
        blocked = guard(request)
        if blocked:
            return blocked
        path = settings.out_dir / "build_summary.json"
        builds = json.loads(path.read_text(encoding="utf-8-sig")).get("builds", []) if path.exists() else []
        return templates.TemplateResponse(
            request=request,
            name="conflicts.html",
            context=base_context(request, builds=builds),
        )

    @app.get("/publish", response_class=HTMLResponse)
    def publish(request: Request, msg: str = "") -> Response:
        blocked = guard(request)
        if blocked:
            return blocked
        return templates.TemplateResponse(
            request=request,
            name="publish.html",
            context=base_context(
                request,
                exports=sitepub.list_exports(settings),
                latest=sitepub.latest_version(settings),
                ftp_configured=bool(settings.ftp_host),
                msg=msg,
            ),
        )

    # ---------- задания ----------

    def job_response(job: Job) -> Response:
        return RedirectResponse(f"/publish?msg=Задание+{job.name}+запущено", status_code=303)

    @app.post("/jobs/build")
    def job_build(request: Request, csrf_token: str = Form("")) -> Response:
        blocked = guard(request, write=True)
        if blocked:
            return blocked
        if not check_csrf(request, csrf_token):
            return RedirectResponse("/publish?msg=CSRF", status_code=303)
        try:
            job = _start_job("Сборка .fet", lambda: _build(settings))
        except RuntimeError as exc:
            return RedirectResponse(f"/publish?msg={exc}", status_code=303)
        return job_response(job)

    @app.post("/jobs/validate")
    def job_validate(request: Request, csrf_token: str = Form("")) -> Response:
        blocked = guard(request, write=True)
        if blocked:
            return blocked
        if not check_csrf(request, csrf_token):
            return RedirectResponse("/publish?msg=CSRF", status_code=303)
        try:
            job = _start_job("Проверка FET", lambda: _validate(settings))
        except RuntimeError as exc:
            return RedirectResponse(f"/publish?msg={exc}", status_code=303)
        return job_response(job)

    @app.post("/jobs/export")
    def job_export(request: Request, csrf_token: str = Form("")) -> Response:
        blocked = guard(request, write=True)
        if blocked:
            return blocked
        if not check_csrf(request, csrf_token):
            return RedirectResponse("/publish?msg=CSRF", status_code=303)
        try:
            job = _start_job("Экспорт на сайт", lambda: _export(settings))
        except RuntimeError as exc:
            return RedirectResponse(f"/publish?msg={exc}", status_code=303)
        return job_response(job)

    @app.post("/publish/upload")
    def upload(
        request: Request,
        csrf_token: str = Form(""),
        version: str = Form(""),
    ) -> Response:
        blocked = guard(request, write=True)
        if blocked:
            return blocked
        if not check_csrf(request, csrf_token):
            return RedirectResponse("/publish?msg=CSRF", status_code=303)
        try:
            result = sitepub.upload_ftp(settings, version)
        except Exception as exc:  # noqa: BLE001 - сообщение показываем пользователю
            return RedirectResponse(f"/publish?msg={exc}", status_code=303)
        return RedirectResponse(
            f"/publish?msg=Выгружено+файлов+{result['uploaded']}", status_code=303
        )

    # ---------- API ----------

    @app.get("/api/site/groups")
    def api_groups(request: Request) -> Response:
        if guard(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        payload = sitepub.build_site_payload(settings)
        return JSONResponse(payload["groups"])

    @app.get("/api/site/schedule")
    def api_schedule(request: Request, group: str = "") -> Response:
        if guard(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if not group:
            return JSONResponse({"error": "group is required"}, status_code=400)
        payload = sitepub.build_site_payload(settings)
        schedule = payload["schedules"].get(group)
        if schedule is None:
            return JSONResponse({}, status_code=404)
        return JSONResponse({group: schedule})

    @app.get("/api/jobs")
    def api_jobs(request: Request) -> Response:
        if guard(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse(
            [
                {
                    "id": job.id,
                    "name": job.name,
                    "status": job.status,
                    "message": job.message,
                    "started": job.started,
                    "finished": job.finished,
                }
                for job in _recent_jobs()
            ]
        )

    @app.get("/download/{version}")
    def download(request: Request, version: str) -> Response:
        blocked = guard(request)
        if blocked:
            return blocked
        try:
            sitepub.resolve_export(settings, version)
        except (ValueError, FileNotFoundError):
            return PlainTextResponse("Выгрузка не найдена", status_code=404)
        archive = settings.site_dir / f"site_{version}.zip"
        if not archive.exists():
            return PlainTextResponse("Архив не найден", status_code=404)
        return FileResponse(
            archive,
            media_type="application/zip",
            filename=f"site_{version}.zip",
        )

    # ---------- публичная страница расписания ----------

    def latest_export() -> Path | None:
        version = sitepub.latest_version(settings)
        if not version:
            return None
        try:
            return sitepub.resolve_export(settings, version)
        except (ValueError, FileNotFoundError):
            return None

    @app.get("/schedule", include_in_schema=False)
    def schedule_redirect() -> Response:
        return RedirectResponse("/schedule/", status_code=307)

    @app.get("/schedule/", response_class=HTMLResponse, include_in_schema=False)
    def schedule_index() -> Response:
        export = latest_export()
        if export is None:
            return HTMLResponse(
                "<!doctype html><meta charset='utf-8'>"
                "<h1>Расписание не опубликовано</h1>"
                "<p>Выполните экспорт в панели или командой <code>run.py export</code>.</p>",
                status_code=404,
            )
        index = export / "index.html"
        if not index.exists():
            return PlainTextResponse("В выгрузке нет index.html", status_code=500)
        return FileResponse(index, media_type="text/html", headers={"Cache-Control": "no-cache"})

    @app.get("/schedule/{resource:path}", include_in_schema=False)
    def schedule_resource(resource: str) -> Response:
        export = latest_export()
        if export is None:
            return PlainTextResponse("Расписание не опубликовано", status_code=404)
        root = export.resolve()
        target = (root / resource).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            return PlainTextResponse("Файл не найден", status_code=404)
        media_type, _ = mimetypes.guess_type(target.name)
        headers = {"Cache-Control": "no-cache" if resource.endswith(".json") else "public, max-age=600"}
        return FileResponse(target, media_type=media_type or "application/octet-stream", headers=headers)

    return app


def _build(settings: Settings) -> str:
    payloads = pipeline.build_fet(settings)
    total = sum(item["stats"]["activities"] for item in payloads)
    return f"Собрано файлов: {len(payloads)}, активностей: {total}"


def _validate(settings: Settings) -> str:
    summary = pipeline.validate_fet(settings)
    bad = [name for name, item in summary.items() if item["fatal"] or not item["static_ok"]]
    return "Все файлы в порядке" if not bad else "Ошибки: " + ", ".join(bad)


def _export(settings: Settings) -> str:
    result = sitepub.export_site(settings)
    return (
        f"Выгрузка {result.version}: групп {result.groups}, "
        f"занятий {result.lessons}, архив {result.zip_path.name}"
    )
