# Aborted: first tool-agent run (2026-10-05)

Kept for transparency; not used in any result. Both scenario-A tool-agent runs failed with HTTP
429 ("temporarily rate-limited upstream") while the four model runs were concurrent. The run was
stopped, A's fault was reverted, and the comparison was rerun with transport retries and
sequential model runs: see `../2026-10-05-tool-agent-deepseek-v4-pro/` and research notes §7b.
