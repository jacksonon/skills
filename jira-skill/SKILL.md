---
name: jira-skill
description: Read a JIRA issue, create JIRA tasks from a source document, or execute a JIRA task in a target repo. Use when the user provides a JIRA link/key or a document link and wants Codex to turn that work into scoped implementation steps with concrete verification.
---

# Jira Skill

`jira-skill` supersedes `from-jira`.

## When To Use

Use this skill when the request starts from one of these inputs:

- a JIRA browse URL or issue key that should become repo work
- a source document that should be decomposed into one or more new JIRA tasks
- a JIRA task plus a target repo where the task should be implemented with multi-agent help

## Workflow Router

Classify the request before doing anything else.

- `Workflow A - JIRA issue -> repo fix`: use for bug fixes or direct task execution when the user gives a JIRA issue URL/key and wants implementation in the current repo.
- `Workflow B - document -> create JIRA task(s)`: use when the user gives a document link or pasted spec and wants new JIRA tasks created from it.
- `Workflow C - JIRA task + target repo -> multi-agent implementation`: use when the user gives a JIRA issue and a specific repo/project path and wants the task executed with multi-agent coordination.

If the request mixes workflows, finish them in order: `B` to create tasks, then `C` to execute one of the new tasks.

## Shared Rules

### Normalize

- For JIRA-linked workflows, run:
  - `python "<skill-path>/scripts/normalize_jira_ref.py" "<jira-url-or-key>"`
- Confirm `issue_key` and, when available, `browse_url` before reading or updating JIRA.
- Do not guess the ticket identity from prose if the parsed key and the request disagree.

### Read Live Source Of Truth

- Prefer live JIRA data or the live source document over cached summaries.
- When browser access is needed, prefer an attached browser session that can reuse an existing logged-in Chrome/Chromium profile or current tab before opening an isolated browser context.
- Only fall back to an isolated browser session when an attached session is unavailable or cannot reach the target content.
- Gather the current summary, description, acceptance criteria, status, comments, and any linked artifacts that change scope.
- If the page is already accessible, continue immediately; only pause and wait for the user to log in when a login page, SSO challenge, or permission gate actually appears.
- If the source cannot be accessed after login or authorization, stop and report the blocker instead of inventing requirements.

### Clarify Before Acting

Pause and ask focused questions if any of these are true:

- expected behavior or acceptance criteria are unclear
- multiple implementation directions are plausible
- reproduction conditions are missing
- linked comments or screenshots change the scope
- the repo cannot satisfy the request without hidden backend/product rules

### Respect Repo Guardrails

- Read every applicable `AGENTS.md` in the target repo before editing.
- Inspect `git status` before changes so user work is preserved.
- Prefer the smallest defensible change and the narrowest useful validation command.

### JIRA Hygiene

- When execution work is clear enough to start, move the issue to `IN PROGRESS` if the workflow expects active implementation.
- When work is complete, update the issue/comment with the actual repo result, validation command, and any remaining gap.
- Use the same authenticated channel for read/write when possible: API first if already available, otherwise the authorized browser session.

## Shared Artifacts

Create one lightweight artifact before deeper execution.

- `issue brief`: normalized JIRA summary, acceptance criteria, constraints, likely code surface, validation target
- `task payload`: create-ready JIRA fields derived from a document
- `execution brief`: frozen handoff for multi-agent implementation containing ticket scope, repo path, target modules, and validation entrypoints

Use the templates in `templates/` instead of free-form notes when helpful.

## Workflow A - JIRA Issue Execution

### Input

- JIRA browse URL or issue key
- current checked-out repo

### Steps

1. Normalize the JIRA reference with `normalize_jira_ref.py`.
2. Read the live ticket and produce an `issue brief`.
3. Resolve ambiguity before editing code.
4. Move the issue to `IN PROGRESS` once execution is clear.
5. Map the ticket into repo scope:
   - identify project root
   - read scoped `AGENTS.md`
   - inspect `git status`
   - search for identifiers, UI strings, modules, APIs, and validation entrypoints
6. Implement the smallest safe fix.
7. Run the narrowest useful validation.
8. Update JIRA with the fix summary, verification result, and any remaining gap.
9. Move the issue to its done state only when the repo result matches the ticket scope.

### Report

Include:

- issue key and summary
- files/modules changed
- validation command run, or the exact blocker
- any assumption that still depends on hidden JIRA or environment state

## Workflow B - Document To JIRA Tasks

### Input

- a source document link, page, PRD, spec, or pasted text
- optional project/component/label defaults

If the source document is already accessible, continue directly. Only pause for login when the document host or JIRA creation flow actually requires authentication.

### Steps

1. Fetch or read the source document.
2. Extract:
   - user problem
   - scope boundaries
   - acceptance criteria
   - dependencies, rollout constraints, and open questions
3. Deduplicate against any JIRA issues already linked in the document.
4. Split the work into issue-sized tasks. Keep each task independently executable.
5. Produce a `task payload` per task with:
   - summary
   - description/context
   - acceptance criteria
   - priority or severity if evident
   - labels/components if the source makes them clear
   - source-document backlink
6. Create the task(s) through the available JIRA channel.
7. Return the created issue keys/links plus a short explanation of how the document was decomposed.

### Stop Conditions

- If the source is too vague to create actionable tasks, stop after analysis and ask only for the missing product/engineering detail.
- Do not create placeholder JIRA tasks with empty acceptance criteria.

## Workflow C - Multi-Agent Task Implementation

### Input

- JIRA browse URL or issue key
- explicit target repo path

### Required Roles

Use multiple agents only after building one shared `execution brief`.

- `ticket analyst`: extracts exact ticket scope, edge cases, and done criteria
- `repo mapper`: finds likely modules, existing patterns, and validation entrypoints
- `implementer`: owns the code change in the target repo
- `verifier`: checks tests/builds/logs and looks for scope gaps

One owner agent remains responsible for final synthesis, code integration, and JIRA updates.

### Steps

1. Normalize the JIRA reference.
2. Read the live ticket and target repo constraints.
3. Produce the `execution brief` before spawning agents.
4. Fan out the role-specific agents with non-overlapping responsibilities.
5. Synthesize their findings into one implementation plan.
6. Execute the code change in the target repo.
7. Run validation and resolve any agent-discovered gaps.
8. Update JIRA with the final implementation and verification evidence.

### Coordination Rules

- Do not send overlapping write ownership to multiple implementers.
- Keep one frozen ticket snapshot for all agents so requirements do not drift.
- Do not let subagents independently transition JIRA status or leave duplicate comments.
- If one role finds a blocker that invalidates the plan, stop fanout and resynchronize before more edits.

## Templates

- `templates/jira_issue_brief.md`
- `templates/jira_create_payload.md`
- `templates/multi_agent_execution_brief.md`

## Notes

- This skill stays transport-agnostic because JIRA access may come from API tokens, browser sessions, SSO, VPN, or MCP tools.
- The bundled script normalizes a JIRA URL or raw issue key. Use live tools for the actual ticket contents and updates.
