# Documentation Index

## Core Docs

| File | Description |
|------|-------------|
| [architecture.md](architecture.md) | Application architecture overview |
| [adding_features.md](adding_features.md) | Guide for adding new features |
| [patterns.md](patterns.md) | Code patterns and conventions |
| [structure_guide.md](structure_guide.md) | Project structure guide |
| [testing.md](testing.md) | Testing guidelines |
| [2026-03-31_changelog.md](2026-03-31_changelog.md) | Changelog: OpenPhone integration & dispatch classification |
| [2026-04-07_metrics_and_dispatcher_insights_roadmap.md](2026-04-07_metrics_and_dispatcher_insights_roadmap.md) | Roadmap: metrics, reports, and dispatcher shift insights |

## Guides

| File | Description |
|------|-------------|
| [2026-03-31_setup_and_next_steps.md](guides/2026-03-31_setup_and_next_steps.md) | **Superseded.** Original setup guide. Use the 2026-06-04 doc for current setup; the new 2026-06-08 doc for the company seed workflow. |
| [2026-06-04_whatsapp_extension.md](guides/2026-06-04_whatsapp_extension.md) | WhatsApp Web ingestion module — backend tables, service-account auth, and the Chrome extension. Current canonical setup guide. |
| [2026-06-08_seeding_and_reclassification.md](guides/2026-06-08_seeding_and_reclassification.md) | The notebook → `companies.json` → DB seed pipeline, the bulk-reclassify snippet, the two pattern tiers (structured vs free-form), and the OPENAI_API_KEY requirement. |
| [2026-06-08_companies_seed_reference.md](guides/2026-06-08_companies_seed_reference.md) | Snapshot of the 35 active companies in `companies.json` as of 2026-06-08, with regex/phone parameters per company and a pattern-shape taxonomy. |
| [2026-06-29_auto_deploy_setup.md](guides/2026-06-29_auto_deploy_setup.md) | Auto-deploy via SSH-from-laptop: how `git push && make deploy` rebuilds the VPS, including the SSH ControlMaster + per-call retry pattern that survives botnet-driven sshd throttling. Reusable recipe for new VPSes/projects. |
