# API Contract: AMC IQ proof-of-concept backend ⇄ frontend

The FastAPI backend (`services/api`) and React UI (`apps/web`) integrate through this contract.
The same shape is returned whether the repository is running in deterministic local and mock mode,
the default path for `scripts/dev.ps1`, or in the connected live topology.

## Source enum

`"foundry"` (Foundry IQ) · `"fabric"` (Fabric IQ) · `"work"` (Work IQ) · `"web"` (Web IQ)

## Endpoints

### `POST /api/ask` -> `AskResult` (non-streaming)

Request:
```json
{ "question": "string", "patientId": "PT-1042 (optional)" }
```

### `POST /api/ask/stream` -> Server-Sent Events

Emits ordered events, then closes:

| `event:` | `data` payload | UI effect |
|---|---|---|
| `plan` | `{ "steps": ["Checking diagnosis & stage", ...] }` | render agent trace steps |
| `source_query` | `{ "source": "fabric", "label": "Fabric IQ", "query": "latest CrCl for PT-1042", "status": "querying" }` | light up that IQ tile |
| `source_result` | `{ "source": "fabric", "status": "done", "summary": "CrCl 48 (2026-06-18)", "citations": ["r3"], "durationMs": 120 }` | tile -> done, attach citations |
| `token` | `{ "text": "..." }` (optional) | stream answer text |
| `final` | `{ "result": AskResult }` | render all panels |
| `error` | `{ "message": "..." }` | error state |

## `AskResult` shape

```jsonc
{
  "question": "string",
  "patient": {
    "id": "PT-1042", "mrn": "MRN-0001042", "display": "Alex Morgan",
    "age": 61, "sex": "F", "ecog": 1,
    "diagnosis": "Metastatic NSCLC (adenocarcinoma)", "stage": "IV",
    "biomarkers": ["EGFR exon 20 insertion"],
    "crcl": 48, "crclDate": "2026-06-18"
  },
  "eligibility": {
    "assessment": "likely_eligible_pending",
    "label": "Likely eligible, pending repeat CrCl and PI confirmation",
    "confidence": "medium"
  },
  "trial": { "id": "NCT99004324", "short": "EGFR exon 20 NSCLC trial", "status": "Recruiting" },
  "criteria": [
    { "text": "Documented EGFR exon 20 insertion", "status": "met", "evidenceRefs": ["r1","r5"] },
    { "text": "ECOG performance status 0-1", "status": "met", "evidenceRefs": ["r3"] },
    { "text": "CrCl >= 50 mL/min (CKD-EPI)", "status": "uncertain", "evidenceRefs": ["r3","r2"] },
    { "text": "Prior platinum-based chemotherapy exclusion", "status": "uncertain", "evidenceRefs": ["r2","r6"] }
  ],
  "evidence": [
    { "refId": "r1", "source": "foundry", "title": "Genomics report, PT-1042",
      "snippet": "EGFR exon 20 insertion (p.A767_V769dup)...", "url": "internal://...", "sourceType": "genomics" },
    { "refId": "r3", "source": "fabric", "title": "labs.csv, CrCl history",
      "snippet": "2026-06-18 CrCl 48 mL/min; 2026-05-20 CrCl 55", "url": null, "sourceType": "structured" }
  ],
  "missingData": [
    "Repeat CrCl (last 48 mL/min, below the 50 threshold)",
    "PI confirmation on the prior-platinum exclusion (line of therapy)"
  ],
  "nextAction": {
    "text": "Order repeat CrCl and route the case to the thoracic oncology trial coordinator; attach the evidence summary and flag the renal threshold.",
    "owner": { "id": "COORD-01", "display": "Dana Whitfield", "role": "Thoracic Oncology Trial Coordinator" },
    "taskType": "route_to_coordinator"
  },
  "humanReview": { "owner": { "id": "PI-01", "display": "Dr. Priya Anand", "role": "Principal Investigator" },
                     "reason": "Confirm prior-platinum exclusion interpretation" },
  "sourceMap": [
    { "source": "foundry", "label": "Foundry IQ", "status": "done", "queries": ["EGFR exon 20 eligibility criteria"], "citations": ["r1","r2"], "durationMs": 210 },
    { "source": "fabric", "label": "Fabric IQ", "status": "done", "queries": ["latest CrCl PT-1042"], "citations": ["r3"], "durationMs": 120 },
    { "source": "work",   "label": "Work IQ",   "status": "done", "queries": ["tumor board PT-1042 owner"], "citations": ["r4"], "durationMs": 90 },
    { "source": "web",    "label": "Web IQ",    "status": "done", "queries": ["NCT99004324 registry"], "citations": ["r7"], "durationMs": 160 }
  ],
  "trace": [ { "step": "Checking diagnosis & stage", "source": "fabric", "detail": "...", "ts": 0 } ],
  "mode": "mock",
  "disclaimer": "Synthetic data. No PHI. Not clinical decision support."
}
```

## Config (backend env)

`USE_LIVE_FOUNDRY`, `USE_LIVE_FABRIC`, `USE_LIVE_WORK`, and `USE_LIVE_WEB` default to `false`,
so the repository stays on local fallback until an operator explicitly enables live paths.
`CORS_ORIGINS` defaults to `http://localhost:5173`. The backend serves on port `8000`.
