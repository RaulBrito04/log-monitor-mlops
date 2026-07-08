from __future__ import annotations

from typing import Any

from src.flask_app.validators import AlertFeedbackPayload, AlertIncidentUpdatePayload, LoginPayload


def _model_schema(model_cls) -> dict[str, Any]:
    return model_cls.model_json_schema(ref_template="#/components/schemas/{model}")


def build_openapi_spec(*, version: str = "1.0.0", server_url: str = "http://localhost:5001") -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Log Monitor MLOps API",
            "version": version,
            "description": (
                "Operational API for the Log Monitor MLOps project. "
                "This contract documents the currently supported public and operator endpoints."
            ),
        },
        "servers": [{"url": server_url}],
        "tags": [
            {"name": "health", "description": "Liveness and operational metrics endpoints."},
            {"name": "auth", "description": "Demo authentication endpoints used by the operator workflow."},
            {"name": "alerts", "description": "Alert-review and incident-workflow endpoints."},
            {"name": "demo", "description": "Sample/demo endpoints kept for the project surface."},
        ],
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "Opaque demo token",
                }
            },
            "schemas": {
                "LoginPayload": _model_schema(LoginPayload),
                "AlertFeedbackPayload": _model_schema(AlertFeedbackPayload),
                "AlertIncidentUpdatePayload": _model_schema(AlertIncidentUpdatePayload),
                "MLQualityPayload": {
                    "type": "object",
                    "required": ["ml_f1_score"],
                    "properties": {
                        "ml_f1_score": {"type": "number"},
                        "model": {"type": "string", "default": "hybrid_ensemble"},
                        "dataset": {"type": "string", "default": "holdout"},
                    },
                },
                "ErrorResponse": {
                    "type": "object",
                    "properties": {
                        "error": {"type": "string"},
                        "message": {"type": "string"},
                        "path": {"type": "string"},
                        "method": {"type": "string"},
                    },
                },
                "HealthResponse": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "example": "healthy"},
                        "version": {"type": "string", "example": version},
                    },
                },
            },
        },
        "paths": {
            "/health": {
                "get": {
                    "tags": ["health"],
                    "summary": "Health check",
                    "responses": {
                        "200": {
                            "description": "Service is healthy.",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/HealthResponse"}}},
                        }
                    },
                }
            },
            "/metrics/ml_quality": {
                "post": {
                    "tags": ["health"],
                    "summary": "Publish operational ML quality metrics",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/MLQualityPayload"}}},
                    },
                    "responses": {
                        "200": {"description": "Metric snapshot updated."},
                        "400": {"description": "Invalid payload.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                    },
                }
            },
            "/login": {
                "post": {
                    "tags": ["auth"],
                    "summary": "Demo login",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/LoginPayload"}}},
                    },
                    "responses": {
                        "200": {"description": "Login succeeded and a demo bearer token was issued."},
                        "400": {"description": "Request body validation failed.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                        "401": {"description": "Invalid credentials.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                    },
                }
            },
            "/api/alerts/feedback": {
                "post": {
                    "tags": ["alerts"],
                    "summary": "Persist analyst feedback for an alert",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AlertFeedbackPayload"}}},
                    },
                    "responses": {
                        "201": {"description": "Feedback persisted."},
                        "400": {"description": "Validation failed.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                        "404": {"description": "Alert not found.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                        "500": {"description": "Database error.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                    },
                }
            },
            "/api/alerts/incident": {
                "post": {
                    "tags": ["alerts"],
                    "summary": "Update alert incident lifecycle state",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AlertIncidentUpdatePayload"}}},
                    },
                    "responses": {
                        "200": {"description": "Incident state updated."},
                        "400": {"description": "Validation or transition failure.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                        "404": {"description": "Alert not found.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                        "500": {"description": "Database error.", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
                    },
                }
            },
            "/api/data": {
                "get": {
                    "tags": ["demo"],
                    "summary": "Read sample data with pagination",
                    "parameters": [
                        {"name": "page", "in": "query", "schema": {"type": "integer", "minimum": 1, "default": 1}},
                        {"name": "per_page", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10}},
                    ],
                    "responses": {"200": {"description": "Sample data page."}, "400": {"description": "Invalid query params."}},
                }
            },
            "/api/users": {
                "get": {
                    "tags": ["demo"],
                    "summary": "Read sample users",
                    "responses": {"200": {"description": "Sample users."}},
                }
            },
            "/search": {
                "get": {
                    "tags": ["demo"],
                    "summary": "Search sample data",
                    "parameters": [
                        {"name": "q", "in": "query", "required": True, "schema": {"type": "string", "minLength": 1, "maxLength": 128}},
                    ],
                    "responses": {"200": {"description": "Search results."}, "400": {"description": "Invalid query string."}},
                }
            },
            "/api/upload": {
                "post": {
                    "tags": ["demo"],
                    "summary": "Upload a supported file",
                    "requestBody": {
                        "required": False,
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"file": {"type": "string", "format": "binary"}},
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "Upload received."}, "400": {"description": "Invalid upload metadata."}, "413": {"description": "Payload too large."}},
                }
            },
            "/admin": {
                "get": {
                    "tags": ["auth"],
                    "summary": "Demo admin panel",
                    "security": [{"bearerAuth": []}],
                    "responses": {"200": {"description": "Admin information returned."}, "401": {"description": "Bearer token required."}, "403": {"description": "Invalid or insufficient token."}},
                }
            },
        },
    }


def build_swagger_ui_html(*, spec_path: str = "/openapi.json") -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Log Monitor MLOps API Docs</title>
    <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css" />
    <style>
      body {{ margin: 0; background: #f5f5f5; }}
      .topbar {{ display: none; }}
    </style>
  </head>
  <body>
    <div id="swagger-ui"></div>
    <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
    <script>
      window.onload = () => {{
        window.ui = SwaggerUIBundle({{
          url: '{spec_path}',
          dom_id: '#swagger-ui',
          deepLinking: true,
          displayRequestDuration: true,
        }});
      }};
    </script>
  </body>
</html>
"""
