# Changelog

## [Unreleased]

### Added

- Add a synthetic McDelivery MCP service with 13 tools for delivery, pricing, and orders.
- Manage isolated episodes, inject business and transport faults, and export state snapshots and call traces.
- Run scripted Iris scenarios and save dialogue, approvals, and sandbox evidence.
- Archive closed episodes on disk and list or purge them through the management API.
- Use Qwen 3.8 Flash to recognize scripted dialogue intents in shadow or active mode.
- Exercise address and drink clarification and replay recorded monitor decisions from evidence.
- Define tasks as `tasks/*.yaml` specs with environment, fault plan, script, and expected checks.
- Separate the world fixture from account fixtures and open episodes with `scene` + `account`.
- Run a local regression suite with a fresh Iris process per scenario and export batch and dialogue summaries.
- Load local Sandbox, Runner, and Monitor settings from a shared `.env` template.
- Add a synthetic Weibo domain with 13 tools, a `daily` world, `alice` and `bob` accounts, four business faults, and a task manifest; user actions are recorded in the account and never change the world.
- Connect Iris to every domain a task declares, probe each domain before the task, and judge the probe per domain; Weibo connects through a generated OAuth credentials file, also available as `python -m runner.domains.weibo_cli` for direct runs.
- Add the `weibo.standard` check pack and a Weibo S0 task set of seven read-only tool contracts; tasks without a declared pack use each domain's own standard pack.
- Run task-set directories in regression with `--tasks-dir`, loading the set's own `checks.py` like `runner.batch`.
- Add `python -m runner.connect` so external orchestrators can prepare a task's Iris connection, including the Weibo credentials file, and receive the environment as JSON.

### Changed

- Run one scenario per Iris MCP credential and support episode-scoped MCP URLs.
- Drive the verdict from task expectations instead of scenario names.

### Fixed

- Reject an empty task set in regression runs, compute the McDelivery quote only for tasks that involve McDelivery, and include `SANDBOX_EPISODE_ID` in the `runner.connect` output.
- Run single-domain checks, including each domain's default pack, on a view of their own domain in multi-domain tasks, so the result no longer depends on the order of `env.domains`.
- Judge Weibo bindings only against results returned before the request, require an empty result to be reported as empty, and check the S0 search keyword and authors, the commented status, and the hot-word top three in order; `python -m runner` now loads a task set's own `checks.py`.
- Disable every domain a task does not use when connecting Iris in regression runs and through `runner.connect`, so host settings cannot route a misdirected call to a real service.
- Require visible quotes, matching Iris ledger entries, and read-only recovery evidence in scenario verdicts.
- Prevent in-flight requests from crossing a round or reset and record observed disconnects separately from planned faults.
- Preserve order chronology and reject invalid reservations and unsupported customizations.
- Pin Runner evidence to the verified episode generation and retry ambiguous opens by ID.
- Reject contradictory payment and delivery claims and classify recovery lookups by timing.
- Share clarification policies across monitor modes and validate replayed replies against scenario actions.
- Preserve matching quotes across comparisons and include reservation details in order receipts.
- Keep business verdicts available when only the diagnostic Iris trace is missing.
- Write run summaries for episodes without McDelivery orders; `orderCount` is `null` when the episode has no McDelivery domain.
- Record `envId` per declared domain (`weibo:daily:alice:0`); tasks without `env.domains` keep `scene:account:seed`.
