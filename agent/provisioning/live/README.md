# Live Provisioning Scripts

Verified scripts that build the AMC IQ environment in Azure, Azure AI Search, and Microsoft Fabric.
They use your `az` login (device login supported). Synthetic data only.

Run from the repository root. Use each script's `Get-Help -Full` output for required inputs,
verification, and teardown safeguards.

## One command

```powershell
Get-Help ./agent/provisioning/live/provision_all.ps1 -Full
# Then invoke it with explicit values for every Mandatory target and resource-name parameter.
```

Idempotent (check then create). Switches: `-SkipFabricAssets`, `-IncludeFabric`.

## Scripts

| Script | What it does |
|---|---|
| `provision_all.ps1` | Orchestrates the IQ service layer: Foundry account and models, storage and docs, Foundry IQ knowledge base, Web IQ knowledge base, Foundry project, role assignments, MCP connections, Fabric assets and connection, hosted agent. Cloud app hosting uses `azd up` plus `deploy_web.ps1`. |
| `provision_foundry_iq.ps1` | Foundry IQ only: storage, docs upload, azureBlob knowledge source, knowledge base, and a retrieve smoke test. |
| `provision_fabric_data_agent.ps1` | Fabric IQ: Lakehouse, Data Agent item, datasource, table selection, AI instructions, publish. |
| `load_fabric_lakehouse.py` | Uploads `data/fabric/*.csv` to OneLake and loads them as Delta tables. Called by the Fabric script. |
| `create_agent.py` | Creates or updates the hosted agent `amciq-agent-eligibility` with the Foundry IQ and Web IQ tools, then runs a smoke test. Set `INCLUDE_FABRIC=true` to also attach the Fabric tool. |
| `teardown.ps1` | Removes the `amciq` prefixed Search and Fabric items and the Foundry account, project, and storage. Add `-DeleteResourceGroup` to remove the whole resource group. |

## Resource naming

Subscription, resource groups, account/service names, containers, knowledge bases/sources, Fabric
items, Foundry agents, and project connections are all operator-provided. The only remaining defaults
are public platform/model/API-version constants and safe generated names.

## Known preview boundaries

- Knowledge base `retrieve` requires `message.content` as an array of parts, not a string.
- Select a model deployment with sufficient quota in the operator's chosen region.
- The Fabric Data Agent is consumed through its MCP endpoint using the operator-selected RemoteTool
  connection with `ProjectManagedIdentity`. The Fabric tool is gated behind `INCLUDE_FABRIC`.
