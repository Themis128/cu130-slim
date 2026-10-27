# Metabase stale queries — n8n 1.x → 2.x schema drift

The omv Metabase dashboards query the `n8n` DB on social-postgres with
SQL written for n8n 1.x. n8n 2.43 dropped those columns:

## Failing → replacement

```sql
-- OLD: SELECT id,email,role FROM "user" ORDER BY email;
-- n8n 2.x renamed "user" columns: role lives on user + role table
SELECT u.id, u.email, r.slug AS role
FROM "user" u LEFT JOIN role r ON r.id = u.role_id
ORDER BY u.email;

-- OLD: SELECT id,name,type,shared::text FROM credentials_entity;
-- n8n 2.x: sharing moved to shared_credentials join table
SELECT c.id, c.name, c.type, sc.role AS shared_role
FROM credentials_entity c
LEFT JOIN shared_credentials sc ON sc.credentials_id = c.id;

-- OLD: SELECT name||' : '||type||' : '||coalesce("nodesAccess",'[]') FROM credentials_entity;
-- nodesAccess is gone — credential access is project-scoped
SELECT c.name || ' : ' || c.type || ' : ' || COALESCE(p.name,'personal')
FROM credentials_entity c
LEFT JOIN shared_credentials sc ON sc.credentials_id = c.id
LEFT JOIN project_relation pr ON pr.resource_id = sc.credentials_id AND pr.resource_type='credential'
LEFT JOIN project p ON p.id = pr.project_id;

-- OLD: SELECT email, is_superuser FROM users;  (probe of n8n's users for admin check)
SELECT email, role IN ('global:admin','global:owner') AS is_superuser FROM "user";
```

## Apply

SSH to omv → Metabase (docker) → update each question, or via Metabase
API PUT /api/card/:id with the replacement dataset_query. Verify each
card renders before saving.
