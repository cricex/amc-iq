from __future__ import annotations

import html as _html
import re
from dataclasses import asdict
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

from app.auth import AuthenticatedUser, require_api_user
from app.config import get_settings
from app.eligibility import EligibilityUnavailable, evaluate_eligibility
from app.fabric_status import get_fabric_status
from app.orchestrator import Orchestrator
from app.registry import load_registry, maybe_find_by_id
from app.schemas import AskRequest, AskResult
from app.streaming import sse_response

settings = get_settings()
orchestrator = Orchestrator(settings=settings)

_ALLOWED_DOC_DIRS = {"foundry_docs", "work"}
_DOC_NAME = re.compile(r"[A-Za-z0-9._-]+\.(md|json)$")

app = FastAPI(title="AMC IQ API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Accept", "Authorization", "Content-Type"],
)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "mode": settings.mode}


@app.get("/api/fabric/status")
def fabric_status() -> dict[str, str | None]:
    """Read-only running/paused state of the backing Fabric F64 capacity, so the UI can tell users
    whether a live assessment will work. Never errors: any failure degrades to state "Unknown"."""
    return get_fabric_status(settings).to_dict()


@app.get("/api/evidence/doc", response_class=HTMLResponse)
def evidence_doc(path: str) -> HTMLResponse:
    """Serve a cited synthetic source document (from data/foundry_docs or data/work) so citation
    links open the real source content in a browser. The indexed Foundry IQ blobs are private and
    firewalled, so raw blob URLs cannot open; these local docs are the same content that was indexed.
    Path is restricted to a whitelist of directories and validated against traversal."""
    parts = path.split("/")
    if len(parts) != 2 or parts[0] not in _ALLOWED_DOC_DIRS or not _DOC_NAME.fullmatch(parts[1]):
        raise HTTPException(status_code=404, detail="Document not found")
    subdir, name = parts
    base = (settings.DATA_DIR / subdir).resolve()
    target = (base / name).resolve()
    if base not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="Document not found")

    body = _html.escape(target.read_text(encoding="utf-8"))
    title = _html.escape(name)
    page = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title><style>"
        "body{font:14px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;max-width:820px;"
        "margin:2rem auto;padding:0 1.25rem;color:#16213e;background:#fafafa}"
        "pre{white-space:pre-wrap;word-wrap:break-word;background:#fff;border:1px solid #e6e6ef;"
        "border-radius:8px;padding:1.25rem}"
        ".src{color:#6b7280;font-size:12px;margin-bottom:1rem;text-transform:uppercase;"
        "letter-spacing:.05em}</style></head>"
        f"<body><div class='src'>AMC IQ synthetic source document &middot; {title}</div>"
        f"<pre>{body}</pre></body></html>"
    )
    return HTMLResponse(page)


@app.post("/api/ask", response_model=AskResult)
def ask(
    request: AskRequest,
    user: AuthenticatedUser | None = Depends(require_api_user),
) -> AskResult:
    try:
        return orchestrator.answer(request, user.access_token if user else None)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except EligibilityUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/ask/stream")
def ask_stream(
    request: AskRequest,
    user: AuthenticatedUser | None = Depends(require_api_user),
) -> EventSourceResponse:
    return sse_response(request, orchestrator, user.access_token if user else None)


# ---- Cohort exploration (read-only) --------------------------------------------------------------
# Per-(patient, trial) eligibility over the synthetic cohort, evaluated on Fabric-native services
# (Fabric Data Agent facts + the Foundry eligibility-evaluator agent). Cohort-wide RANKING views
# (near-eligible patients, candidate trials) require Fabric Graph traversal (NL2Ontology) and are
# served from Fabric IQ directly once the graph is provisioned, not from this API.


@app.get("/api/cohort/patients/{patient_id}/trials/{trial_id}/eligibility")
def cohort_eligibility(
    patient_id: str,
    trial_id: str,
    _user: AuthenticatedUser | None = Depends(require_api_user),
) -> list[dict[str, Any]]:
    """Per-criterion eligibility evaluation for a (patient, trial) via Fabric-native services."""
    registry = load_registry()
    if maybe_find_by_id(registry["patients"], patient_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown patient id: {patient_id}")
    if maybe_find_by_id(registry["trials"], trial_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown trial id: {trial_id}")
    try:
        return [asdict(result) for result in evaluate_eligibility(settings, patient_id, trial_id)]
    except EligibilityUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
