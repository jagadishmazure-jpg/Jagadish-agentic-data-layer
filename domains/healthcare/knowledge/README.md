# domains/healthcare/knowledge

The healthcare knowledge layer's source documents and the questions used to evaluate retrieval. Synthetic, with no
patient information.

| File | What it does |
|---|---|
| `corpus.yaml` | 24 documents: nursing policies (Morse assessment, bed alarms, rounding, mobility aids, sitters, approval, the assistant's scope, minimum necessary access, equity monitoring, incident reporting), practice guides (delirium, toileting, sedation), region-scoped ward profiles, incident reviews, and one message carrying a prompt injection |
| `eval.yaml` | 24 questions with the documents that answer them, used by `adl healthcare retrieval` |
