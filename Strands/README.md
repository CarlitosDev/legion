# Legion on AWS Strands

The Strands implementation of the autonomous workforce specified in [REQUIREMENTS.md](REQUIREMENTS.md): 13 specialist
agents (Strands `Agent`s on Amazon Bedrock) driven by a **deterministic workflow engine**. Personality is behavioural;
workflow enforcement is code.

```
CLI (human)  ──approve gates, resolve escalations──┐
                                                   ▼
Orchestrator ── Workflow engine (rules) ── Markdown ledger (.legion/<project>/…, audit.md)
 3 parallel     gates · task order · PR review        │            ▲
 timeouts       QA · escalation · budget · retries    ▼            │ mirror
 recovery                 ▲                       Jira (REST)  GitHub (gh)
                          │ tools (least privilege per agent)
                  Strands Agents → Bedrock (model + thinking budget per agent)
                  git worktree per code task → PR → Michael → checks → merge to integration branch
```

## What is enforced in code (not by prompts)
| Requirement | Mechanism |
|---|---|
| 4 human gates, in order, never implied | `Workflow.approve_gate`; CLI approval needs an interactive TTY and typed confirmation; agent subprocesses get no stdin and cannot run `legion` |
| Tasks wait for their stage's gate and their dependencies | `Workflow.runnable` |
| Every code change reviewed by Michael | A code task completes only when its PR is `merged`; merge needs Michael's approval **and** passing checks; approval covers the exact HEAD reviewed |
| Michael can't block forever | Only critical/major findings block; after `max_review_rounds` the PR escalates to you |
| Authors can't review/bypass | Only `michael` reviews; authors only resolve findings |
| Eleanor blocks release | Gate 3 needs a passing validation newer than the last merge; a critical defect forces failure; failures spawn fix tasks (planned by Ed) plus a re-validation task |
| Architecture baseline | Design decisions freeze at Gate 2; reopening needs ≥40 chars of evidence and goes to you as an escalation |
| Demis can't derail delivery | His tools file ideas to `backlog/`; he gets a longer timeout (10 min) |
| Will only for educational projects | `new --educational` |
| Ed's weaknesses don't matter | Scheduling, timeouts, retries and recovery are the orchestrator's |
| Budget | Per-task and per-project token caps; exceeding the project cap escalates and halts |
| Recovery | All state is markdown; `run` re-queues tasks left `in_progress` by a crash |

## Setup
```sh
cd Strands
uv venv --python 3.13 .venv && uv pip install --python .venv/bin/python -e '.[dev]'
cp .env.example .env     # AWS profile/region, model catalog (Opus + Luna), per-agent models, timeouts
.venv/bin/legion roster  # shows each agent's resolved model
```
Settings are Pydantic (`src/legion/settings.py`), overridable with `LEGION_*`. Luna's Bedrock model id has to be
supplied (`LEGION_MODEL_CATALOG__LUNA`); until then no agent resolves to it. Defaults: eu-west-1, 3 parallel agents,
300 s per task attempt (Demis 600 s), thinking budgets for Demis and Hadrian.

## Run a project
```sh
legion new "Student progress" projects/student-progress-brief.md      # add --educational to activate Will
legion plan discovery        # Ed creates discovery tasks
legion run                   # agents work in parallel until idle
legion status; legion tasks; legion escalations
legion approve project --approver "<you>" --evidence "<what you reviewed>"     # Gate 1 (interactive)
legion plan design && legion run
legion approve design  ...   # Gate 2
legion plan implementation && legion run   # code tasks: worktree → PR → Michael → checks → merge
legion plan release / validation …         # then approve preproduction (3) and production (4)
```
Everything lands in `.legion/<project>/` as readable markdown: `tasks/`, `prs/`, `decisions/`, `escalations/`,
`approvals/`, `validations/`, `backlog/`, `docs/` and the append-only `audit.md`.

## Integrations (off by default)
- **Jira (DATA on efcloud):** `LEGION_JIRA_ENABLED=true`, `JIRA_EMAIL`, `JIRA_API_TOKEN`. New tasks become issues, status
  changes transition them, decisions become comments. Issue type and status names are configurable
  (`LEGION_JIRA_ISSUE_TYPE`, `LEGION_JIRA_STATUS_MAP`). `legion jira-backfill` adds issues for existing tasks.
- **GitHub (CarlitosDev/legion):** `LEGION_GITHUB_ENABLED=true` and `gh` logged in. Task PRs target
  `legion/integration`; Michael's review is posted as a PR comment (GitHub forbids approving your own PR from one
  account, so the engine's recorded approval is what gates `gh pr merge`). Without it, PRs live in the ledger and merge locally.
- Code is written into `LEGION_REPO_PATH` (default: the git repo at cwd) via worktrees under the project's state dir; your
  own checkout is never touched. Merging `legion/integration` into `main` and the actual production deploy are **not automated yet**:
  release/production tasks run as agent tasks, and Gate 4 is recorded, but you do the final merge/deploy yourself.

## Limits to know about
- **Not a sandbox.** File tools are confined to the worktree; shells, `-c`/`-e` one-liners, git branching/pushing, `gh`,
  `aws`, `curl` and `legion` are denied; agent commands run with a scrubbed environment (no AWS/Jira/GitHub credentials)
  and no stdin. But an agent can still run code it has written into a file (e.g. `python script.py`), with your user's
  filesystem and network access. For untrusted work, run under a restricted OS user or container with no network and
  no access to the main repo's `.git`.
- Timeouts use Strands' `cancel_signal`: an agent stops at its next loop boundary, not mid model call, and its slot stays
  occupied until then so the parallel cap holds.
- Jira/GitHub adapters are covered by unit tests with mocked transports; they have not been exercised against the real services.
- The Bedrock path (`StrandsInvoker`) is tested with a scripted model through a real Strands event loop; no live Bedrock call has been made.

## Tests
`.venv/bin/python -m pytest -q` (47 tests: engine rules, end-to-end lifecycle with real git worktrees, parallel cap,
timeouts, crash recovery, tools, adapters, CLI).
