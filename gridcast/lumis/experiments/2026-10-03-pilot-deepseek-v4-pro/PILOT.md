# Pilot run — not for reporting

This first run (J, A, F, C with deepseek-v4-pro-0813) shook out the harness and SDK integration.
Its numbers are **not** comparable to the main run and should not be reported as results:

* Incident windows looked back 30 min and scenarios ran back to back (~15 min apart), so A's,
  F's and C's windows reached the previous scenario (e.g. C's agent found F's 1.7.0 rollout).
* Loki count queries filtered the log *line*, but GridCast's error text is in structured metadata
  (`error`, `exception`), so log facts were always absent.
* feature-service did not count builds that failed before starting (C), so `feature-builds-failing`
  could not match.
* The first single-pass implementation used the SDK adapter (100 KB response cap; 2/4 failures)
  and was replaced mid-run (`raw/*/single_pass-r*.sdk-adapter-attempt`, `raw/J.first-attempt`).

All of these are fixed for the main run (20-min cooldown, 10-min lookback, metadata LogQL,
failure counting, uncapped single-pass). Raw artefacts are kept here for transparency.
What the pilot did show: J concluded deterministically in 7/7 runs (≤ 0.5 s, no model); on A the
agent reached a correct supported diagnosis in 2/2 runs ($0.11–0.16, 3.5–5.3 min).
