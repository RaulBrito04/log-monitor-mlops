from __future__ import annotations

import pickle
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import src.dashboard.auth as auth
import src.dashboard.data as dashboard_data
import src.dashboard.ui as dashboard_ui
import src.dashboard.pages_impl as pages_impl
import src.ml.reference_explainer as reference_explainer
import src.ml.seed_reviewed_feedback as reviewed_seed
import src.ml.seed_validation_feedback as validation_seed


class DummyModel:
    classes_ = np.array([0, 1])

    def predict_proba(self, frame):
        values = np.asarray(frame)
        score = np.clip(values[:, 0] / 10.0, 0.0, 1.0)
        return np.column_stack([1.0 - score, score])


def test_dashboard_http_and_prometheus_helpers(monkeypatch):
    class Response:
        status_code = 200

        def json(self):
            return {"status": "success", "data": {"result": []}}

        def raise_for_status(self):
            return None

    monkeypatch.setattr(dashboard_data, "get_config", lambda: SimpleNamespace(
        flask_api_url="http://flask", prometheus_url="http://prom"
    ))
    calls = []

    def post(url, **kwargs):
        calls.append(("post", url, kwargs))
        return Response()

    def get(url, **kwargs):
        calls.append(("get", url, kwargs))
        return Response()

    monkeypatch.setattr(dashboard_data.requests, "post", post)
    monkeypatch.setattr(dashboard_data.requests, "get", get)
    assert dashboard_data._flask_post("/x", {"a": 1})["status"] == "success"
    assert dashboard_data._prometheus_get("/api", {"q": "x"})["status"] == "success"
    assert calls[0][0] == "post"


def test_dashboard_http_error_payloads(monkeypatch):
    class Response:
        status_code = 422

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError("http failure")

    monkeypatch.setattr(dashboard_data, "get_config", lambda: SimpleNamespace(
        flask_api_url="http://flask", prometheus_url="http://prom"
    ))
    monkeypatch.setattr(dashboard_data.requests, "post", lambda *a, **k: Response({"message": "bad"}))
    with pytest.raises(ValueError, match="bad"):
        dashboard_data._flask_post("/x", {})
    monkeypatch.setattr(dashboard_data.requests, "post", lambda *a, **k: Response({"error": "failed"}))
    with pytest.raises(ValueError, match="failed"):
        dashboard_data._flask_post("/x", {})
    monkeypatch.setattr(dashboard_data.requests, "post", lambda *a, **k: Response({}))
    with pytest.raises(ValueError, match="HTTP 422"):
        dashboard_data._flask_post("/x", {})
    class PromResponse(Response):
        status_code = 200
    monkeypatch.setattr(dashboard_data.requests, "get", lambda *a, **k: PromResponse({"status": "error"}))
    with pytest.raises(ValueError, match="Prometheus query failed"):
        dashboard_data._prometheus_get("/api")


def test_prometheus_parsers_and_targets(monkeypatch):
    assert dashboard_data._parse_vector_value({}) is None
    assert dashboard_data._parse_vector_value({"data": {"result": []}}) is None
    assert dashboard_data._parse_vector_value({"data": {"result": [{"value": [1, "0.75"]}]}}) == 0.75
    frame = dashboard_data._range_to_frame({
        "data": {"result": [
            {"metric": {"job": "api"}, "values": [[1, "0.4"]]},
            {"metric": {"model": "rf"}, "values": [[2, "0.8"]]},
            {"metric": {}, "values": []},
        ]}
    }, "value")
    assert list(frame["series"]) == ["api", "rf"]
    monkeypatch.setattr(dashboard_data, "_prometheus_get", lambda *a, **k: {
        "data": {"activeTargets": [
            {"labels": {"job": "api", "instance": "one"}, "health": "up", "lastError": "", "scrapeUrl": "u"},
            {"labels": {}, "health": "down"},
        ]}
    })
    targets = dashboard_data._fetch_targets()
    assert targets[0]["job"] == "api"
    assert targets[1]["instance"] == "unknown"


def test_overview_and_alert_options(monkeypatch):
    one_values = [{"count": 9}, {"timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc)}]
    rows_values = [
        [{"severity": "high", "count": 2}],
        [{"bucket": "a", "count": 3}],
        [{"bucket": "b", "count": 4}],
    ]

    def one(*args, **kwargs):
        return one_values.pop(0)

    def rows(*args, **kwargs):
        return rows_values.pop(0)

    monkeypatch.setattr(dashboard_data, "_one", one)
    monkeypatch.setattr(dashboard_data, "_rows", rows)
    prom_values = iter([
        {"data": {"result": [{"value": [1, "0.91"]}]}},
        {"data": {"result": [{"value": [1, "4"]}]}},
    ])
    monkeypatch.setattr(dashboard_data, "_prometheus_get", lambda *a, **k: next(prom_values))
    monkeypatch.setattr(dashboard_data, "_fetch_targets", lambda: [{"job": "api"}])
    snapshot = dashboard_data._fetch_overview_snapshot()
    assert snapshot["total_alerts"] == 9
    assert snapshot["latest_f1"] == 0.91
    assert snapshot["targets"] == [{"job": "api"}]

    values = iter([
        [{"alert_type": "a"}],
        [{"source": "s"}],
        [{"severity": "high"}],
        [{"incident_status": "NEW"}],
    ])
    monkeypatch.setattr(dashboard_data, "_rows", lambda *a, **k: next(values))
    assert dashboard_data._fetch_alert_options() == {
        "types": ["a"], "sources": ["s"], "severities": ["high"], "statuses": ["NEW"]
    }


def test_dashboard_query_builders_cover_filters(monkeypatch):
    captured = []

    def rows(sql, params=()):
        captured.append((sql, tuple(params)))
        return [{"ok": True}]

    monkeypatch.setattr(dashboard_data, "_rows", rows)
    assert dashboard_data._fetch_alerts("high", "sql", "sensor", "OPEN", "10.0", 6, 12)
    sql, params = captured[-1]
    assert "AND severity = %s" in sql
    assert "AND alert_type = %s" in sql
    assert "AND source = %s" in sql
    assert "AND COALESCE(incident_status" in sql
    assert "ILIKE %s" in sql
    assert params == (6, "high", "sql", "sensor", "OPEN", "%10.0%", 12)

    assert dashboard_data._fetch_logs(6, "10.", "/api", "POST", 500, "needle", 7)
    sql, params = captured[-1]
    assert "endpoint ILIKE %s" in sql
    assert "CAST(data AS TEXT)" in sql
    assert params == (6, "%10.%", "%/api%", "POST", 500, "%needle%", "%needle%", 7)
    assert dashboard_data._fetch_logs_for_ids((), 2) == []
    assert dashboard_data._fetch_logs_for_ids((3, 4), 2)
    assert captured[-1][1] == ([3, 4], 2)


def test_dashboard_data_queries_and_submissions(monkeypatch):
    captured = []
    monkeypatch.setattr(dashboard_data, "_rows", lambda sql, params=(): captured.append((sql, tuple(params))) or [{"id": 1}])
    assert dashboard_data._fetch_alert_detail(2)["id"] == 1
    assert dashboard_data._fetch_feedback_history(2, 3)
    assert dashboard_data._fetch_incident_history(2, 4)
    assert dashboard_data._fetch_prediction_split()
    assert dashboard_data._fetch_alert_trend(8)
    assert dashboard_data._fetch_log_context(5)
    assert len(captured) == 7
    monkeypatch.setattr(dashboard_data, "_one", lambda *a, **k: None)
    assert dashboard_data._fetch_log_context_window(5) == []
    monkeypatch.setattr(dashboard_data, "_one", lambda *a, **k: {"timestamp": datetime.now(timezone.utc)})
    monkeypatch.setattr(dashboard_data, "_rows", lambda *a, **k: [{"id": 5}])
    assert dashboard_data._fetch_log_context_window(5, 10) == [{"id": 5}]

    posted = []
    monkeypatch.setattr(dashboard_data, "_flask_post", lambda path, payload: posted.append((path, payload)) or {"status": "ok"})
    monkeypatch.setattr(dashboard_data, "clear_dashboard_caches", lambda: posted.append(("clear", {})))
    assert dashboard_data.submit_alert_feedback(7, "true_positive", "reason", "u")["status"] == "ok"
    assert dashboard_data.submit_alert_incident(7, "OPEN", "alice", "note", "u")["status"] == "ok"
    assert posted[0][0] == "/api/alerts/feedback"
    assert posted[2][0] == "/api/alerts/incident"


def test_dashboard_prometheus_history_and_explanation_errors(monkeypatch):
    monkeypatch.setattr(dashboard_data, "_prometheus_get", lambda *a, **k: {
        "data": {"result": [{"metric": {"prediction": "anomaly"}, "values": [[1, "0.2"]]}]}
    })
    frame = dashboard_data._fetch_ml_f1_history(1, "1m")
    assert frame.iloc[0]["f1_score"] == 0.2

    monkeypatch.setattr(dashboard_data, "_fetch_alert_detail", lambda _id: None)
    assert dashboard_data._fetch_alert_explanation(1)["status"] == "unavailable"
    assert dashboard_data._fetch_alert_counterfactual(1)["status"] == "unavailable"

    monkeypatch.setattr(dashboard_data, "_fetch_alert_detail", lambda _id: {"log_ids": [10, 11]})
    assert "not attached" in dashboard_data._fetch_alert_explanation(1, 99)["message"]
    assert "not attached" in dashboard_data._fetch_alert_counterfactual(1, 99)["message"]

    monkeypatch.setattr(dashboard_data, "_fetch_log_context_window", lambda _id: [])
    class LimeFailure:
        def explain_log(self, *args, **kwargs):
            raise dashboard_data.LimeUnavailableError("lime unavailable")
    class CfFailure:
        def explain_log(self, *args, **kwargs):
            raise LookupError("cf unavailable")
    monkeypatch.setattr(dashboard_data, "get_lime_explainer", lambda: LimeFailure())
    monkeypatch.setattr(dashboard_data, "get_counterfactual_explainer", lambda: CfFailure())
    assert dashboard_data._fetch_alert_explanation(1, 10)["status"] == "unavailable"
    assert dashboard_data._fetch_alert_counterfactual(1, 10)["status"] == "unavailable"


def test_auth_and_ui_helpers(monkeypatch):
    state = {}
    fake_st = SimpleNamespace(session_state=state)
    monkeypatch.setattr(auth, "st", fake_st)
    monkeypatch.setattr(auth, "get_config", lambda: SimpleNamespace(
        dashboard_username="alice", dashboard_password="secret"
    ))
    assert not auth.is_authenticated()
    assert auth.current_user() is None
    assert auth.attempt_login("alice", "secret")
    assert auth.is_authenticated()
    assert auth.current_user() == "alice"
    auth.logout()
    assert not auth.is_authenticated()
    assert not auth.attempt_login("alice", "wrong")
    assert auth.current_user() is None

    assert dashboard_ui.format_timestamp(None) == "-"
    assert dashboard_ui.format_timestamp("") == "-"
    dt = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert dashboard_ui.format_timestamp(dt).endswith("UTC")
    assert dashboard_ui.format_timestamp("raw") == "raw"
    assert dashboard_ui.humanize_seconds(None) == "-"
    assert dashboard_ui.humanize_seconds(12) == "12s"
    assert dashboard_ui.humanize_seconds(120) == "2.0m"
    assert dashboard_ui.humanize_seconds(7200) == "2.0h"
    assert dashboard_ui.humanize_seconds(172800) == "2.0d"


def test_reviewed_seed_sampling_and_building():
    frame = pd.DataFrame({"alert_id": range(5), "scenarios": [["sql"], ["scan"], ["brute"], ["rate"], ["off"]]})
    assert reviewed_seed.sample_evenly(pd.DataFrame(), 2).empty
    assert len(reviewed_seed.sample_evenly(frame, None)) == 5
    assert reviewed_seed.sample_evenly(frame, 0).empty
    sampled = reviewed_seed.sample_evenly(frame, 3)
    assert len(sampled) == 3
    positive = pd.DataFrame([
        {"alert_id": 1, "scenarios": ["sql_injection", "scanning"]},
        {"alert_id": 2, "scenarios": "brute_force"},
    ])
    negative = pd.DataFrame([{"alert_id": 9}, {"alert_id": 10}, {"alert_id": 11}])
    rows, stats = reviewed_seed.build_seed_rows(
        reviewed_seed.SeedConfig(negative_ratio=1.0, max_positive_alerts=1, max_negative_alerts=1, user_id="tester"),
        positive,
        negative,
    )
    assert len(rows) == 2
    assert stats["selected_true_positive_alerts"] == 1
    assert set(rows["label"]) == {"true_positive", "false_positive"}


def test_seed_persistence_and_summary(tmp_path, monkeypatch):
    class Cursor:
        rowcount = 1
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, sql, params):
            self.last = params

    class Conn:
        def cursor(self):
            return Cursor()
        def commit(self):
            self.committed = True

    conn = Conn()
    empty = pd.DataFrame()
    assert reviewed_seed.persist_seed_feedback(conn, empty) == 0
    rows = pd.DataFrame([{"alert_id": 1, "user_id": "u", "label": "true_positive", "reason": "r"}])
    assert reviewed_seed.persist_seed_feedback(conn, rows) == 1
    path = tmp_path / "summary.json"
    monkeypatch.setattr(reviewed_seed, "SUMMARY_PATH", path)
    reviewed_seed.write_summary({"ok": True})
    assert path.exists()


def test_validation_seed_parsing_and_rows(tmp_path):
    tick = chr(96)
    path = tmp_path / "validation.md"
    path.write_text(
        f"Validation started at: {tick}2026-01-01T10:00:00Z{tick}\n"
        f"Benign source IP used: {tick}10.0.0.1{tick}\n"
        f"Attack source IP used: {tick}10.0.0.2/32{tick}\n",
        encoding="utf-8",
    )
    context = validation_seed.parse_validation_results(path)
    assert context.attack_source_ip == "10.0.0.2/32"
    assert validation_seed.normalize_ip_text("10.0.0.1") == "10.0.0.1/32"
    assert validation_seed.normalize_ip_text("10.0.0.1/24") == "10.0.0.1/24"
    bad_path = tmp_path / "bad.md"
    bad_path.write_text("invalid", encoding="utf-8")
    with pytest.raises(ValueError):
        validation_seed.parse_validation_results(bad_path)

    attack = pd.DataFrame([
        {"alert_id": 1, "existing_feedback": 0},
        {"alert_id": 2, "existing_feedback": 1},
    ])
    benign = pd.DataFrame([{"alert_id": 3, "existing_feedback": 0}])
    rows = validation_seed.build_feedback_rows(attack, benign, "u")
    assert list(rows["alert_id"]) == [1, 3]
    assert set(rows["label"]) == {"true_positive", "false_positive"}
    assert validation_seed.build_feedback_rows(pd.DataFrame(), pd.DataFrame(), "u").empty


def test_validation_persistence_and_summary(tmp_path):
    class Cursor:
        rowcount = 1
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, sql, params):
            self.last = params

    class Conn:
        def cursor(self):
            return Cursor()
        def commit(self):
            self.committed = True

    conn = Conn()
    assert validation_seed.persist_feedback_rows(conn, pd.DataFrame()) == 0
    rows = pd.DataFrame([{"alert_id": 2, "user_id": "u", "label": "false_positive", "reason": "r"}])
    assert validation_seed.persist_feedback_rows(conn, rows) == 1
    path = tmp_path / "summary.json"
    validation_seed.write_summary(path, {"ok": True})
    assert path.exists()


def test_reference_explainer_guards_and_helpers(tmp_path):
    allowed = tmp_path / "models"
    allowed.mkdir()
    model_path = allowed / "model.pkl"
    with model_path.open("wb") as handle:
        pickle.dump(DummyModel(), handle)
    features_path = tmp_path / "features.txt"
    features_path.write_text("f1\nf2\n", encoding="utf-8")
    dataset_path = tmp_path / "dataset.csv"
    pd.DataFrame([
        {"log_id": 1, "f1": 8, "f2": 2, "label": 1, "timestamp": "2026-01-01T00:00:00Z"},
        {"log_id": 2, "f1": 2, "f2": None, "label": 0, "timestamp": "2026-01-01T00:00:00Z"},
    ]).to_csv(dataset_path, index=False)

    with pytest.raises(ValueError, match="outside"):
        reference_explainer._safe_load_pickle(tmp_path / "outside.pkl", allowed)
    with pytest.raises(ValueError, match="extension"):
        reference_explainer._safe_load_pickle(allowed / "model.txt", allowed)
    with pytest.raises(FileNotFoundError):
        reference_explainer._safe_load_pickle(allowed / "missing.pkl", allowed)

    explainer = reference_explainer.RandomForestReferenceExplainerBase(
        model_path=model_path,
        features_path=features_path,
        dataset_paths=[dataset_path],
        allowed_model_dir=allowed,
    )
    assert explainer.feature_names == ["f1", "f2"]
    assert explainer.reference_label_column == "label"
    assert explainer._predicted_label(0.5) == "anomaly"
    assert explainer._predicted_label(0.2) == "normal"
    assert explainer._row_from_reference_dataset(1) is not None
    assert explainer._row_from_reference_dataset(999) is None
    row, source = explainer._resolve_feature_row(1)
    assert source == "dataset"
    assert explainer._predict_anomaly_probability(row) > 0.5
    candidates, source = explainer._reference_candidate_rows(1)
    assert not candidates.empty
    assert source in {"actual_label_and_model", "actual_label"}
    assert explainer._resolve_label_column(pd.DataFrame({"x": [1]})) is None


def test_reference_explainer_dataset_errors(tmp_path):
    allowed = tmp_path / "models"
    allowed.mkdir()
    model_path = allowed / "model.pkl"
    with model_path.open("wb") as handle:
        pickle.dump(DummyModel(), handle)
    features = tmp_path / "features.txt"
    features.write_text("f1\n", encoding="utf-8")
    bad = tmp_path / "bad.txt"
    bad.write_text("x", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        reference_explainer.RandomForestReferenceExplainerBase(
            model_path=model_path, features_path=features, dataset_paths=[bad], allowed_model_dir=allowed
        )
    empty_features = tmp_path / "empty.txt"
    empty_features.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        reference_explainer.RandomForestReferenceExplainerBase(
            model_path=model_path, features_path=empty_features, dataset_paths=[], allowed_model_dir=allowed
        )


def test_seed_run_parse_and_main_paths(tmp_path, monkeypatch, capsys):
    positive = pd.DataFrame([{"alert_id": 1, "scenarios": ["sql_injection"]}])
    negative = pd.DataFrame([{"alert_id": 2}])

    class Conn:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    monkeypatch.setattr(reviewed_seed, "db_connect", lambda: Conn())
    monkeypatch.setattr(reviewed_seed, "load_positive_candidates", lambda conn: positive)
    monkeypatch.setattr(reviewed_seed, "load_negative_candidates", lambda conn: negative)
    monkeypatch.setattr(reviewed_seed, "persist_seed_feedback", lambda conn, rows: len(rows))
    summary_path = tmp_path / "reviewed.json"
    monkeypatch.setattr(reviewed_seed, "SUMMARY_PATH", summary_path)
    summary = reviewed_seed.run_seed(reviewed_seed.SeedConfig(dry_run=False))
    assert summary["inserted_feedback_rows"] == 2
    assert summary_path.exists()

    monkeypatch.setattr(reviewed_seed, "persist_seed_feedback", lambda conn, rows: 99)
    dry_summary = reviewed_seed.run_seed(reviewed_seed.SeedConfig(dry_run=True))
    assert dry_summary["inserted_feedback_rows"] == 0

    monkeypatch.setattr(reviewed_seed, "parse_args", lambda: reviewed_seed.SeedConfig(
        negative_ratio=1.0, max_positive_alerts=1, max_negative_alerts=1, user_id="main"
    ))
    monkeypatch.setattr(reviewed_seed, "run_seed", lambda config: {
        "positive_candidates": 1, "negative_candidates": 1,
        "selected_true_positive_alerts": 1, "selected_false_positive_alerts": 1,
        "seed_rows": 2, "inserted_feedback_rows": 2,
    })
    reviewed_seed.main()
    assert "REVIEWED FEEDBACK BOOTSTRAP" in capsys.readouterr().out


def test_validation_seed_run_parse_and_main_paths(tmp_path, monkeypatch, capsys):
    tick = chr(96)
    results_path = tmp_path / "validation.md"
    results_path.write_text(
        f"Validation started at: {tick}2026-01-01T10:00:00Z{tick}\n"
        f"Benign source IP used: {tick}10.0.0.1{tick}\n"
        f"Attack source IP used: {tick}10.0.0.2{tick}\n",
        encoding="utf-8",
    )
    attack = pd.DataFrame([{"alert_id": 1, "existing_feedback": 0}])
    benign = pd.DataFrame([{"alert_id": 2, "existing_feedback": 0}])

    class Conn:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    monkeypatch.setattr(validation_seed, "db_connect", lambda: Conn())
    monkeypatch.setattr(validation_seed, "load_alert_candidates", lambda conn, ip, lower: attack if "2" in ip else benign)
    monkeypatch.setattr(validation_seed, "persist_feedback_rows", lambda conn, rows: len(rows))
    summary_path = tmp_path / "validation.json"
    summary = validation_seed.run_seed(validation_seed.ValidationSeedConfig(
        results_path=str(results_path), summary_path=str(summary_path), dry_run=False
    ))
    assert summary["seed_rows"] == 2
    assert summary["inserted_feedback_rows"] == 2
    assert summary_path.exists()

    dry_summary = validation_seed.run_seed(validation_seed.ValidationSeedConfig(
        results_path=str(results_path), summary_path=str(summary_path), dry_run=True
    ))
    assert dry_summary["inserted_feedback_rows"] == 0

    monkeypatch.setattr(validation_seed, "parse_args", lambda: validation_seed.ValidationSeedConfig(
        results_path=str(results_path), summary_path=str(summary_path)
    ))
    monkeypatch.setattr(validation_seed, "run_seed", lambda config: {
        "attack_alert_candidates": 1, "benign_alert_candidates": 1,
        "attack_candidates_without_feedback": 1, "benign_candidates_without_feedback": 1,
        "seed_rows": 2, "inserted_feedback_rows": 2,
    })
    validation_seed.main()
    assert "VALIDATION FEEDBACK BOOTSTRAP" in capsys.readouterr().out


def test_seed_candidate_queries_and_db_config(monkeypatch):
    seen = []

    def read_sql(sql, conn, **kwargs):
        seen.append((sql, kwargs))
        return pd.DataFrame()

    monkeypatch.setattr(reviewed_seed.pd, "read_sql", read_sql)
    assert reviewed_seed.load_positive_candidates(object()).empty
    assert reviewed_seed.load_negative_candidates(object()).empty
    monkeypatch.setattr(validation_seed.pd, "read_sql", read_sql)
    assert validation_seed.load_alert_candidates(object(), "10.0.0.1", datetime.now(timezone.utc)).empty
    assert len(seen) == 3
    assert "params" in seen[-1][1]


def test_auth_render_and_require_auth(monkeypatch):
    class Form:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    class FakeSt:
        def __init__(self, username, password, submitted):
            self.session_state = {}
            self.username = username
            self.password = password
            self.submitted = submitted
            self.events = []
        def title(self, value):
            self.events.append(("title", value))
        def caption(self, value):
            self.events.append(("caption", value))
        def form(self, name):
            return Form()
        def text_input(self, label, **kwargs):
            return self.username if label == "Username" else self.password
        def form_submit_button(self, *args, **kwargs):
            return self.submitted
        def success(self, value):
            self.events.append(("success", value))
        def error(self, value):
            self.events.append(("error", value))
        def rerun(self):
            self.events.append(("rerun", True))
        def stop(self):
            raise RuntimeError("stopped")

    monkeypatch.setattr(auth, "get_config", lambda: SimpleNamespace(
        dashboard_username="alice", dashboard_password="secret"
    ))
    fake = FakeSt(" alice ", "wrong", True)
    monkeypatch.setattr(auth, "st", fake)
    auth.render_login_gate()
    assert any(event[0] == "error" for event in fake.events)
    fake = FakeSt(" alice ", "secret", True)
    monkeypatch.setattr(auth, "st", fake)
    auth.render_login_gate()
    assert any(event[0] == "success" for event in fake.events)
    fake = FakeSt("", "", False)
    monkeypatch.setattr(auth, "st", fake)
    with pytest.raises(RuntimeError, match="stopped"):
        auth.require_auth()


def test_dashboard_ui_render_helpers(monkeypatch):
    class Sidebar:
        def title(self, *args):
            pass
        def caption(self, *args):
            pass
        def write(self, *args):
            pass
        def button(self, *args, **kwargs):
            return False

    class FakeSt:
        sidebar = Sidebar()
        def set_page_config(self, **kwargs):
            self.page = kwargs
        def markdown(self, *args, **kwargs):
            pass
        def error(self, message):
            self.error_message = message
        def info(self, message):
            self.info_message = message

    fake = FakeSt()
    monkeypatch.setattr(dashboard_ui, "st", fake)
    monkeypatch.setattr(dashboard_ui, "get_config", lambda: SimpleNamespace(refresh_seconds=15))
    monkeypatch.setattr(dashboard_ui, "current_user", lambda: "alice")
    dashboard_ui.configure_page("Title", "icon")
    dashboard_ui.render_sidebar("Alerts", False)
    dashboard_ui.render_error("bad")
    dashboard_ui.render_empty("none")
    assert fake.page["page_title"] == "Title"
    assert fake.error_message == "bad"
    assert fake.info_message == "none"


def test_reference_explainer_extra_branches(tmp_path):
    allowed = tmp_path / "models"
    allowed.mkdir()
    model_path = allowed / "model.pkl"
    with model_path.open("wb") as handle:
        pickle.dump(DummyModel(), handle)
    features = tmp_path / "features.txt"
    features.write_text("f1\nf2\n", encoding="utf-8")
    explainer = reference_explainer.RandomForestReferenceExplainerBase(
        model_path=model_path,
        features_path=features,
        dataset_paths=[tmp_path / "missing.csv"],
        allowed_model_dir=allowed,
    ) if False else None

    base = object.__new__(reference_explainer.RandomForestReferenceExplainerBase)
    base.feature_names = ["f1", "f2"]
    base.feature_medians = {"f1": 1.0, "f2": 2.0}
    base.model = DummyModel()
    frame = pd.DataFrame({"f1": [np.nan, 4], "extra": [1, 2]})
    prepared = base._prepare_feature_frame(frame)
    assert list(prepared.columns) == ["f1", "f2"]
    assert base._predict_proba(np.array([[5, np.nan]])).shape == (1, 2)
    base.reference_frame = pd.DataFrame({"f1": [1], "f2": [2]})
    base.reference_features = pd.DataFrame({"f1": [1], "f2": [2]})
    base.reference_label_column = None
    candidates, source = base._reference_candidate_rows(0)
    assert not candidates.empty
    assert source == "model_prediction"
    with pytest.raises(LookupError):
        base._reference_candidate_rows(1)
    base._row_from_live_context = lambda log_id, context: None
    with pytest.raises(LookupError):
        base._resolve_feature_row(7, pd.DataFrame({"id": [1]}))


def test_rule_engine_runtime_paths(monkeypatch):
    from unittest.mock import MagicMock
    from src.detection import rule_engine

    monkeypatch.setattr(rule_engine, "observe_pipeline_stage", lambda *args, **kwargs: None)
    persisted = []
    monkeypatch.setattr(rule_engine, "persist_component_runtime_metrics", lambda *args, **kwargs: persisted.append(args))
    summary = {
        "alerts_created": 2,
        "rules_executed": 6,
        "error_count": 0,
        "rules_duration_seconds": 0.1,
        "per_rule": [],
    }
    rule_engine._finalize_cycle(
        summary,
        0.2,
        configured_window="60 seconds",
        configured_interval_seconds=1,
        sleep_duration=0.3,
    )
    assert persisted

    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    monkeypatch.setattr(rule_engine, "connect", lambda: conn)
    monkeypatch.setattr(rule_engine, "assert_rule_engine_schema", lambda conn: None)
    monkeypatch.setattr(rule_engine, "run_once", lambda *args: summary)
    monkeypatch.setattr(rule_engine.time, "sleep", lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt()))
    rule_engine.mode_realtime(1, "60 seconds")
    cursor.close.assert_called_once()
    conn.close.assert_called_once()


def test_rule_engine_environment_fallbacks(monkeypatch):
    from src.detection import rule_engine

    monkeypatch.delenv("RULE_ENGINE_INTERVAL_SEC", raising=False)
    monkeypatch.delenv("RULE_ENGINE_WINDOW", raising=False)
    assert rule_engine._env_int("RULE_ENGINE_INTERVAL_SEC", 60) == 60
    assert rule_engine._env_window() == "60 seconds"
    monkeypatch.setenv("RULE_ENGINE_INTERVAL_SEC", "bad")
    monkeypatch.setenv("RULE_ENGINE_WINDOW", " 5 minutes ")
    assert rule_engine._env_int("RULE_ENGINE_INTERVAL_SEC", 60) == 60
    assert rule_engine._env_window() == "5 minutes"


def test_dashboard_public_cached_wrappers(monkeypatch):
    d = dashboard_data
    monkeypatch.setattr(d, "_fetch_overview_snapshot", lambda: {"overview": True})
    monkeypatch.setattr(d, "_fetch_alert_options", lambda: {"types": []})
    monkeypatch.setattr(d, "_fetch_alerts", lambda *args: [{"alert": True}])
    monkeypatch.setattr(d, "_fetch_alert_detail", lambda *args: {"detail": True})
    monkeypatch.setattr(d, "_fetch_feedback_history", lambda *args: [{"feedback": True}])
    monkeypatch.setattr(d, "_fetch_incident_history", lambda *args: [{"incident": True}])
    monkeypatch.setattr(d, "_fetch_logs_for_ids", lambda *args: [{"log": True}])
    monkeypatch.setattr(d, "_fetch_logs", lambda *args: [{"logs": True}])
    monkeypatch.setattr(d, "_fetch_log_context", lambda *args: {"context": True})
    monkeypatch.setattr(d, "_fetch_prediction_split", lambda: [{"prediction": True}])
    monkeypatch.setattr(d, "_fetch_alert_trend", lambda *args: [{"trend": True}])
    monkeypatch.setattr(d, "_fetch_ml_f1_history", lambda *args: pd.DataFrame({"f1_score": [1.0]}))
    monkeypatch.setattr(d, "_fetch_alert_explanation", lambda *args: {"explanation": True})
    monkeypatch.setattr(d, "_fetch_alert_counterfactual", lambda *args: {"counterfactual": True})
    monkeypatch.setattr(d, "LimeAlertExplainer", lambda: "lime")
    monkeypatch.setattr(d, "CounterfactualAlertExplainer", lambda: "counterfactual")
    assert d.fetch_overview_snapshot()["overview"]
    assert d.fetch_alert_options()["types"] == []
    assert d.fetch_alerts()[0]["alert"]
    assert d.fetch_alert_detail(1)["detail"]
    assert d.fetch_feedback_history(1)[0]["feedback"]
    assert d.fetch_incident_history(1)[0]["incident"]
    assert d.fetch_logs_for_ids((1,))[0]["log"]
    assert d.fetch_logs()[0]["logs"]
    assert d.fetch_log_context(1)["context"]
    assert d.fetch_prediction_split()[0]["prediction"]
    assert d.fetch_alert_trend()[0]["trend"]
    assert d.fetch_ml_f1_history().iloc[0]["f1_score"] == 1.0
    assert d.fetch_alert_explanation(1)["explanation"]
    assert d.fetch_alert_counterfactual(1)["counterfactual"]
    assert d.get_lime_explainer() == "lime"
    assert d.get_counterfactual_explainer() == "counterfactual"
    d.clear_dashboard_caches()


def test_dashboard_connection_and_rows(monkeypatch):
    class Cursor:
        def __init__(self):
            self.executed = None
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, sql, params):
            self.executed = (sql, params)
        def fetchall(self):
            return [{"id": 1}]

    class Connection:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def cursor(self, cursor_factory=None):
            return Cursor()

    monkeypatch.setattr(dashboard_data, "get_config", lambda: SimpleNamespace(
        postgres_host="h", postgres_port=5432, postgres_db="d",
        postgres_user="u", postgres_password="p"
    ))
    monkeypatch.setattr(dashboard_data.psycopg2, "connect", lambda **kwargs: Connection())
    assert dashboard_data._db_connection()
    assert dashboard_data._rows("SELECT 1", [1]) == [{"id": 1}]


def test_dashboard_display_labels_are_uniform():
    assert pages_impl._display_label("suspicious_user_agent") == "Suspicious User Agent"
    assert pages_impl._display_label("rule") == "Rule"
    assert pages_impl._display_label(None) == "-"
    assert pages_impl._display_severity("medium") == "MEDIUM"
    assert pages_impl._display_severity(None) == "-"
