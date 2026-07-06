# Week 18 - LIME Explainability

## What was added
- `src/ml/lime_explainer.py`: local LIME explanations over the deployed `random_forest_latest.pkl`
- Alert-level LIME panel in the Streamlit Alerts page
- Fallback path that uses nearby raw logs when a `log_id` is not present in the cached training dataset
- Dashboard tests plus a focused unit test for the explainer

## Design choices
- LIME is attached to the supervised Random Forest because it exposes `predict_proba()` and gives a defensible local explanation target.
- The current hybrid runtime still combines rule logic with anomaly scoring, so the UI labels the explanation clearly as a `random_forest` local explanation rather than pretending it explains the whole ensemble.
- The dashboard first tries the stored feature dataset for stable explanations and only falls back to live feature reconstruction when needed.

## Operational note
- This requires the `lime` Python package from `requirements.txt`.
- If the package or model artifacts are unavailable, the dashboard shows an empty-state message instead of failing hard.

## Remaining limitation
- Counterfactual explanations are still open.
- This iteration explains the supervised anomaly probability, not the full hybrid score.
