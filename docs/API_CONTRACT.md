# API Contract

## Endpoints
The Flask service now exposes a lightweight public contract at:

- OpenAPI JSON: `http://localhost:5001/openapi.json`
- Swagger UI: `http://localhost:5001/docs/api`

## Covered endpoints
- `GET /health`
- `POST /metrics/ml_quality`
- `POST /login`
- `POST /api/alerts/feedback`
- `POST /api/alerts/incident`
- `GET /api/data`
- `GET /api/users`
- `GET /search`
- `POST /api/upload`
- `GET /admin`

## Why this matters
- External consumers can integrate against a stable machine-readable contract.
- The project now has a defendable API surface instead of only ad-hoc route descriptions.
- Contract drift becomes easier to detect in tests and during review.

## Practical note
- The primary contract is the local OpenAPI JSON at `/openapi.json`.
- The Swagger UI page is a convenience layer for demo and exploration.
- The current UI shell loads Swagger assets from a public CDN, so the JSON contract is the stronger reproducibility artefact for offline or restricted environments.

## Example checks

```bash
curl http://localhost:5001/openapi.json
curl http://localhost:5001/docs/api
curl -X POST http://localhost:5001/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}'
```
