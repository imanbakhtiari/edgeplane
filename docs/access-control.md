# Access control

Edgeplane applies two controls to every authenticated request:

1. **Role capability** controls whether a user may mutate a resource.
2. **Section assignment** controls whether the user may access that UI/API area at all.

Section assignment only narrows access; it never elevates a role.

| Role | Default capability |
| --- | --- |
| `ADMIN` | Full access, including users, credentials, DNS, policies and system settings |
| `OPERATOR` | Routine CDN operations: vhosts, deployments, purges and node operations; no user/system/DNS administration |
| `VIEWER` | Read-only operational visibility; all write endpoints remain denied |

Administrators configure assignments under **Users → Accessible sections**. An empty stored assignment means “use role defaults,” which keeps existing accounts compatible after migration. Direct API requests to an unassigned section return HTTP 403. Browser navigation is generated from the same effective section list returned by `/api/v1/auth/me`.

Apply migration `20260915_section_permissions` before starting the updated Supervisor.
