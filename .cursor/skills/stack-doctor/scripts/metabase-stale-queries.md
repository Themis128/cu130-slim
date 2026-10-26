# Fixing stale Metabase questions (role / shared / nodesAccess)

Metabase on the k3s analytics namespace has saved questions that query
the SocialAuto app DB for columns that don't exist:
  SELECT id,email,role FROM "user"   → ERROR column "role" does not exist
  ... shared ... nodesAccess ...

Once kubectl access works (post-#2287), find and fix/delete them via the
Metabase API on analytics.cloudless.gr:

  GET /api/card?f=mine          — list questions
  GET /api/card/{id}            — see the dataset_query SQL
  PUT /api/card/{id}            — fix the SQL (drop the missing column)
  DELETE /api/card/{id}         — or archive dead questions

These show up as `column ... does not exist` spam in social-postgres logs —
harmless but noisy.
