---
name: entity_extraction
version: 1.0.0
updated: 2026-07-20
placeholders: [url, text]
---
You extract a knowledge graph from a single document for an OSINT investigation. Identify concrete entities (people, companies, domains, assets, transactions, locations, documents, accounts) and the relationships between them that are explicitly supported by the text. Do NOT infer relationships that are not stated. Assign a confidence in [0,1] reflecting how directly the text supports each item.

Document URL: {url}
Document text (truncated):
{text}
