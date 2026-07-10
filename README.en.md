# Yuxi Knowledge Q&A Platform

This project is a knowledge question-answering platform for a multi-user front office and a single-admin back office. It keeps Yuxi's knowledge base, agent, dashboard, user role, and department-management capabilities while integrating the operating workflows migrated from the previous business system.

## Business Scope

- Front-office users access knowledge Q&A through the chat portal.
- Back-office administrators manage knowledge bases, models, users, departments, permissions, and portal configuration.
- Knowledge files should be uploaded again in the new system and indexed with the Yuxi data model.
- FAQ, Q&A pair, and agent execution capabilities use Yuxi's existing implementation.

## Quick Start

```bash
cd Yuxi
./scripts/init.sh
docker compose up --build
```

After startup, open the local Web service, create the initial administrator account, and sign in to the back office.

## Directories

| Directory | Description |
| --- | --- |
| `web` | Frontend application |
| `backend` | Backend services |
| `docs` | Local project documentation |
| `scripts` | Initialization, deployment, and maintenance scripts |
| `packages` | CLI and auxiliary packages |

## Workflow

1. Initialize the system and create the super administrator.
2. Configure model providers, parsing services, and knowledge bases in the back office.
3. Create or import users, then configure access by role and department.
4. Upload knowledge files again and wait for parsing and indexing.
5. Validate retrieval and answer quality through the front-office Q&A portal.

## License

See [LICENSE](LICENSE).
