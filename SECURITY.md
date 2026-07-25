# Security

## Reporting a vulnerability

**Do not open a public GitHub issue for an active security vulnerability.**

Please report security vulnerabilities privately through
[GitHub private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability)
on this repository.

Include in your report:

- A clear description of the vulnerability and the affected component.
- Steps to reproduce, including any payloads, configurations, or environment assumptions.
- The potential impact: data exposure, privilege escalation, prompt injection, retrieval poisoning,
  or other concern.
- Any suggested mitigations or patches if you have them.

Please do not include live credentials, real patient data, or PHI in a vulnerability report.

## Supported versions

This is a demonstration project without a formal release cadence. Only the current `main` branch
is actively maintained.

## Response process

The repository owner will acknowledge reports within a reasonable time and communicate a remediation
plan or disclosure timeline. Because this is a personal demonstration project, response times may
vary.

## Out of scope

- Issues in the Azure platform, Microsoft Foundry, or Microsoft Fabric services themselves should
  be reported to Microsoft through [MSRC](https://www.microsoft.com/en-us/msrc).
- Vulnerabilities that require physical access to the operator's Azure subscription.
- Issues in third-party dependencies should be reported to the upstream maintainer.

## Security design

See [`docs/security.md`](docs/security.md) for the full threat model, authentication boundaries,
prompt injection risks, retrieval poisoning considerations, and deployment recommendations.
