from __future__ import annotations

from src.flask_app.openapi import build_openapi_spec


def test_build_openapi_spec_includes_core_paths_and_schemas():
    spec = build_openapi_spec(version='1.2.3', server_url='http://localhost:5001')

    assert spec['openapi'] == '3.1.0'
    assert spec['info']['version'] == '1.2.3'
    assert spec['servers'][0]['url'] == 'http://localhost:5001'
    assert '/health' in spec['paths']
    assert '/login' in spec['paths']
    assert '/api/alerts/feedback' in spec['paths']
    assert '/api/alerts/incident' in spec['paths']
    assert 'LoginPayload' in spec['components']['schemas']
    assert 'AlertFeedbackPayload' in spec['components']['schemas']
    assert 'AlertIncidentUpdatePayload' in spec['components']['schemas']
    assert 'bearerAuth' in spec['components']['securitySchemes']
