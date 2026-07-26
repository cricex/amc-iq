# AMC IQ naming and consistency standard

Use clear, operator-owned names and never commit subscription, tenant, workspace, application,
or resource identifiers.

## Resource names

Use the prefix `amciq` for resources created by this proof of concept:

```text
amciq-<layer>-<purpose>[-<environment>]
```

- Layers include `foundry`, `fabric`, `work`, `web`, `agent`, `api`, `web-ui`, and `core`.
- Use short kebab-case purposes such as `eligibility`, `trials`, `docs`, or `oncology`.
- Add an environment suffix such as `dev`, `test`, or `prod` when resources are not shared.
- Use `snake_case` only where a service does not permit hyphens.
- Existing resources are always explicit operator inputs; provisioning must not infer private
  subscriptions, tenants, workspaces, or service names.

## Tags

Taggable resources should identify the project and environment without personal data:

```text
project = amc-iq
environment = <dev|test|prod>
managed-by = <azd|bicep|operator>
```

## Synthetic entity names

All patient, trial, person, site, date, and key clinical identifiers are defined in
[`data/registry/registry.yaml`](../data/registry/registry.yaml). Generators and fixtures should load
that registry rather than duplicate identifiers.

Trial IDs use the reserved project range `NCT99xxxxxx` and must be rechecked against
ClinicalTrials.gov immediately before publication. See [`data/README.md`](../data/README.md).

## Consistency rules

1. Keep the same synthetic identifiers across every data layer and the UI.
2. Keep live resource coordinates in environment configuration, never source files.
3. Prefer deterministic generation and explicit operator inputs.
4. Remove abandoned resources, scripts, and private deployment notes before release.
