# Legion: autonomous software workforce prototype

## Minimal architecture

1. **Codex worker layer:** 13 separate role profiles in `.codex/agents`, registered by `.codex/config.toml`. Ed coordinates; specialists handle scoped work. Codex's configured six-subagent limit bounds concurrent workers.
2. **Workflow kernel:** `workforce.py` stores project state, dependency-ready tasks, PR review decisions, Eleanor's QA result, and ordered approval evidence in JSON. A POSIX file lock serializes local state updates.
3. **Jira adapter:** `jira_adapter.py` can list project issues and create Task issues in the configured Jira project. The local workflow ledger is not yet synchronized with Jira; GitHub branch and PR lifecycle also remain unconnected.
4. **Human control:** Four approvals are recorded in order. Implementation tasks are locked until project and design approval. Gate 3 requires completed tasks, merged PRs, and Eleanor's passing validation. Gate 4 records production authorization.

Codex's agent profiles provide role instructions and multi-agent tools, but do not themselves provide durable scheduling, Jira synchronization, or a mandatory PR review policy. The workflow kernel owns local scheduling and policy checks; profiles own specialist behavior. All 13 identities are defined, with up to six subagents concurrent. Ed's role profile keeps coordination with the primary agent; Codex does not expose a supported per-project depth limit in this configuration.

## Four approval gates

1. Project objective, scope, priorities, and success criteria.
2. Architecture, implementation approach, and significant technical decisions.
3. Completed implementation, merged PRs, Eleanor's test results, and outstanding risks.
4. Explicit production deployment authorization.

The prototype stores an approver name and evidence for each gate and rejects skipped, out-of-order, or empty approvals. Gate 3 also checks that every task is complete, no PR remains open, and Eleanor's latest validation passed. Each completed code task requires a merged PR reviewed by Michael for its current attempt. The human owner must run approval commands. The local CLI records the supplied approver name; it does not authenticate that identity, so a real deployment must put approvals behind a trusted human interface. Silence never advances a gate.

## Run locally

Use a separate state file for each project. Approval commands are for the human owner to run after reviewing the evidence.

```sh
python3 workforce.py project demo "Build a small example service"
python3 workforce.py approve project_approval --approver "owner" --evidence "Scope and success criteria approved"
python3 workforce.py approve technical_design_approval --approver "owner" --evidence "Architecture approved"
python3 workforce.py task T1 "Define API contract" --agent hadrian --kind non_code
python3 workforce.py task T2 "Implement API" --agent teddy --depends-on T1
python3 workforce.py claim T1 --agent hadrian
python3 workforce.py done T1
python3 workforce.py claim T2 --agent teddy
python3 workforce.py pr-open PR-1 --task T2 --title "Implement API"
python3 workforce.py pr-review PR-1 --reviewer michael --decision approved --evidence "Contract, error paths, and tests reviewed"
python3 workforce.py pr-merge PR-1
python3 workforce.py done T2
python3 workforce.py validate --tester eleanor --result passed --evidence "Acceptance suite passed"
python3 workforce.py approve preproduction_approval --approver "owner" --evidence "Implementation and QA results accepted"
python3 workforce.py approve production_deployment_approval --approver "owner" --evidence "Production deployment authorised"
python3 workforce.py status
python3 -m unittest discover -s tests -v
```

The default state path is `.workforce/state.json`. It is project-local data and contains task details, review notes, QA outcomes, and approval evidence; do not put secrets in it. The state lock prevents two workers from claiming the same ready task. Use `retry TASK --reason "..." [--agent ID]` to recover a task left in progress by an interrupted worker.

## Jira connection

`.codex/jira.json` points to `https://efcloud.atlassian.net`, project `DATA` (Legion), and issue type `Task`. Set `JIRA_EMAIL` and `JIRA_API_TOKEN` in the shell environment for the account used by the adapter. The token is never stored in the repository or local ledger. Listing is read-only:

```sh
python3 jira_adapter.py list
```

Creating a Jira task changes Jira. The CLI requires a local project with Gate 1 recorded before it will send a create request:

```sh
python3 jira_adapter.py create --summary "Implement the approved work" --description "Acceptance notes"
```

The adapter does not currently sync status, assignees, comments, or dependency links between Jira and the local workflow ledger. Creating remote tasks still requires the user's authorization for that task.

## Current boundary

This is a local control plane, not a complete autonomous runtime. It does not invoke model workers, automatically create or synchronize Jira issues, open remote PRs, merge on GitHub, or deploy. Its PR and approval commands record local workflow evidence only. The kernel requires a merged, Michael-approved PR for the current task attempt before a code task can be completed; non-code tasks must be declared with `--kind non_code`. Scope freezes after Gate 3. Until branch protections exist, use actual GitHub PR review and deployment controls as the enforcement point. No credentials are stored here.

The next integration step is to synchronize the local workflow with Jira `DATA`, then connect GitHub branch/PR lifecycle and map its branch policy without changing the four-gate contract.
