# Troubleshooting

Connected, live issues are listed first, followed by deterministic local and mock fallback issues.

## Backend will not start: `SettingsConfigurationError`

**Symptom:** `SettingsConfigurationError: Live/production configuration is incomplete or still uses public placeholders: AZURE_TENANT_ID, ...`

**Cause:** One or more live flags (`USE_LIVE_*`) are `true` but the required coordinates are
missing or still hold `.example.invalid` placeholder values.

**Fix (local mode):** Ensure `APP_ENVIRONMENT=development` and all six live flags are `false`.
Use `scripts/dev.ps1`, which sets this automatically.

**Fix (connected mode):** Fill in every required variable listed in the error message. See
[`docs/configuration.md`](configuration.md) for the flag routing matrix.

---

## Backend will not start: `anonymous_mock_enabled` is unexpectedly `false`

**Symptom:** The `/api/ask` route requires a bearer token even though you are running locally.

**Cause:** `APP_ENVIRONMENT` is not set to one of `development`, `dev`, `local`, or `test`,
or at least one live flag is `true`.

**Fix:** Set `APP_ENVIRONMENT=development` and ensure all `USE_LIVE_*` flags are `false`. Check
for a `.env` file in `services/api/` that overrides the defaults.

---

## Fabric capacity `CapacityNotActive`

**Symptom:** Live Fabric IQ returns an error containing `CapacityNotActive`.

**Cause:** The backing Fabric F64 capacity is paused.

**Fix:** Resume the capacity via the Fabric portal or CLI:
```bash
az fabric capacity resume --subscription <guid> --resource-group <rg> --capacity-name <name>
```
Allow 2–5 minutes for the capacity to reach Active state before retrying.

---

## AI Search KB retrieve fails: `400 Bad Request`

**Symptom:** Live Foundry IQ or Web IQ returns HTTP 400.

**Cause:** The `message.content` field may not be in the required array-of-parts format, or the
`api-version` is wrong.

**Fix:** Confirm `SEARCH_API_VERSION=2026-05-01-preview`. `LiveFoundryIQ` and `LiveWebIQ`
already send `content` as `[{"type":"text","text":"..."}]`; verify no middleware is
rewriting the body.

---

## Work IQ OBO fails: `token_exchange_failed`

**Symptom:** Live Work IQ returns `WorkIQTokenExchangeError: Work IQ OBO failed (...)`.

**Cause (common):** The user's delegated token does not have the Work IQ scope, or the OBO
certificate in Key Vault is missing or expired.

**Fix:**
1. Confirm the user has consented to the Work IQ delegated scope.
2. Confirm `WORK_IQ_CLIENT_ID`, `WORK_IQ_ENDPOINT`, and `WORK_IQ_SCOPE` are set correctly.
3. In production, confirm the managed identity has `Key Vault Secret User` on `WORK_IQ_KEY_VAULT_URL`
   and that `WORK_IQ_CLIENT_CERTIFICATE_SECRET_NAME` exists in the vault.
4. In development, confirm `WORK_IQ_CLIENT_CERTIFICATE` (base64 PFX) and
   `WORK_IQ_CLIENT_CERTIFICATE_THUMBPRINT` are set.

---

## CORS error in browser

**Symptom:** The frontend reports a CORS error when calling the API.

**Cause:** The API's `CORS_ORIGINS` does not include the origin where the UI is running.

**Fix:** Add the frontend origin, for example `http://localhost:5173`, to `CORS_ORIGINS` in the
backend environment. `scripts/dev.ps1` sets this automatically to match the Vite port.

---

## Local mode: data validation errors

**Symptom:** `tools/validate_consistency.py` exits with non-zero; or `pytest` reports fixture
errors about missing patient or trial IDs.

**Cause:** The Fabric CSV tables are out of sync with `data/registry/registry.yaml`, or the
hero rows were accidentally changed.

**Fix:**
```powershell
.\.venv\Scripts\python data/fabric/generate.py
.\.venv\Scripts\python tools/validate_consistency.py
```

---

## `data/web/` MockWebIQ raises `ValueError: Bundled Web IQ fixtures must be explicitly marked synthetic`

**Symptom:** The local Web IQ adapter throws a `ValueError`.

**Cause:** A fixture file in `data/web/` is missing `"synthetic": true` in either the
`manifest.json` entry or the fixture JSON file itself.

**Fix:** Add `"synthetic": true` to both the manifest entry and the fixture file, or remove the
fixture if it was accidentally added.

---

## CI: `git diff --check` whitespace failure

**Symptom:** CI fails on the `Check patch whitespace` step.

**Cause:** A commit introduced trailing whitespace, mixed line endings, or a blank line at end of file.

**Fix:** Run `git diff --check` locally and fix the flagged lines.

---

## Frontend: blank page or JS error on load

**Symptom:** `http://localhost:5173` shows a blank page or console errors about missing env vars.

**Cause (common):** Entra variables (`VITE_ENTRA_*`) are partially set, some set, some empty.

**Fix:** Either set all three Entra variables or set none of them. `scripts/dev.ps1` sets all
three to empty, which correctly triggers the unauthenticated local path.

---

## Frontend SSE stream ends without a `final` event

**Symptom:** The assessment never completes; the UI shows source tiles done but no result.

**Cause:** The backend returned an `error` event or the SSE connection dropped.

**Fix:** Check the backend logs. If the backend is unavailable, the frontend falls back to
`POST /api/ask` (synchronous). If that also fails, it renders the static `sampleResult.ts`
fallback with `AssessmentFailureCard`.

---

## Evidence citation links (`/api/evidence/doc`) return 404

**Symptom:** Clicking a citation link returns 404.

**Cause:** The `path` query parameter does not match the allowlist or the file does not exist.

**Fix:** Only `foundry_docs/<file>.md` and `work/<file>.md` paths are served. Confirm the
synthetic document exists in `data/foundry_docs/` or `data/work/` and the filename matches exactly.
