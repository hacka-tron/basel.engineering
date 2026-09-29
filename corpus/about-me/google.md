---
type: role
tags: [google, fitbit, youtube, testing, release-engineering, ci-cd]
priority: high
---

# Google (September 2019 – July 2023)

## Overview

Basel spent about 4 years at Google in San Francisco, driving test automation, release-gating infrastructure, and cross-platform device testing across two teams: Fitbit's DevEx team and YouTube's Living Room team.

## Fitbit DevEx team

On Fitbit's DevEx team, Basel built a browser-based end-to-end release gate running hundreds of tests in under 10 minutes by parallelizing execution across 30 shared job runners, with quarantine controls for flaky tests to keep the gate trustworthy. He aligned automated testing with Fitbit's canary, dogfood, and staging release cycles by partnering with developers across release forums, integrating automated test suites into the Git-based CI/CD pipeline, and extending supporting Python services to accelerate releases.

## YouTube Living Room team

On YouTube's Living Room team, Basel designed a cross-platform account seeding solution spanning Android, Web, and Living Room consoles (Xbox, PlayStation, Switch), extending backend RPC endpoints and deploying secure API endpoints to staging. This enabled automated coverage of roughly 70% of test cases and expanded overall scenario coverage to 93%. He also drove performance and end-to-end validation work for those consoles, partnering with developers on maintainable test coverage.

## Shared testing infrastructure and mentoring

Across both teams, Basel designed and implemented a TypeScript-based end-to-end testing framework supporting 10,000+ daily automated executions at 99.5% reliability, gating org-wide pull requests and deployments in CI/CD pipelines; he introduced a page-object abstraction layer over the underlying React components to keep the tests maintainable. He built a custom Java/ADB mobile automation framework from scratch to catch and prevent build regressions on real devices and emulators, which was adopted as core internal tooling across 2 sister teams and became the org standard. He enhanced Java network libraries to onboard untrusted third-party devices onto testing resources, driving device test coverage from 75% to 88% and lifting multi-platform success rates from 80% to 95%.

Basel engineered a 55-point lift in automated test success rates (20% to 75%) while establishing the team's inaugural on-call rotation to catch regressions during development deployments. He built React dashboards and interactive Storybook tooling giving engineering teams granular visibility into component performance metrics, informing 4 internal design decisions. He also mentored and onboarded 4 sister engineering teams to integrate the automation framework into their core application deployment pipelines.
