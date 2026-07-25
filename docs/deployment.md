# Deployment

This document covers: live Azure provisioning, IaC (`infra/main.bicep`), the `azd` workflow,
teardown, and cost safeguards.

> **Warning:** Deploying live resources incurs Azure charges. Review the cost section below before
> proceeding. Always use `scripts/dev.ps1` (mock mode, free) for local demos.

## Prerequisites

- Azure CLI (`az`) — latest
- Azure Developer CLI (`azd`) — latest
- Python 3.12+ with the repo venv activated (`.venv`)
- Node.js 22+ with `npm ci` run in `apps/web/`
- An Azure subscription with sufficient quota for:
  - Azure Container Apps (API backend)
  - Azure Static Web Apps (React UI)
  - Azure AI Search (Foundry IQ KB + Web IQ KB) — operator-provided or pre-existing
  - Azure Key Vault (Work IQ OBO certificate)
  - Microsoft Fabric F64 capacity — operator-provided

## Provisioning the IQ service layer

The provisioning scripts in `agent/provisioning/live/` build the demo environment. They are
idempotent (check-then-create) and require explicit operator input for every resource name.
No owner-environment defaults exist.

```powershell
# Review available parameters before running anything
Get-Help ./agent/provisioning/live/provision_all.ps1 -Full

# Provision everything (supply every Mandatory parameter)
./agent/provisioning/live/provision_all.ps1 `
  -SubscriptionId <guid> `
  -ResourceGroup  <name> `
  ... (all Mandatory parameters)
```

| Script | What it provisions |
|---|---|
| `provision_all.ps1` | Full IQ service layer: Foundry account and models, storage, docs upload, Foundry IQ KB, Web IQ KB, Foundry project, role assignments, MCP connections, Fabric assets, hosted agent |
| `provision_foundry_iq.ps1` | Foundry IQ only: storage, docs upload, azureBlob knowledge source, knowledge base, smoke test |
| `provision_fabric_data_agent.ps1` | Fabric IQ: Lakehouse, Data Agent item, datasource, table selection, AI instructions, publish |
| `load_fabric_lakehouse.py` | Uploads `data/fabric/*.csv` to OneLake and loads as Delta tables |
| `create_agent.py` | Creates/updates the hosted agent with Foundry IQ and Web IQ tools, smoke test |
| `teardown.ps1` | Removes all `amciq`-prefixed Search and Fabric items, the Foundry account, project, and storage; `-DeleteResourceGroup` removes the whole RG |

## IaC (`infra/main.bicep`)

The Bicep template provisions the application hosting layer:

- Azure Container Apps managed environment (Log Analytics + App Insights)
- Container App for the FastAPI API (port 8000)
- Azure Static Web App for the React/Vite UI
- Azure Container Registry for `azd`-built API images
- Azure Key Vault (for Work IQ OBO certificate)
- User-assigned managed identity shared by the API container and role assignments

**Existing resources** (Foundry account, AI Search service, Fabric capacity) are **referenced by
parameter, not recreated** by the Bicep template.

Key parameters (all required, no defaults for resource names):

| Parameter | Description |
|---|---|
| `tenantId` | Entra tenant for Key Vault and managed identity |
| `apiContainerImage` | Container image; `azd deploy` replaces the placeholder |
| `managedIdentityName` | Shared managed identity resource name |
| `existingSearchServiceName` | Pre-existing AI Search service |
| `fabricWorkspaceId` | Fabric workspace GUID |
| `workIqClientId` | Confidential client app ID for Work IQ OBO |
| `webClientId` | SPA public client app ID |
| `apiAudience` | Accepted audience URI for token validation |
| `eligibilityEvaluatorAgentName` | Foundry evaluator agent name |

## azd workflow

```powershell
# Authenticate
az login --use-device-code
azd auth login

# First deploy (provisions Bicep + builds and pushes container)
azd up

# Subsequent code deploys (skips Bicep)
azd deploy

# Build and push the React UI to Static Web Apps
./agent/provisioning/live/deploy_web.ps1
```

## Configure live mode

After provisioning, copy `services/api/.env.example` to `services/api/.env` and fill in the
live coordinates. Then set the live flags:

```bash
USE_LIVE_FOUNDRY=true
USE_LIVE_FABRIC=true
USE_LIVE_WORK=true       # only if M365 tenant is seeded
USE_LIVE_WEB=true
USE_LIVE_AGENT=true      # only if hosted agent is provisioned
APP_ENVIRONMENT=production
```

Do not use `scripts/dev.ps1` for live testing — it always forces mock mode. Start the backend
manually with the live environment variables set.

## Clean-clone validation (pre-release)

Before declaring the release candidate ready, clone into a fresh directory and follow the README
exactly:

```powershell
git clone <repo-url> amciq-fresh
cd amciq-fresh
./scripts/dev.ps1
# Validate: backend starts, UI renders, hero question answers correctly
.\.venv\Scripts\python tools/validate_consistency.py
cd services/api; ..\..\.venv\Scripts\python -m pytest
cd apps/web; npm test
```

The repository is not ready if it only works in the original development environment.

## Teardown

```powershell
# Remove amciq-prefixed IQ resources (keeps the resource group)
Get-Help ./agent/provisioning/live/teardown.ps1 -Full
./agent/provisioning/live/teardown.ps1 -SubscriptionId <guid> -ResourceGroup <name>

# Remove the entire resource group (all Azure resources)
./agent/provisioning/live/teardown.ps1 -SubscriptionId <guid> -ResourceGroup <name> -DeleteResourceGroup

# Suspend (not delete) the Fabric capacity to stop per-second charges
az fabric capacity suspend --subscription <guid> --resource-group <rg> --capacity-name <name>
```

## Cost considerations

| Component | Cost driver | How to minimize |
|---|---|---|
| Azure Container Apps | Per-second CPU + memory when handling requests | Scale to zero when not in use |
| Azure Static Web Apps | Bandwidth; Standard tier for custom domains | Free tier covers demos |
| Azure AI Search | Search units (provisioned) + agentic-retrieval inference | Delete or pause between demos |
| Azure Container Registry | Storage + pull operations | Use Basic tier |
| Azure Key Vault | Operations per certificate read | Cached in process (5 min) |
| Foundry Agent Service | Model inference tokens per assessment | Disable `USE_LIVE_AGENT` when not needed |
| Microsoft Fabric F64 capacity | Per-second charges when Active | Suspend when cost control is desired |
| Work IQ (when GA) | API call charges | Disable `USE_LIVE_WORK` when not testing |
| Bing web KB (Web IQ) | Bing search transactions + inference | Disable `USE_LIVE_WEB` when not needed |

**Set Azure Budget alerts** on the subscription before enabling live resources. Use
`scripts/dev.ps1` (mock mode) for all demos that do not require live data.

## Known preview boundaries

- AI Search agentic-retrieval requires `message.content` as an array of parts, not a string.
- The `2026-05-01-preview` API version may change; monitor Azure AI Search release notes.
- The Fabric Data Agent is consumed through its MCP endpoint with `ProjectManagedIdentity`;
  the Fabric tool is gated behind `INCLUDE_FABRIC` in `create_agent.py`.
- Work IQ requires a seeded M365 tenant; there is no self-service setup path in this repository.
