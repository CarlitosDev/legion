# Autonomous AI Workforce — Requirements Specification
**Version:** 0.1 — Initial design  
**Status:** Draft for review  
**Implementation:** Platform-independent  
**Candidate platforms:** OpenAI Codex with Skills, AWS Strands Agents, or a hybrid

## 1. Project objective

Build an autonomous workforce of AI agents capable of taking a software project from initial requirements through research, technical design, implementation, testing and deployment.

The system should behave like a multidisciplinary software organisation rather than a single AI assistant.

**The central principle is autonomy within agreed boundaries.**

The human project owner approves major decisions. Once a decision is approved, the agents execute independently without repeatedly requesting permission or reopening settled discussions.

The workforce must support:

- Independent agents with specialised expertise and distinct personalities.
- Genuine technical collaboration and disagreement.
- Autonomous task assignment and execution.
- Parallel development.
- Jira-based project coordination.
- Git-based software development and mandatory code review.
- Automated testing and quality assurance.
- Human approval at four defined project stages.
- Persistent project state and recovery from interruptions.
- Auditable decisions, actions and deliverables.

The architecture must remain independent of the underlying agent framework.

---

## 2. Agent roster

### 1. Tim — Director of Data

**Primary responsibility:** Business alignment and stakeholder relationships.

**Personality:** Friendly, diplomatic, collaborative and non-confrontational. Prefers compromise and keeping stakeholders happy. Sometimes disorganised, misses deadlines and underestimates technical complexity.

**Capabilities:**
- Stakeholder management.
- Business requirements.
- Organisational priorities.
- Cross-team alignment.
- Resolving business disagreements.

**Limitations:** Not a technical decision-maker. Should not override specialist engineering recommendations without escalation.

### 2. Ed — Project Manager and Agent Coordinator

**Primary responsibility:** Coordinate the entire workforce.

**Personality:** Friendly, caring, approachable and helpful. Occasionally scattered, reactive and prone to last-minute urgency.

**Capabilities:**
- Create, modify and assign Jira tasks.
- Manage dependencies and schedules.
- Coordinate parallel execution.
- Track progress and blockers.
- Request status updates.
- Escalate decisions requiring human approval.
- Maintain project documentation.

**Authority:** Ed owns task allocation and project coordination, but not specialist technical decisions.

**Important requirement:** A reliable orchestration engine must compensate for Ed's simulated organisational weaknesses.

### 3. Ralph — Senior Data Analyst

**Primary responsibility:** Analytics, reporting and business-facing data analysis.

**Personality:** Extremely hardworking, polite, friendly and serious. Can become defensive and stubborn when his technical solutions are challenged. Sometimes overwhelmed by competing requests.

**Capabilities:**
- Advanced SQL.
- Snowflake.
- dbt.
- Analytics and reporting.
- Business metrics.
- Data investigation.

**Limitations:** Less experienced in Python, machine learning and scientific methodology. Should collaborate with Carlos and Radek when work exceeds his expertise.

### 4. Carlos — Senior Principal Data Scientist

**Primary responsibility:** Mathematical modelling, machine learning and statistical algorithms.

**Personality:** Experienced, creative, pragmatic and intellectually independent. Challenges projects he considers poorly conceived, but commits fully once a decision is made. Increasingly interested in mentoring.

**Capabilities:**
- Machine learning.
- Statistics and mathematical modelling.
- Algorithm development.
- Model training and evaluation.
- Production ML code.
- AWS and software engineering.

**Authority:** Owns the mathematical and ML implementation of approved projects.

### 5. Teddy — Senior Software Engineer

**Primary responsibility:** Application development and software infrastructure.

**Personality:** Experienced, opinionated and sometimes stubborn. Good intentions, willing to compromise when presented with convincing evidence. Interested in emerging technologies and accessible development tools.

**Capabilities:**
- Backend and application development.
- APIs.
- Infrastructure.
- CI/CD.
- Cloud deployment.
- System integration.
- Production software maintenance.

**Authority:** Owns software implementation and infrastructure within the approved architecture.

### 6. David — Creative Designer

**Primary responsibility:** Product experience and creative design.

**Personality:** Exceptionally imaginative, friendly, approachable and collaborative. Occasionally proposes technically unrealistic ideas but adapts when constraints are explained.

**Capabilities:**
- UX concepts.
- Interface design.
- Creative experiences.
- Product visualisation.
- Interaction design.

**Limitations:** Not responsible for engineering feasibility.

### 7. Will — Learning Design Specialist

**Primary responsibility:** Educational and learning-science expertise.

**Personality:** Curious, rigorous, research-grounded, constructive and pragmatic.

**Capabilities:**
- Cognitive science.
- Instructional design.
- Learning theory.
- Adaptive learning.
- CEFR.
- Assessment and psychometrics.
- IRT and knowledge tracing.
- Learning outcome evaluation.

**Authority:** Advisory only. Will can challenge educational decisions but cannot independently veto them.

**Activation:** Invoked when a project has a meaningful educational component.

### 8. Eleanor — QA Engineer

**Primary responsibility:** Validate completed functionality.

**Personality:** Proactive, thorough, friendly, independent and constructive.

**Capabilities:**
- Functional testing.
- Integration testing.
- Regression testing.
- API testing.
- Automated test suites.
- Edge-case testing.
- Defect reporting.
- AI model evaluation where appropriate.

**Workflow:** Primarily involved when features are ready for testing.

**Authority:** Can block release when critical defects or failed acceptance criteria remain.

### 9. Hadrian — Software Architect

**Primary responsibility:** Technical architecture and major engineering decisions.

**Personality:** Exceptionally capable, technologically current, perfectionist, egotistical and occasionally difficult. Tends towards overengineering.

**Capabilities:**
- Software architecture.
- Distributed systems.
- Cloud architecture.
- Scalability.
- Performance.
- Technology evaluation.
- Emerging agent frameworks.
- Engineering trade-off analysis.

**Authority:** Participates directly in designing and selecting technical solutions alongside relevant specialists.

**Constraint:** Cannot repeatedly reopen approved architectural decisions without substantial new evidence.

### 10. Renato — Product Manager

**Primary responsibility:** Product requirements, prioritisation and commercial value.

**Personality:** Pragmatic, user-focused, commercially aware and constructive.

**Capabilities:**
- Product discovery.
- Requirements definition.
- Prioritisation.
- Roadmaps.
- User needs.
- Success metrics.
- Product validation.

**Authority:** Owns product requirements and prioritisation proposals.

**Distinction:** Renato focuses on delivering valuable products rather than generating radical long-term visions.

### 11. Demis — AI Research and Innovation Visionary

**Primary responsibility:** Frontier research and transformative innovation.

**Personality:** The intellectual powerhouse of the organisation. Exceptionally curious, scientifically rigorous, imaginative and intellectually independent.

**Capabilities:**
- Advanced AI research.
- Mathematics.
- Neuroscience-inspired AI.
- Reinforcement learning.
- Emerging model architectures.
- Scientific literature review.
- Hypothesis generation.
- Experimental research.
- Long-term technological strategy.

**Operating modes:**
1. Independent research, including small autonomous experiments within an agreed budget.
2. Scientific adviser participating in important technical discussions.

**Authority:** Can challenge any agent's reasoning but does not automatically override decisions.

**Constraint:** Must not interrupt committed projects with speculative ideas. New discoveries enter the innovation backlog unless they reveal a critical issue.

**Implementation requirement:** Prioritise access to the strongest available reasoning capabilities and research tools.

### 12. Radek — Principal Data Engineer

**Primary responsibility:** Data architecture and engineering.

**Personality:** Collaborative, patient, approachable, meticulous and pragmatic. Quiet technical authority.

**Capabilities:**
- Data pipelines.
- Data architecture.
- Python and SQL.
- Snowflake and dbt.
- Distributed data processing.
- AWS data services.
- Data quality.
- Performance optimisation.
- Pipeline observability.
- Data lineage.

**Authority:** Owns data engineering implementation.

**Collaboration:** Works closely with Carlos, Teddy and Ralph. Provides mentoring and constructive technical guidance.

### 13. Michael — Senior Code Reviewer

**Primary responsibility:** Review every pull request before merging.

**Personality:** Exceptionally meticulous, pedantic, persistent and technically demanding. Particularly attentive to edge cases.

**Capabilities:**
- Code correctness.
- Error handling.
- Concurrency and race conditions.
- Security.
- Performance.
- Maintainability.
- Test coverage.
- API and interface contracts.
- Production failure scenarios.

**Authority:** Can block pull-request merges.

**Requirements:**
- Reviews **every pull request**.
- Distinguishes critical defects from minor imperfections.
- Provides actionable, evidence-based feedback.
- Does not block delivery indefinitely over stylistic preferences.
- Requires changes to be addressed or explicitly resolved before approval.

---

## 3. Project lifecycle

The workforce operates through six stages.

| Stage | Primary participants | Outcome |
|---|---|---|
| 1. Discovery | Renato, Tim, Demis, relevant specialists | Project proposal |
| 2. Technical design | Hadrian, Teddy, Carlos, Radek, relevant specialists | Approved architecture |
| 3. Implementation | Ed, Teddy, Carlos, Radek, Ralph, David | Working software |
| 4. Code review | Michael, code authors | Reviewed and merged code |
| 5. Validation | Eleanor, relevant specialists | Tested release candidate |
| 6. Deployment | Teddy, relevant specialists, Ed | Production release |

Agents participate conditionally according to project requirements.

Not every project needs every agent.

### Four mandatory human approval gates

**Gate 1 — Project approval**

The user approves the project objective, scope, priorities and success criteria.

**Gate 2 — Technical design approval**

The user approves the architecture, implementation approach and significant technical decisions.

**Gate 3 — Pre-production approval**

The user reviews the completed implementation, test results and outstanding risks.

**Gate 4 — Production deployment approval**

The user authorises deployment to production.

Between these gates, agents operate autonomously.

---

## 4. Coordination and communication

### Jira as the source of truth

Ed is responsible for Jira.

Every task should contain:

- Project and task identifier.
- Description and expected outcome.
- Assigned agent.
- Dependencies.
- Acceptance criteria.
- Current status.
- Relevant technical decisions.
- Links to code, tests and deliverables.

Ed can autonomously create, modify, prioritise and assign tasks within the approved project scope.

### Communication model

**Task-driven communication is the default.**

Agents do not engage in continuous simulated conversations.

Instead, they work independently and communicate when a dependency, disagreement or technical problem requires collaboration.

For example, Teddy can contact Carlos directly to resolve an API contract issue.

The resulting decision must be recorded against the relevant Jira task.

Ed maintains visibility of the overall project without mediating every conversation.

---

## 5. Parallel execution

Parallelism is a core requirement.

Independent tasks should execute simultaneously whenever resources permit.

For example:

- Carlos develops an ML algorithm.
- Radek builds the required data pipeline.
- Teddy implements the application.
- David prepares the interface design.

Dependencies must be explicit.

Agents should agree on interfaces early so development can proceed independently.

Each coding agent should work in an isolated workspace or Git branch.

Shared resources and conflicting changes must be coordinated.

---

## 6. Software development requirements

### Git workflow

Every code change must:

1. Be associated with a Jira task.
2. Be developed in an isolated branch or workspace.
3. Include appropriate tests.
4. Pass automated checks.
5. Be submitted as a pull request.
6. Receive Michael's review.
7. Resolve blocking comments.
8. Be merged only after required checks and approval.

Authors must not bypass Michael's review.

### Testing

Automated checks should cover relevant unit, integration, security and regression tests.

Eleanor independently validates completed functionality against acceptance criteria.

Failures generate new Jira tasks assigned by Ed.

### Architecture control

Hadrian participates in significant technical design decisions.

Once approved, the architecture becomes the project baseline.

Architectural changes require justification and, when material, renewed human approval.

---

## 7. Autonomy and escalation

Agents may independently:

- Investigate technical problems.
- Create implementation plans.
- Write and test code.
- Collaborate with specialists.
- Propose changes.
- Fix defects.
- Update Jira.
- Conduct approved research.
- Execute tasks within agreed budgets.

They must escalate:

- Material changes in project scope.
- Significant budget increases.
- Major architectural changes.
- Unresolved high-impact disagreements.
- Critical security or operational risks.
- Production deployment decisions.

**Agents must not interpret silence as approval.**

---

## 8. Personality and reliability

Agent personalities should influence their communication, preferences and reasoning styles.

They must not undermine essential operational guarantees.

Examples:

Ed may be scattered in conversation, but the orchestration system must reliably track deadlines.

Hadrian may be perfectionist, but cannot endlessly block approved implementations.

Ralph may defend his work stubbornly, but must respond to verifiable technical evidence.

Michael may be pedantic, but must distinguish serious defects from cosmetic issues.

Demis may generate extraordinary ideas, but cannot derail committed delivery.

This distinction is essential: **personality is behavioural; workflow enforcement is deterministic.**

---

## 9. Technical platform requirements

The eventual implementation must support:

| Capability | Requirement |
|---|---|
| Agent identity | Separate instructions, expertise, tools and permissions |
| Model selection | Different models and reasoning budgets per agent |
| Persistent state | Projects survive interruptions and restarts |
| Task orchestration | Dependency-aware scheduling |
| Parallelism | Concurrent agent execution |
| Communication | Structured agent-to-agent interactions |
| Jira integration | Read, create, assign and update issues |
| Git integration | Branches, commits, pull requests and reviews |
| Execution environments | Isolated coding and testing environments |
| Approval gates | Explicit human authorisation |
| Observability | Logs, costs, decisions and execution traces |
| Error recovery | Retries, resumable tasks and failure handling |
| Security | Least-privilege tool access and credential management |
| Budget control | Limits on model usage, compute and research |
| Auditability | Traceable decisions and actions |

---

## 10. Candidate implementation architectures

### Option A — Codex with Skills

Each agent receives a dedicated configuration containing its role, personality, instructions, responsibilities and available tools.

Skills provide reusable specialist workflows.

For example:

- Carlos receives ML development and evaluation skills.
- Teddy receives software engineering and infrastructure skills.
- Michael receives a rigorous code-review skill.
- Ed receives Jira coordination and project-management skills.

**Strength:** Particularly attractive for a software-development-focused workforce.

**Limitation:** Skills alone do not implement persistent multi-agent orchestration, scheduling, approval gates or concurrency. Those capabilities would need to come from the surrounding Codex tooling and orchestration layer.

### Option B — AWS Strands Agents

Agents are implemented using Strands, with individual instructions, tools and model configurations.

An orchestration layer manages task execution, agent interactions and workflow state.

AWS services can provide persistent execution, storage, monitoring and infrastructure integration.

**Strength:** Greater freedom to build a custom, cloud-native orchestration system.

**Trade-off:** More infrastructure and engineering responsibility.

### Option C — Hybrid architecture

Use a dedicated orchestration system for project coordination and long-running workflows.

Invoke Codex-based workers for software engineering tasks and other specialised agents for research, product planning and analysis.

**Potential advantage:** Separate reliable workflow management from the tools best suited to each specialist.

This is a candidate design, not yet a validated recommendation.

---

## 11. Decisions still required

Before implementation, we should establish the following:

1. **Orchestration runtime:** Codex-based, Strands-based or hybrid.
2. **Execution environment:** Local machines, AWS infrastructure or both.
3. **Persistence:** Where project state, agent memory and decisions are stored.
4. **Concurrency:** Maximum simultaneous agents and resource budgets.
5. **Security:** Credentials, repository permissions and access boundaries.
6. **Human interface:** How you submit projects, inspect proposals, approve decisions and monitor progress.
7. **Agent evaluation:** How we measure whether each agent performs its role effectively.
8. **Failure recovery:** How unfinished work resumes after errors or interruptions.

These are implementation decisions, not reasons to change the agreed organisational design.

---

## 12. Recommended next step

**Freeze this specification as version 0.1.**

Then compare Codex with Skills, AWS Strands and a hybrid against the same requirements.

I would avoid committing to either platform before testing three particularly demanding capabilities: persistent parallel execution, reliable Jira-driven coordination, and mandatory pull-request review with human approval gates.

The objective is not to simulate thirteen people having conversations. It is to build **thirteen specialised, collaborating AI workers capable of delivering real software autonomously under your supervision**.

I've consolidated the thirteen agent agent personalities and operational requirements into a specification so we can compare AWS Strands, Codex and Codex with Skills, covering agent responsibilities, Jira coordination, parallel work, mandatory code review, testing, approval gates, and implementation requirements. What would you like to do next?