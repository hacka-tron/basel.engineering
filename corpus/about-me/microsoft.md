---
type: role
tags: [microsoft, azure, distributed-systems, infrastructure-as-code, release-engineering]
priority: high
---

# Microsoft (July 2024 – Current)

## Role overview

At Microsoft, Basel is a Software Engineer on the core Azure team, working on a service that catalogs over 35 million virtual containers and physical assets globally and provides realtime updates about this data to core platform teams. The role spans reliability engineering, infrastructure-as-code, release engineering, and security hardening for a mission-critical, globally-scaled service.

## Reliability and rate-limiting

Basel designed and implemented distributed rate-limiting and advanced deny-list capabilities to manage data access and eliminate critical usage spikes, reducing outages on this service from roughly 2 per month to 0 — each incident carried about $1 million in downstream business impact given the criticality of the service. He also reduced service CPU utilization by 53% and stabilized service uptime by replacing free-form Kusto queries with standardized functions and materialized tables, an initiative he proposed and led a group of 3 engineers to complete.

## Infrastructure-as-code and safe deployment

Basel designed infrastructure-as-code for a Service Fabric managed cluster using ARM templates and Ev2 safe deployment, provisioning the cluster, Key Vault, and managed identity in an orchestrated sequence with staged regional rollouts, managed validation between waves, and incident-linked expedited paths for emergency fixes. He also automated certificate lifecycle management through Key Vault and an internal certificate authority with mid-life auto-renewal, and replaced certificate-based telemetry authentication with a user-assigned managed identity.

## Service decoupling and release isolation

Basel decoupled two core services from a shared Service Fabric application into independent packages with dedicated, governed release pipelines, isolating rollbacks, ring progression, and failure blast radius between them. He executed the cutover through a dark-deployment approach to prevent duplicate consumers before enabling automated releases.

## Security hardening

Basel hardened cluster infrastructure with Trusted Launch, Secure Boot, internal node images, automatic OS and runtime patching, Azure AD just-in-time access, restricted management ports, and availability-zone resiliency.

## CI/CD, tooling, and team leadership

Basel standardized CI/CD on 1ES Pipeline Templates with release gating and consistent build versioning across build, test, and release stages, eliminating manual rollout steps and saving roughly 20 engineering hours per week. He integrated automated AI code review into Azure DevOps pull requests to shorten review cycles, and increased team code coverage from 40% to 85% by leveraging Microsoft Copilot to retroactively add unit tests to the codebase. He built automated C# ticketing and alerting systems to monitor inventory health and trigger real-time alerts for unmanaged hardware states, helping the team catalog 95% of unmanaged device scenarios.

On the team side, Basel acted as technical lead for 2 junior engineers, mentoring them on distributed systems, Azure service design, and code quality standards, and accelerated their path to independent ownership within 3 months. He also established team engineering standards, including on-call handoff processes, formal design doc reviews, and end-to-end testing requirements — spearheading and upholding the team's foundational technical workflows.
