# Documentation

| File | What it does |
|---|---|
| `architecture.md` | Layers, request and action paths, design choices, shared vs domain code |
| `roadmap.md` | Phases 0 to 6, built vs planned, the next three domains |
| `value-case.md` | Phase 0: the problem, KPI baselines and targets, the result, value attribution |
| `mit-article-mapping.md` | How each idea in the MIT Sloan article maps to code (paraphrased, credited by link) |
| `ai-business-models.md` | The four AI-era business models and what the data layer provides for each |
| `operating-model.md` | Roles, RACI, change path and review cadence |
| `data-governance.md` | Contracts, classification, personal data, acceptable use, row and column security |
| `purview-mapping.md` | Planned Microsoft Purview registration and Google Cloud and AWS equivalents |
| `threat-model.md` | STRIDE, OWASP Top 10 for LLM Applications, MITRE ATLAS, residual risks |
| `observability.md` | Signals, thresholds and where they go on each cloud |
| `failure-modes.md` | System-level failure modes, handling and tests |
| `cloud-mapping.md` | Every capability on Azure, Google Cloud and AWS, with status |
| `metrics.md` | Every number, as real command output |
| `deployment.md` | How the gated deployment would work; nothing is deployed |
| `implementation-guide.md` | Adopt this: eight steps and a checklist |
| `adding-a-domain.md` | How mortgage, insurance and healthcare are added on the shared core |
| `interview-guide.md` | Two-minute and ten-minute explanations, expected questions |
| `glossary.md` | Terms used across the docs |
| `components/` | One document per component, 16 sections each |
| `infra/` | One document per infrastructure stack and the workflows |
| `adr/` | Architecture decision records |

## Reading paths

| Reader | Path |
|---|---|
| Recruiter | [../README.md](../README.md) → [interview-guide.md](interview-guide.md) → [metrics.md](metrics.md) |
| Adopter | [implementation-guide.md](implementation-guide.md) → [architecture.md](architecture.md) → [components/README.md](components/README.md) → [adding-a-domain.md](adding-a-domain.md) |
| Security reviewer | [threat-model.md](threat-model.md) → [data-governance.md](data-governance.md) → [components/data-gateway.md](components/data-gateway.md) → [infra/workflows.md](infra/workflows.md) |
| Business reader | [value-case.md](value-case.md) → [mit-article-mapping.md](mit-article-mapping.md) → [ai-business-models.md](ai-business-models.md) → [operating-model.md](operating-model.md) |
