---
name: vector-search-ops
description: Semantic content retrieval + dedup — the Vector Search adopt.
  Embeds posts/journals via DMR into pgvector (or a fallback table) so
  near-dup detection goes semantic and 'similar past content' is
  retrievable. Tool: scripts/vector_search.py — `embed` builds the index,
  `similar "<text>"` finds the closest posts.
---
# Vector search — semantic content dedup + retrieval
