---
type: skills
tags: [languages, cloud, infrastructure, databases, frontend, ai-tools]
priority: normal
---

# Skills

## Languages

Python, Java, C#, TypeScript, JavaScript, SQL. C# is his primary language at Microsoft (alerting/ticketing systems, service code); Java and TypeScript were central to the test automation frameworks he built at Google; Python shows up across both roles in supporting tooling and services.

## Cloud and infrastructure

Azure (including Service Fabric, ARM Templates, Key Vault, Managed Identity), AWS, GCP, and Docker. At Microsoft, this is hands-on infrastructure-as-code work — provisioning Service Fabric clusters, Key Vault, and managed identities via ARM templates with Ev2 safe deployment. On the AWS side, CryptoKing (a personal project) uses Lambda for serverless API logic and S3 for static hosting; this site (Glassbox) itself runs on AWS with Terraform, EC2, RDS, and Kubernetes (k3s).

## CI/CD and release engineering

Azure DevOps, 1ES/OneBranch Pipelines, Ev2 Safe Deployment, GitHub Actions. Basel standardized CI/CD on 1ES Pipeline Templates at Microsoft, saving roughly 20 engineering hours per week, and built Git-based CI/CD integration for automated test suites at Google.

## Observability

Geneva, Grafana, Kusto (Azure Data Explorer). Kusto specifically: Basel replaced free-form Kusto queries with standardized functions and materialized tables at Microsoft, cutting service CPU utilization by 53%.

## Databases and storage

PostgreSQL, MongoDB, FASTER KV, and Kusto. Redis and Kafka round out the messaging/caching side — Redis in particular is used extensively in this site's own architecture (vector index, caching layers, job queue).

## Frontend

React, React Native, Angular. React Native and Expo power the Goal Buddy mobile app; React (plus an internal page-object abstraction layer over React components) was used in the E2E testing frameworks Basel built at Google.

## AI-assisted development

GitHub Copilot, Claude Code, and Codex. At Microsoft, Basel used Copilot to retroactively add unit tests and raise team code coverage from 40% to 85%, and integrated automated AI code review into Azure DevOps pull requests. This site (Glassbox) was itself built using a multi-model pipeline: Claude as architect/reviewer, Codex for implementation, and Gemini for additional review.
