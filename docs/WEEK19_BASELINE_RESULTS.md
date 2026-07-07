# Week 19 Throughput Baseline Results

Generated at: `2026-07-07T14:16:56.896095+00:00`

## Scope

This report summarizes the latest persisted throughput snapshots captured by the Week 19 instrumentation layer.

## Benchmark Config

- Started at: `2026-07-07T14:16:46.742387+00:00`
- Source files: `logs/app.log.backup, logs/app.log`
- Replay file: `tmp/week19_baseline_replay.log`
- Replay lines: `5000`
- Source line pool: `3376`
- Ingestion batch size: `500`
- Realtime fetch limit: `1000`
- Realtime poll interval: `2.000` s
- Configured ceiling: `500.000` logs/s
- Measurement mode: `active_processing_no_sleep`
- Measurement note: `Week 19 collector measures active processing throughput without inter-cycle sleep; the steady-state daemon loop remains bounded by fetch_limit / poll_interval.`
- Raw logs before run: `159524`
- Benchmark start log id: `159626`
- Raw logs after run: `164524`
- Hybrid max log id before run: `159586`
- Hybrid max log id after run: `164626`
- Rule engine status: `skipped`
- Rule engine note: `Manual rule_engine invocation was skipped to avoid duplicating alerts on the shared development dataset; a background realtime rule-engine service may still create alerts while the stack is running.`

## Component Summary

| Component | Recorded At | Duration (s) | Throughput (logs/s) | Batch/Logs | Errors |
|---|---|---:|---:|---:|---:|
| hybrid_pipeline | 2026-07-07T14:16:56.818320+00:00 | 0.248 | 4025.615 | 1000 | 0 |
| ingester | 2026-07-07T14:16:51.462799+00:00 | 3.969 | 1259.708 | 5000 | 0 |
| realtime_hybrid | 2026-07-07T14:16:56.865715+00:00 | 2.187 | 2286.194 | 5000 | 0 |

## Highlights

- Highest observed throughput: `hybrid_pipeline` at `4025.615` logs/s.
- Lowest observed throughput: `ingester` at `1259.708` logs/s.
- Active processing throughput exceeds the configured steady-state ceiling by `357.2%`.

## hybrid_pipeline

- Recorded at: `2026-07-07T14:16:56.818320+00:00`
- Duration: `0.248` s
- Throughput: `4025.615` logs/s
- Logs processed: `1000`
- Anomalies found: `0`

## ingester

- Recorded at: `2026-07-07T14:16:51.462799+00:00`
- Duration: `3.969` s
- Throughput: `1259.708` logs/s
- Logs ingested: `5000`
- Failed records: `0`
- Configured batch size: `500`
- Source file: `/home/raulb/projects/log-monitor-mlops/tmp/week19_baseline_replay.log`

## realtime_hybrid

- Recorded at: `2026-07-07T14:16:56.865715+00:00`
- Duration: `2.187` s
- Throughput: `2286.194` logs/s
- Logs processed: `5000`
- Anomalies found: `0`
- Configured fetch limit: `1000`
- Configured poll interval: `2.000`
- Configured ceiling: `500.000`
- Cycles executed: `5`
- Remaining unprocessed logs: `0`
- Fetch duration: `0.141`
- Feature engineering duration: `0.404`
- Evaluation duration: `1.637`
