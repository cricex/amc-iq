# Deployment

This document describes the connected Azure topology first, then the deterministic local fallback
used for safe reproducibility.

> **Warning:** Live resources incur Azure charges. Review the cost section before proceeding.
> Use `scripts/dev.ps1` whenever you want the no-cloud local walkthrough.

## Connected Azure topology

The proof of concept's connected footprint is:
- Azure Static Web Apps for the React UI,
- Azure Container Apps for the FastAPI API,
- Azure AI Search for Foundry IQ and the Web IQ stand-in,
- Microsoft Fabric for the Lakehouse and Data Agent,
- Azure Key Vault for the Work IQ OBO certificate, and
- operator-provided Foundry resources referenced by configuration.

## Prerequisites

- Azure CLI (`az`), current release
- Azure Developer CLI (`azd`), current release
- Python 3.12+ with the repo venv activated (`.venv`)
- Node.js 22+ with `npm ci` run in `apps/web/`
- An Azure subscription with sufficient quota for:
  - Azure Container Apps
  - Azure Static Web Apps
  - Azure AI Search, operator-provided or pre-existing
  - Azure Key Vault
  - Microsoft Fabric F64 capacity, operator-provided

## Provisioning the intelligence layer

The provisioning scripts in `agent/provisioning/live/` build the connected environment. They are
idempotent, check-then-create, and require explicit operator input for every resource name.

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
| `provision_all.ps1` | Full intelligence layer: Foundry account and models, storage, docs upload, Foundry IQ KB, Web IQ KB stand-in, Foundry project, role assignments, MCP connections, Fabric assets, hosted agent |
| `provision_foundry_iq.ps1` | Foundry IQ only: storage, docs upload, `azureBlob` knowledge source, knowledge base, smoke test |
| `provision_fabric_data_agent.ps1` | Fabric IQ: Lakehouse, Data Agent item, datasource, table selection, AI instructions, publish |
| `load_fabric_lakehouse.py` | Uploads `data/fabric/*.csv` to OneLake and loads them as Delta tables |
| `create_agent.py` | Creates or updates the hosted agent with Foundry IQ and Web IQ tools, then smoke-tests it |
| `teardown.ps1` | Removes `amciq`-prefixed Search and Fabric items, the Foundry account, project, and storage; `-DeleteResourceGroup` removes the whole resource group |

## IaC (`infra/main.bicep`)

The Bicep template provisions the application hosting layer:

- Azure Container Apps managed environment, with Log Analytics and App Insights
- Container App for the FastAPI API, port 8000
- Azure Static Web App for the React/Vite UI
- Azure Container Registry for `azd`-built API images
- Azure Key Vault for the Work IQ OBO certificate
- User-assigned managed identity shared by the API container and role assignments

**Existing resources**, including Foundry, AI Search, and Fabric capacity, are **referenced by
parameter, not recreated**.

Key parameters, all required and operator-supplied:

| Parameter | Description |
|---|---|
| `tenantId` | Entra tenant for Key Vault and the managed identity |
| `apiContainerImage` | Container image, `azd deploy` replaces the placeholder |
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

## Enable live mode

After provisioning, copy `services/api/.env.example` to `services/api/.env`, fill in the
operator-owned coordinates, then enable the live flags you intend to use:

```dotenv
USE_LIVE_FOUNDRY=true
USE_LIVE_FABRIC=true
USE_LIVE_WORK=true       # only if the M365 tenant is seeded
USE_LIVE_WEB=true
USE_LIVE_AGENT=true      # only if the hosted agent is provisioned
APP_ENVIRONMENT=production
```

Do not use `scripts/dev.ps1` for connected validation, it always forces local fallback mode.
Start the backend manually with the live environment variables set.

## Cost considerations

| Component | Cost driver | How to minimize |
|---|---|---|
| Azure Container Apps | Per-second CPU + memory when handling requests | Scale to zero when not in use |
| Azure Static Web Apps | Bandwidth; Standard tier for custom domains | Free tier can cover low-traffic reviews |
| Azure AI Search | Search units, plus agentic-retrieval inference | Delete or pause between operator sessions |
| Azure Container Registry | Storage + pull operations | Use Basic tier |
| Azure Key Vault | Operations per certificate read | Cached in process for five minutes |
| Foundry Agent Service | Model inference tokens per assessment | Disable `USE_LIVE_AGENT` when not needed |
| Microsoft Fabric F64 capacity | Per-second charges when Active | Suspend when cost control is desired |
| Work IQ, when GA billing applies | API call charges | Disable `USE_LIVE_WORK` when not testing |
| Bing web KB, Web IQ stand-in | Bing search transactions + inference | Disable `USE_LIVE_WEB` when not needed |

Set Azure Budget alerts on the subscription before enabling live resources.

## Deterministic local fallback

`scripts/dev.ps1` always disables live flags, clears Entra SPA values, points `DATA_DIR` at the
checked-in synthetic package, starts the API in local mode, and launches the Vite UI without cloud
dependencies. Use it for reproducible walkthroughs that do not require connected services.

## Clean-clone validation (pre-release)

Before declaring the repository ready for handoff, validate from a fresh checkout:

```powershell
git clone <repo-url> amciq-fresh
cd amciq-fresh
./scripts/dev.ps1
# Validate: backend starts, UI renders, hero question answers correctly
.\.venv\Scripts\python tools/validate_consistency.py
cd services/api; ..\..\.venv\Scripts\python -m pytest
cd apps/web; npm test
```

The proof of concept is not ready if it only works in the original development environment.

## Teardown

```powershell
# Remove amciq-prefixed IQ resources (keeps the resource group)
Get-Help ./agent/provisioning/live/teardown.ps1 -Full
./agent/provisioning/live/teardown.ps1 -SubscriptionId <guid> -ResourceGroup <name>

# Remove the entire resource group (all Azure resources)
./agent/provisioning/live/teardown.ps1 -SubscriptionId <guid> -ResourceGroup <name> -DeleteResourceGroup

# Suspend, not delete, the Fabric capacity to stop per-second charges
az fabric capacity suspend --subscription <guid> --resource-group <rg> --capacity-name <name>
```

## Known preview boundaries

- AI Search agentic retrieval requires `message.content` as an array of parts, not a string.
- The `2026-05-01-preview` API version may change, monitor Azure AI Search release notes.
- The Fabric Data Agent is consumed through its MCP endpoint with `ProjectManagedIdentity`.
- Work IQ requires a seeded M365 tenant, there is no self-service setup path in this repository.
