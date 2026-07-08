# Counterfactual Explanations

## What was added
- `src/ml/counterfactual_explainer.py`
- Shared reference-dataset support in `src/ml/reference_explainer.py`
- Counterfactual panel in the Streamlit Alerts page for the selected related log

## How it works
The implementation is intentionally heuristic and grounded in real reference data.

1. Load the deployed supervised `RandomForest` and the selected supervised feature list.
2. Load the reference dataset used for explanation support.
3. For the selected log, locate the current feature row from the artifact dataset or reconstruct it from nearby raw logs.
4. Find the nearest opposite-class reference example.
5. Apply feature changes in weighted order until the model flips to the target class.

This means the explanation is not a purely synthetic optimization result floating off-manifold; it is anchored to an observed opposite-class pattern.

## Why this is responsible
- It avoids pretending to explain the full hybrid runtime score when the explanation target is actually the supervised Random Forest.
- It keeps the counterfactual tied to realistic feature values seen in the project's own reference artifacts.
- It is explicit about being local and heuristic.

## Limitation
- The current counterfactual explains the supervised `RandomForest` perspective, not the full rule + IF hybrid runtime.
- It is feature-space local reasoning, not an actionable business workflow recommendation.
