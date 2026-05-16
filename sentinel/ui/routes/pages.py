"""Full-page GET routes for the UI."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

import sentinel
from sentinel.ui.server import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "title": "API Sentinel",
            "version": sentinel.__version__,
        },
    )
