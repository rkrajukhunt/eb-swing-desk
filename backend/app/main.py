from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .adapters.factory import active_adapter
from .api import routes_misc, routes_paper, routes_scan
from .config import env
from .database import init_db
from .engine.scanner import run_scan
from .paper.engine import auto_exit_loop, stop_auto_exit_loop
from .services.settings_store import get_settings
from .utils.market_hours import IST, is_trading_day

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("swingdesk")


def _scheduled_scan() -> None:
    if not is_trading_day():
        log.info("scheduled scan skipped — holiday/weekend")
        return
    try:
        run_scan(active_adapter(), "all", refresh=True)
        log.info("scheduled scan complete")
    except Exception:
        log.exception("scheduled scan failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(auto_exit_loop())

    scheduler = AsyncIOScheduler(timezone=IST)
    settings = get_settings()
    if settings.get("scan_schedule_enabled"):
        try:
            trigger = CronTrigger.from_crontab(settings["scan_schedule_cron"], timezone=IST)
            scheduler.add_job(lambda: asyncio.to_thread(_scheduled_scan), trigger, id="scan")
            log.info("scan scheduled: %s IST", settings["scan_schedule_cron"])
        except ValueError as e:
            log.warning("invalid scan cron '%s': %s", settings.get("scan_schedule_cron"), e)
    scheduler.start()

    yield

    scheduler.shutdown(wait=False)
    stop_auto_exit_loop()
    task.cancel()


app = FastAPI(title="EB Swing Desk", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in env.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_scan.router)
app.include_router(routes_paper.router)
app.include_router(routes_misc.router)
