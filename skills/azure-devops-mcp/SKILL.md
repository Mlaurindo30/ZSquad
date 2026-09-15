---
name: azure-devops-mcp
description: Industrial-grade operational skill for the Azure DevOps MCP server (@azure-devops/mcp). Covers all 40 tools across Core, Work, Pipelines, Repos, WIT, Wiki, Test Plans, Search, and Advanced Security with strict Segregation of Duties (SoD) and error handling.
version: 1.0.0
author: Martin Fowler & Gregor Hohpe (04-solution-architect)
compliance:
  iso_27001: ["A.5.3", "A.8.28", "A.8.32"]
  soc_2: ["CC6.1", "CC8.1"]
  nist_sp_800_53: ["CM-5"]
---

# Azure DevOps MCP Operational Skill (40 Tools)

### 1.1 Governance & Segregation of Duties (SoD) Matrix

Under **ADR-0001**, all operations executed via the Azure DevOps MCP server MUST comply with identity isolation boundaries:
1. **`development_team` (`squads@michellaurindooutlook812.onmicrosoft.com`):** 38 contributor personas. Authorized for authoring code, creating branches, opening PRs, updating work items in progress, running test pipelines, and posting non-approving commentary.
2. **`pr_and_card_approver` (`arthemis@michellaurindooutlook812.onmicrosoft.com`):** 5 governance reviewer personas (`code-reviewer`, `security-reviewer`, `qa-engineer`, `performance-engineer`, and `governance-auditor`). Authorized for required PR review votes, comment thread resolution, and closing G6 work items. **The author account (`squads@`) can NEVER vote on its own PR or close its own card.**
3. **`service_accounts.cyber_red` (`cyber_red@michellaurindooutlook812.onmicrosoft.com`):** 1 persona (`34-offensive-cyber-operator`). Dedicated identity for offensive security, penetration testing, and red team verification. Operates under mandatory cross-account dual sign-off (`double_signoff_with: [security-reviewer]`) on sensitive paths: `/auth/`, `/crypto/`, `/iac/`, `*.tf`, and `Dockerfile`.
4. **`service_accounts.customer_data_pii` (`customer_data_pii@...`):** Dedicated read-only service account for PII compliance checks; 0 personas assigned to vote.
5. **`human_master` (`Michel`):** Final arbiter and break-glass authority.

---

### 1.2 Group 1: Core Services (3 Tools)

#### 1. `core_list_projects`
- **Objective:** Enumerate accessible projects within the Azure DevOps organization to resolve project GUIDs and status.
- **Parameters:**
  - *Required:* None.
  - *Optional:*
    - `continuationToken` (string): Pagination token for fetching subsequent pages.
    - `projectNameFilter` (string): Filter projects by name substring.
    - `skip` (integer): Number of projects to skip.
    - `stateFilter` (string): Filter by state (`all`, `createPending`, `deleted`, `deleting`, `new`, `unchanged`, `wellFormed`). Default: `wellFormed`.
    - `top` (integer): Maximum number of projects to return.
- **Invocation Payload Example:**
```json
{
  "stateFilter": "wellFormed",
  "projectNameFilter": "Arthemis",
  "top": 10
}
```
- **SoD Policy:** Read-only operation. Callable by all 41 personas (`squads@`, `arthemis@`, `cyber_red@`).
- **Failure Modes & Error Handling:**
  - `401 / 403 Forbidden`: Expired Personal Access Token (PAT) or insufficient organization permissions. Circuit breaker logs failure and alerts `00-delivery-orchestrator`.
  - `404 Not Found`: Organization URL invalid or unreachable.

#### 2. `core_list_project_teams`
- **Objective:** List teams defined within a project or organization to resolve team identity IDs for capacity and sprint tracking.
- **Parameters:**
  - *Required:* None.
  - *Optional:*
    - `mine` (boolean): If true, returns only teams where the authenticated identity is a member.
    - `project` (string): Name or ID of the project. If omitted, returns all teams across the organization.
    - `skip` (integer): Number of teams to skip.
    - `top` (integer): Maximum number of teams to return.
- **Invocation Payload Example:**
```json
{
  "project": "Arthemis",
  "mine": false,
  "top": 20
}
```
- **SoD Policy:** Read-only operation. Usable by orchestrators, architects, and team leads across all accounts.
- **Failure Modes & Error Handling:**
  - Returns empty list if project name is misspelled; validation must cross-check with `core_list_projects`.

#### 3. `core_get_identity_ids`
- **Objective:** Resolve user or group display names/emails to their unique Azure DevOps identity GUIDs for assignment and reviewer voting.
- **Parameters:**
  - *Required:*
    - `searchFilter` (string): Display name, email address, or account name of the identity.
- **Invocation Payload Example:**
```json
{
  "searchFilter": "arthemis@michellaurindooutlook812.onmicrosoft.com"
}
```
- **SoD Policy:** Read-only discovery. Critical for mapping PR reviewer IDs before issuing `repo_pull_request_write`.
- **Failure Modes & Error Handling:**
  - Returns empty array if user does not exist in Azure AD tenant. Requires fallback to directory search or logging `IDENTITY_NOT_RESOLVED`.

---

### 1.3 Group 2: Work, Iterations & Capacity (3 Tools)

#### 4. `work`
- **Objective:** Retrieve work-related configuration, sprint iterations, team settings, and member capacity allocations.
- **Parameters:**
  - *Required:*
    - `action` (string, enum: `["list_iterations", "list_team_iterations", "get_team_settings", "get_team_capacity", "get_iteration_capacities"]`).
  - *Optional:*
    - `depth` (integer): Depth of iteration hierarchy.
    - `excludedIds` (array of strings): Work item IDs to exclude.
    - `iterationId` (string): GUID of the sprint iteration (required for capacity actions).
    - `project` (string): Target project name/ID.
    - `team` (string): Target team name/ID.
    - `timeframe` (string, enum: `["current", "past", "future"]`).
- **Invocation Payload Example:**
```json
{
  "action": "get_team_capacity",
  "project": "Arthemis",
  "team": "agent-squad Team",
  "iterationId": "7b8f9e01-2345-6789-abcd-ef0123456789"
}
```
- **SoD Policy:** Read-only telemetry. Callable by `squads@` (e.g., `01-product-manager`, `02-product-owner`) and `arthemis@` (`14-governance-auditor`).
- **Failure Modes & Error Handling:**
  - `Invalid iterationId`: Emits error code `-32602`. The agent must first invoke `work(action='list_team_iterations', timeframe='current')`.

#### 5. `work_iteration_write`
- **Objective:** Create project iterations (sprints) or assign iterations to team backlogs.
- **Parameters:**
  - *Required:*
    - `action` (string, enum: `["create", "assign"]`).
    - `iterations` (array of objects): List of iteration objects with `name`, `startDate`, `finishDate`.
    - `project` (string): Target project.
  - *Optional:*
    - `team` (string): Team name (required when `action == "assign"`).
- **Invocation Payload Example:**
```json
{
  "action": "create",
  "project": "Arthemis",
  "iterations": [
    {
      "name": "Sprint 24",
      "startDate": "2026-09-15T00:00:00Z",
      "finishDate": "2026-09-29T23:59:59Z"
    }
  ]
}
```
- **SoD Policy:** Restricted to Project Administration identities (`arthemis@` or `human_master`). Contributors (`squads@`) cannot mutate project-wide sprint cadences.
- **Failure Modes & Error Handling:**
  - Date overlap conflict returns HTTP 400. Agent must validate existing iterations before invoking.

#### 6. `work_capacity_write`
- **Objective:** Update developer capacity, activities (Development, Testing, Design), and planned days off for a sprint.
- **Parameters:**
  - *Required:*
    - `action` (string): Operation name (e.g. `update`).
    - `activities` (array of objects): `[{"name": "Development", "capacityPerDay": 6.0}]`.
    - `iterationId` (string): Sprint GUID.
    - `project` (string): Target project.
    - `team` (string): Target team.
    - `teamMemberId` (string): Identity GUID of the team member.
  - *Optional:*
    - `daysOff` (array of objects): Specific date ranges for planned leave.
- **Invocation Payload Example:**
```json
{
  "action": "update",
  "project": "Arthemis",
  "team": "agent-squad Team",
  "iterationId": "7b8f9e01-2345-6789-abcd-ef0123456789",
  "teamMemberId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "activities": [
    {
      "name": "Development",
      "capacityPerDay": 6.0
    }
  ],
  "daysOff": []
}
```
- **SoD Policy:** Managed by `02-product-owner` or `14-governance-auditor` under `arthemis@` to enforce realistic WIP limits (max 8 Story Points per persona).
- **Failure Modes & Error Handling:**
  - Capacity updates fail if member GUID is invalid. Re-query using `core_get_identity_ids`.

---

### 1.4 Group 3: Build & Release Pipelines (6 Tools)

#### 7. `pipelines_definition`
- **Objective:** List pipeline definitions, retrieve pipeline YAML file paths, and inspect revision history.
- **Parameters:**
  - *Required:*
    - `action` (string, enum: `["list", "list_revisions"]`).
    - `project` (string): Target project.
  - *Optional:*
    - `builtAfter`, `notBuiltAfter` (string): Date filters.
    - `continuationToken` (string).
    - `definitionId` (integer): Target definition ID (required for `list_revisions`).
    - `definitionIds` (string): Comma-separated definition IDs.
    - `includeAllProperties`, `includeLatestBuilds` (boolean).
    - `minMetricsTime` (string).
    - `name` (string): Filter by pipeline name.
    - `path` (string): Folder path filter.
    - `processType` (integer).
    - `queryOrder` (string).
    - `repositoryId`, `repositoryType` (string).
    - `taskIdFilter` (string).
    - `top` (integer).
    - `yamlFilename` (string): Filter by YAML file name.
- **Invocation Payload Example:**
```json
{
  "action": "list",
  "project": "Arthemis",
  "name": "agent-squad-ci",
  "includeLatestBuilds": true
}
```
- **SoD Policy:** Read-only inspection. Open to all personas.
- **Failure Modes & Error Handling:**
  - Empty results when pipeline is located in a subfolder; omit `name` and filter programmatically.

#### 8. `pipelines_build`
- **Objective:** List builds, query real-time build execution status, and inspect source commit changes.
- **Parameters:**
  - *Required:*
    - `action` (string, enum: `["list", "get_status", "get_changes"]`).
    - `project` (string): Project name/ID.
  - *Optional:*
    - `branchName` (string): e.g. `refs/heads/main` or feature branch.
    - `buildId` (integer): Required for `get_status` and `get_changes`.
    - `buildIds` (string): Comma-separated IDs.
    - `buildNumber` (string).
    - `continuationToken` (string).
    - `definitions` (string): Comma-separated pipeline definition IDs.
    - `deletedFilter` (string).
    - `includeSourceChange` (boolean).
    - `maxBuildsPerDefinition` (integer).
    - `maxTime`, `minTime` (string).
    - `properties` (string).
    - `queryOrder` (string).
    - `queues` (string).
    - `reasonFilter` (string).
    - `repositoryId`, `repositoryType` (string).
    - `requestedFor` (string).
    - `resultFilter` (string, enum: `["failed", "succeeded", "partiallySucceeded", "canceled"]`).
    - `statusFilter` (string, enum: `["all", "cancelling", "completed", "inProgress", "notStarted", "postponed"]`).
    - `tagFilters` (string).
    - `top` (integer).
- **Invocation Payload Example:**
```json
{
  "action": "get_status",
  "project": "Arthemis",
  "buildId": 48201
}
```
- **SoD Policy:** Read-only. Monitored by `08-devops-engineer`, `09-qa-engineer`, and `05-software-engineer`.
- **Failure Modes & Error Handling:**
  - Build ID not found (HTTP 404): Handle deleted or expired builds gracefully.

#### 9. `pipelines_build_log`
- **Objective:** Fetch and parse containerized build logs and test execution step outputs to extract failure traces.
- **Parameters:**
  - *Required:*
    - `action` (string, enum: `["list", "get_content"]`).
    - `buildId` (integer): Build identifier.
    - `project` (string): Target project.
  - *Optional:*
    - `endLine` (integer): Ending line for pagination.
    - `logId` (integer): Specific log file ID (required for `get_content`).
    - `startLine` (integer): Starting line (1-indexed).
- **Invocation Payload Example:**
```json
{
  "action": "get_content",
  "project": "Arthemis",
  "buildId": 48201,
  "logId": 14,
  "startLine": 1,
  "endLine": 500
}
```
- **SoD Policy:** Read-only inspection. Critical for `09-qa-engineer` and `05-software-engineer` during root-cause analysis.
- **Failure Modes & Error Handling:**
  - Truncated output: Respect byte offset limits; page through logs using `startLine` and `endLine`.

#### 10. `pipelines_run`
- **Objective:** Query pipeline execution runs and list execution history.
- **Parameters:**
  - *Required:*
    - `action` (string, enum: `["get", "list"]`).
    - `pipelineId` (integer): Pipeline definition ID.
    - `project` (string): Project name.
  - *Optional:*
    - `runId` (integer): Specific run identifier (required for `get`).
- **Invocation Payload Example:**
```json
{
  "action": "get",
  "project": "Arthemis",
  "pipelineId": 12,
  "runId": 1045
}
```
- **SoD Policy:** Read-only query.
- **Failure Modes & Error Handling:**
  - Invalid runId returns error; retry with `action='list'` to retrieve latest active run ID.

#### 11. `pipelines_artifact`
- **Objective:** List build drop artifacts and download build outputs, test result packages, or binary packages.
- **Parameters:**
  - *Required:*
    - `action` (string, enum: `["list", "download"]`).
    - `buildId` (integer): Build identifier.
    - `project` (string): Target project.
  - *Optional:*
    - `artifactName` (string): Name of the artifact (e.g. `drop`, `coverage-report`).
    - `destinationPath` (string): Local directory destination for download.
- **Invocation Payload Example:**
```json
{
  "action": "download",
  "project": "Arthemis",
  "buildId": 48201,
  "artifactName": "coverage-report",
  "destinationPath": "work/agent_squad/reports/"
}
```
- **SoD Policy:** Artifact retrieval is open to contributor and audit identities.
- **Failure Modes & Error Handling:**
  - If artifact has expired (retention policy), report gracefully without failing the entire gate evaluation.

#### 12. `pipelines_write`
- **Objective:** Trigger new pipeline runs, create/rename pipelines, or update stages (retry failed jobs).
- **Parameters:**
  - *Required:*
    - `action` (string, enum: `["run_pipeline", "create_pipeline", "rename_pipeline", "update_build_stage"]`).
    - `project` (string): Target project.
  - *Optional:*
    - `buildId` (integer): Required for `update_build_stage`.
    - `folder` (string).
    - `forceRetryAllJobs` (boolean).
    - `name` (string): Pipeline name.
    - `pipelineId` (integer): Target pipeline ID for `run_pipeline` or `rename_pipeline`.
    - `pipelineVersion` (integer).
    - `previewRun` (boolean): If true, validates YAML without scheduling.
    - `repositoryConnectionId`, `repositoryId`, `repositoryName`, `repositoryType` (string).
    - `resources` (object): Pipeline resource triggers (repositories, pipelines).
    - `stageName` (string): Target stage name for retry/update.
    - `stagesToSkip` (array of strings).
    - `status` (string, enum: `["retry", "cancel"]`).
    - `templateParameters` (object): YAML runtime parameters.
    - `variables` (object): Pipeline runtime variable overrides.
    - `yamlOverride` (string): Inline YAML override (preview mode only).
    - `yamlPath` (string): Path to YAML definition in repo.
- **Invocation Payload Example:**
```json
{
  "action": "run_pipeline",
  "project": "Arthemis",
  "pipelineId": 12,
  "templateParameters": {
    "targetBranch": "feat/US-401-mcp-integration",
    "runIntegrationTests": "true"
  }
}
```
- **SoD Policy:**
  - `run_pipeline`: Executable by `squads@` (`08-devops-engineer`, `05-software-engineer`) on non-production branches.
  - Production deployments and pipeline definition mutation (`create_pipeline`, `rename_pipeline`) REQUIRE human approval (`human_master`) or `arthemis@` (`14-governance-auditor`).
- **Failure Modes & Error Handling:**
  - Parameter validation error (HTTP 400): Log exact YAML parser error and verify template parameter schema before retrying.

---

### 1.5 Group 4: Repositories, Branches & Pull Requests (9 Tools)

#### 13. `repo_repository`
- **Objective:** Discover repositories within the project and retrieve clone URLs, default branches, and repository IDs.
- **Parameters:**
  - *Required:*
    - `action` (string, enum: `["get", "list"]`).
  - *Optional:*
    - `project` (string): Project name/ID.
    - `repoNameFilter` (string): Filter by repository name.
    - `repositoryNameOrId` (string): Target repository identifier (required for `get`).
    - `skip`, `top` (integer).
- **Invocation Payload Example:**
```json
{
  "action": "get",
  "project": "Arthemis",
  "repositoryNameOrId": "agent-squad"
}
```
- **SoD Policy:** Read-only repository metadata discovery.
- **Failure Modes & Error Handling:**
  - 404 if repository does not exist; verify repository provisioning in project setup ledger.

#### 14. `repo_create_branch`
- **Objective:** Create a feature, fix, or release branch from a specified source branch or commit SHA.
- **Parameters:**
  - *Required:*
    - `branchName` (string): Name of the new branch (e.g. `feat/US-401-deep-mcp`).
    - `repositoryId` (string): Name or GUID of repository.
  - *Optional:*
    - `project` (string): Target project.
    - `sourceBranchName` (string): Base branch (default: default branch, e.g. `main`).
    - `sourceCommitId` (string): Specific base commit SHA.
- **Invocation Payload Example:**
```json
{
  "project": "Arthemis",
  "repositoryId": "agent-squad",
  "branchName": "feat/US-401-deep-mcp",
  "sourceBranchName": "main"
}
```
- **SoD Policy:** Executed by `development_team` (`squads@`). Branches MUST follow conventional naming: `feat/*`, `fix/*`, `chore/*`, `docs/*`. Direct branch creation on protected branches (`main`, `release/*`) is blocked by Azure DevOps branch policies.
- **Failure Modes & Error Handling:**
  - `Branch already exists`: Returns HTTP 400. The agent must inspect branch existence with `repo_branch` before creating.

#### 15. `repo_branch`
- **Objective:** Inspect branch status, ahead/behind commit counts, and verify branch existence.
- **Parameters:**
  - *Required:*
    - `action` (string, enum: `["get", "list", "list_mine"]`).
    - `repositoryId` (string): Repository identifier.
  - *Optional:*
    - `branchName` (string): Target branch name (required for `get`).
    - `filterContains` (string): Substring filter for branch listing.
    - `project` (string): Target project.
    - `top` (integer).
- **Invocation Payload Example:**
```json
{
  "action": "get",
  "project": "Arthemis",
  "repositoryId": "agent-squad",
  "branchName": "feat/US-401-deep-mcp"
}
```
- **SoD Policy:** Read-only inspection.
- **Failure Modes & Error Handling:**
  - Branch not found returns error; verify refs prefix formatting (`refs/heads/` is handled automatically or explicitly).

#### 16. `repo_file`
- **Objective:** Read file contents or list directory structures directly from the Git repository tree at a specific revision.
- **Parameters:**
  - *Required:*
    - `action` (string, enum: `["get_content", "list_directory"]`).
    - `repositoryId` (string): Repository identifier.
  - *Optional:*
    - `path` (string): File or folder path (e.g. `/contracts/handoff.schema.json`).
    - `project` (string): Target project.
    - `recursionDepth` (integer).
    - `recursive` (boolean): Recursively list directory contents.
    - `version` (string): Branch name, commit SHA, or tag name.
    - `versionType` (string, enum: `["branch", "commit", "tag"]`).
- **Invocation Payload Example:**
```json
{
  "action": "get_content",
  "project": "Arthemis",
  "repositoryId": "agent-squad",
  "path": "/templates/devops.yaml",
  "version": "main",
  "versionType": "branch"
}
```
- **SoD Policy:** Read-only inspection. Open to all personas.
- **Failure Modes & Error Handling:**
  - Binary file detection: Tool emits content in supported formats or rejects large binaries. Check size before parsing.

#### 17. `repo_search_commits`
- **Objective:** Search Git commit history across branches, authors, and date ranges.
- **Parameters:**
  - *Required:*
    - `searchText` (string): Commit message search query.
  - *Optional:*
    - `author` (string): Author name or email.
    - `branch` (string): Branch filter.
    - `commitEndDate`, `commitStartDate` (string): ISO-8601 date filters.
    - `includeFacets` (boolean).
    - `orderBy` (string).
    - `project` (string): Target project.
    - `repository` (string): Repository filter.
    - `skip`, `top` (integer).
- **Invocation Payload Example:**
```json
{
  "searchText": "US-401",
  "project": "Arthemis",
  "repository": "agent-squad",
  "top": 10
}
```
- **SoD Policy:** Read-only commit traceability.
- **Failure Modes & Error Handling:**
  - Search indexing delay: Newly pushed commits may take up to 60 seconds to index. Fall back to branch query if immediate verification is needed.

#### 18. `repo_pull_request`
- **Objective:** Query Pull Requests, filter by author, reviewer, target branch, and retrieve changed files and linked work items.
- **Parameters:**
  - *Required:*
    - `action` (string, enum: `["get", "list", "list_by_commits"]`).
  - *Optional:*
    - `commits` (array of strings): Commit IDs for `list_by_commits`.
    - `created_by_me` (boolean).
    - `created_by_user` (string): User GUID.
    - `i_am_reviewer` (boolean).
    - `includeChangedFiles` (boolean): Include list of modified file paths.
    - `includeLabels`, `includeWorkItemRefs` (boolean).
    - `project` (string): Target project.
    - `pullRequestId` (integer): PR ID (required for `get`).
    - `queryType` (string).
    - `repository`, `repositoryId` (string).
    - `skip` (integer).
    - `sourceRefName`, `targetRefName` (string): e.g. `refs/heads/feat/US-401`.
    - `status` (string, enum: `["all", "abandoned", "active", "completed"]`).
    - `top` (integer).
    - `user_is_reviewer` (string): User GUID filter.
- **Invocation Payload Example:**
```json
{
  "action": "get",
  "project": "Arthemis",
  "repositoryId": "agent-squad",
  "pullRequestId": 134,
  "includeChangedFiles": true,
  "includeWorkItemRefs": true
}
```
- **SoD Policy:** Read-only inspection.
- **Failure Modes & Error Handling:**
  - 404 if PR was abandoned or invalid ID; verify ID against active PR listing.

#### 19. `repo_pull_request_write`
- **Objective:** Create Pull Requests, update titles/descriptions, attach work items, configure auto-complete, manage required reviewers, and submit approval votes.
- **Parameters:**
  - *Required:*
    - `action` (string, enum: `["create", "update", "update_reviewers", "vote"]`).
  - *Optional:*
    - `autoComplete` (boolean): Enable auto-completion upon policy satisfaction.
    - `bypassPolicy` (boolean): STRICTLY PROHIBITED in automated squad flow.
    - `bypassReason` (string).
    - `deleteSourceBranch` (boolean): Auto-delete branch after merge.
    - `description` (string): PR Markdown body.
    - `forkSourceRepositoryId` (string).
    - `isDraft` (boolean).
    - `labels` (array of strings).
    - `mergeCommitMessage` (string).
    - `mergeStrategy` (string, enum: `["noFastForward", "squash", "rebase", "rebaseMerge"]`). Default: `squash`.
    - `project` (string): Target project.
    - `pullRequestId` (integer): PR identifier (required for update, vote, update_reviewers).
    - `repositoryId` (string): Repository name or GUID.
    - `reviewerAction` (string, enum: `["add", "remove"]`).
    - `reviewerIds` (array of strings): Identity GUIDs to add/remove as reviewers.
    - `sourceRefName` (string): Source branch (e.g. `refs/heads/feat/US-401`).
    - `status` (string, enum: `["active", "abandoned", "completed"]`).
    - `targetRefName` (string): Target branch (e.g. `refs/heads/main`).
    - `title` (string): PR title (e.g. `feat(mcp): implement deep azure-devops skill`).
    - `transitionWorkItems` (boolean).
    - `vote` (integer, enum: `[10, 5, 0, -5, -10]` representing Approved, Approved with suggestions, No vote, Waiting for author, Rejected).
    - `workItems` (array of strings): Work item IDs to link to PR.
- **Invocation Payload Examples:**

*1. PR Creation by Author (`squads@`):*
```json
{
  "action": "create",
  "project": "Arthemis",
  "repositoryId": "agent-squad",
  "sourceRefName": "refs/heads/feat/US-401-deep-mcp",
  "targetRefName": "refs/heads/main",
  "title": "feat(mcp): complete 40-tool azure-devops operational skill",
  "description": "## Objective\nExhaustive architectural mapping.\n\n## Verification\n11/11 tests passing.\nCloses #401",
  "workItems": ["401"],
  "deleteSourceBranch": true,
  "mergeStrategy": "squash"
}
```

*2. PR Approval Vote by Reviewer (`arthemis@`):*
```json
{
  "action": "vote",
  "project": "Arthemis",
  "repositoryId": "agent-squad",
  "pullRequestId": 134,
  "vote": 10
}
```

*3. Dual Sign-off Security Review Vote by Red Team (`cyber_red@`):*
```json
{
  "action": "vote",
  "project": "Arthemis",
  "repositoryId": "agent-squad",
  "pullRequestId": 134,
  "vote": 10
}
```

- **SoD Policy Enforcement (CRITICAL):**
  - `action == "create"`: MUST be executed under `squads@` (Contributors).
  - `action == "vote"`: MUST NEVER be invoked by the author account (`squads@`). Any attempt by `squads@` to vote on its own PR is an ISO 27001 / SOC 2 violation and is blocked.
  - Standard PRs require vote = 10 from `arthemis@` (`code-reviewer`, `qa-engineer`).
  - Sensitive paths (`/auth/`, `/crypto/`, `/iac/`, `*.tf`, `Dockerfile`): Azure DevOps branch policy requires **two distinct account approvals**: `arthemis@` (`security-reviewer`) AND `cyber_red@` (`offensive-cyber-operator`).
- **Failure Modes & Error Handling:**
  - Merge conflict: Status indicates conflict; agent reports blocked state and delegates back to `05-software-engineer`.

#### 20. `repo_pull_request_thread`
- **Objective:** List discussion threads and comments on a PR to audit reviews, blockers, and sign-offs.
- **Parameters:**
  - *Required:*
    - `action` (string, enum: `["list", "list_comments"]`).
    - `pullRequestId` (integer): PR ID.
    - `repositoryId` (string): Repository ID.
  - *Optional:*
    - `authorDisplayName`, `authorEmail` (string).
    - `baseIteration`, `iteration` (integer).
    - `fullResponse` (boolean).
    - `project` (string): Target project.
    - `skip` (integer).
    - `status` (string, enum: `["unknown", "active", "fixed", "wontFix", "closed", "byDesign", "pending"]`).
    - `threadId` (integer): Required for `list_comments`.
    - `top` (integer).
- **Invocation Payload Example:**
```json
{
  "action": "list",
  "project": "Arthemis",
  "repositoryId": "agent-squad",
  "pullRequestId": 134,
  "status": "active"
}
```
- **SoD Policy:** Read-only inspection used by `pr_governance.py` to audit approval tags.
- **Failure Modes & Error Handling:**
  - Empty threads list indicates clean PR with no active review remarks.

#### 21. `repo_pull_request_thread_write`
- **Objective:** Post formal review comments, inline code review suggestions, reply to existing threads, or resolve threads (`status: fixed`).
- **Parameters:**
  - *Required:*
    - `action` (string, enum: `["create", "reply", "update", "update_status"]`).
    - `pullRequestId` (integer): PR ID.
    - `repositoryId` (string): Repository ID.
  - *Optional:*
    - `changeTrackingId` (integer).
    - `commentId` (integer): Target comment ID for `update`.
    - `content` (string): Markdown comment body (must include persona tag, e.g. `[10-security-reviewer] approve`).
    - `filePath` (string): Path to file for inline code discussion.
    - `firstComparingIteration`, `secondComparingIteration` (integer).
    - `fullResponse` (boolean).
    - `project` (string): Target project.
    - `rightFileEndLine`, `rightFileEndOffset`, `rightFileStartLine`, `rightFileStartOffset` (integer): Code coordinate range for inline comments.
    - `status` (string, enum: `["active", "fixed", "wontFix", "closed", "byDesign", "pending"]`).
    - `threadId` (integer): Required for `reply`, `update`, and `update_status`.
- **Invocation Payload Example:**
```json
{
  "action": "create",
  "project": "Arthemis",
  "repositoryId": "agent-squad",
  "pullRequestId": 134,
  "content": "[10-security-reviewer] approve: verified zero token leakage in mcp_server.py; STRIDE threat model mitigated.",
  "status": "closed"
}
```
- **SoD Policy:**
  - Review personas (`code-reviewer`, `security-reviewer`, `qa-engineer`) post from `arthemis@`.
  - Red Team (`offensive-cyber-operator`) posts from `cyber_red@`.
  - Author (`squads@`) can only reply or acknowledge (`[05-software-engineer] acknowledge: fixed in commit abc1234`).
- **Failure Modes & Error Handling:**
  - Updating a thread without `threadId` fails with `-32602`. Must fetch thread ID with `repo_pull_request_thread`.

---

### 1.6 Group 5: Work Items / WIT (7 Tools)

#### 22. `wit_work_item`
- **Objective:** Query work item details, expand relations/links, list revisions, inspect comments, or retrieve work items assigned to the current user or iteration.
- **Parameters:**
  - *Required:*
    - `action` (string, enum: `["get", "get_batch", "list_comments", "my", "list_revisions", "list_for_iteration", "get_type"]`).
  - *Optional:*
    - `asOf` (string): Historical timestamp for point-in-time query.
    - `expand` (string, enum: `["all", "fields", "links", "none", "relations"]`).
    - `fields` (string): Comma-separated list of field reference names to retrieve.
    - `id` (integer): Work item ID (for `get`, `list_comments`, `list_revisions`).
    - `ids` (string): Comma-separated work item IDs for `get_batch`.
    - `includeCompleted` (boolean).
    - `iterationId` (string): Sprint ID for `list_for_iteration`.
    - `project` (string): Target project.
    - `skip`, `top` (integer).
    - `team` (string).
    - `type` (string): Work item type name (for `get_type`).
    - `workItemId` (integer).
    - `workItemType` (string).
- **Invocation Payload Example:**
```json
{
  "action": "get",
  "project": "Arthemis",
  "id": 401,
  "expand": "all"
}
```
- **SoD Policy:** Read-only inspection open to all personas.
- **Failure Modes & Error Handling:**
  - 404 Work Item Not Found: Agent emits `ITEM_NOT_FOUND` and checks ledger for valid ID.

#### 23. `wit_query`
- **Objective:** Execute WIQL (Work Item Query Language) queries or run saved flat/hierarchical queries.
- **Parameters:**
  - *Required:*
    - `action` (string, enum: `["get", "get_results", "wiql"]`).
  - *Optional:*
    - `depth` (integer).
    - `expand` (string).
    - `id` (string): Saved query GUID.
    - `includeDeleted` (boolean).
    - `project` (string): Target project.
    - `query` (string): Folder path or name of saved query.
    - `responseType` (string).
    - `team` (string).
    - `timePrecision` (boolean).
    - `top` (integer).
    - `useIsoDateFormat` (boolean).
    - `wiql` (string): Raw WIQL query text (required when `action == "wiql"`).
- **Invocation Payload Example:**
```json
{
  "action": "wiql",
  "project": "Arthemis",
  "wiql": "SELECT [System.Id], [System.Title], [System.State] FROM WorkItems WHERE [System.TeamProject] = 'Arthemis' AND [System.WorkItemType] = 'User Story' AND [System.State] <> 'Closed' ORDER BY [System.ChangedDate] DESC",
  "top": 20
}
```
- **SoD Policy:** Read-only query execution.
- **Failure Modes & Error Handling:**
  - Syntax error in WIQL (HTTP 400): Ensure field names use bracket notation `[System.Field]`.

#### 24. `wit_backlog`
- **Objective:** Retrieve product, sprint, or feature backlogs and reorder work item priorities.
- **Parameters:**
  - *Required:*
    - `action` (string, enum: `["list", "list_work_items", "reorder"]`).
  - *Optional:*
    - `backlogId` (string): Backlog level identifier (e.g. `Stories`, `Epics`).
    - `ids` (array of integers): Work item IDs to reorder.
    - `iterationId`, `iterationPath` (string).
    - `nextId`, `previousId` (integer): Boundary work item IDs for reordering.
    - `parentId` (integer).
    - `project` (string): Target project.
    - `team` (string): Target team.
- **Invocation Payload Example:**
```json
{
  "action": "list_work_items",
  "project": "Arthemis",
  "team": "agent-squad Team",
  "backlogId": "Stories"
}
```
- **SoD Policy:** Backlog listing is open; reordering backlog priorities is reserved for `01-product-manager` and `02-product-owner` under `arthemis@`.
- **Failure Modes & Error Handling:**
  - Backlog category not found: Query `action='list'` to retrieve active backlog category keys.

#### 25. `wit_work_item_attachment`
- **Objective:** Download attachments (specifications, architecture diagrams, test runs) linked to work items.
- **Parameters:**
  - *Required:*
    - `attachmentId` (string): Attachment GUID.
  - *Optional:*
    - `fileName` (string): File name to save locally.
    - `project` (string): Target project.
    - `savePath` (string): Local directory destination.
- **Invocation Payload Example:**
```json
{
  "attachmentId": "c4d5e6f7-8901-2345-6789-0123456789ab",
  "fileName": "architecture_diagram.png",
  "savePath": "work/agent_squad/artifacts/"
}
```
- **SoD Policy:** Read-only attachment retrieval.
- **Failure Modes & Error Handling:**
  - Save path must be within validated workspace boundaries to prevent directory traversal.

#### 26. `wit_work_item_write`
- **Objective:** Create new work items, update field values (State, Title, Acceptance Criteria, Story Points), perform batch updates, or attach child items.
- **Parameters:**
  - *Required:*
    - `action` (string, enum: `["create", "update", "update_batch", "add_child"]`).
  - *Optional:*
    - `batchUpdates` (array of objects): For batch operations.
    - `fields` (object): Key-value pairs of field reference names and values (e.g. `{"System.Title": "...", "System.State": "Active"}`).
    - `id` (integer): Target work item ID for update.
    - `items` (array of objects).
    - `parentId` (integer): Parent work item ID for `add_child`.
    - `project` (string): Target project.
    - `updates` (array of JSON Patch objects).
    - `workItemType` (string): e.g. `User Story`, `Task`, `Bug`, `Epic` (required for `create`).
- **Invocation Payload Example:**
```json
{
  "action": "update",
  "project": "Arthemis",
  "id": 401,
  "fields": {
    "System.State": "Active",
    "Microsoft.VSTS.Scheduling.StoryPoints": 5,
    "System.Description": "Detailed implementation of deep MCP operational skills."
  }
}
```
- **SoD Policy Enforcement:**
  - `squads@` (Contributors): Allowed to create tasks, move items to `Active` or `Resolved`, and update technical fields.
  - Transition to `Closed` (Gate G6): STRICTLY RESTRICTED to `arthemis@` (`14-governance-auditor`) upon verification of delivery ledger and G6 gate decision YAML. `squads@` is blocked from closing cards.
- **Failure Modes & Error Handling:**
  - Invalid state transition (e.g. `New` -> `Closed` skipping required flow): Emits HTTP 400. Agent inspects process template state rules.

#### 27. `wit_work_item_comment_write`
- **Objective:** Post formal progress updates, audit findings, gate verdicts, and handoff notices to work item discussions.
- **Parameters:**
  - *Required:*
    - `action` (string, enum: `["add", "update"]`).
  - *Optional:*
    - `commentId` (integer): Required for `update`.
    - `format` (string, enum: `["markdown", "html"]`). Default: `markdown`.
    - `project` (string): Target project.
    - `text` (string): Comment content (required).
    - `workItemId` (integer): Target work item ID.
- **Invocation Payload Example:**
```json
{
  "action": "add",
  "project": "Arthemis",
  "workItemId": 401,
  "text": "### Gate G2 (Design) Evaluated\n**Status:** APPROVED\n**Architect:** Martin Fowler & Gregor Hohpe (04-solution-architect)\n**Artifacts:** specs/architecture.md, adr/ADR-0001.md\n**Evidence:** Zero schema mismatch verified."
}
```
- **SoD Policy:** All agents can post commentary matching their persona tag `[NN-persona-id]`.
- **Failure Modes & Error Handling:**
  - Exceeding character limit (Azure DevOps WIT comments limit is 262,144 chars). Ensure comments are structured summaries linking to repository markdown artifacts.

#### 28. `wit_work_item_link_write`
- **Objective:** Establish formal traceability links between work items, commits, branches, PRs, builds, and wiki pages.
- **Parameters:**
  - *Required:*
    - `action` (string, enum: `["link", "unlink", "link_to_pull_request", "add_artifact_link"]`).
  - *Optional:*
    - `artifactUri` (string): e.g. `vstfs:///Git/Ref/...`.
    - `branchName` (string).
    - `buildId` (integer).
    - `comment` (string): Description of the relationship.
    - `commitId` (string).
    - `id` (integer): Source work item ID.
    - `linkType` (string, enum: `["Parent", "Child", "Related", "Duplicate", "Predecessor", "Successor"]`).
    - `pageId`, `pagePath`, `wikiId` (string/integer).
    - `project`, `projectId` (string).
    - `pullRequestId` (integer).
    - `pullRequestProjectId` (string).
    - `repositoryId` (string).
    - `type` (string).
    - `updates` (array of objects).
    - `url` (string): External hyperlink URL.
    - `workItemId` (integer): Target work item ID.
- **Invocation Payload Example:**
```json
{
  "action": "link_to_pull_request",
  "project": "Arthemis",
  "id": 401,
  "pullRequestId": 134,
  "repositoryId": "agent-squad",
  "comment": "Implementation PR for US-401 deep MCP skill"
}
```
- **SoD Policy:** Callable by contributor (`squads@`) or governance (`arthemis@`) to establish bi-directional traceability.
- **Failure Modes & Error Handling:**
  - Link already exists: Ignore idempotent error code or log warning.

---

### 1.7 Group 6: Wiki Documentation (2 Tools)

#### 29. `wiki`
- **Objective:** Discover project wikis, list page hierarchies, retrieve page metadata, and extract markdown page content.
- **Parameters:**
  - *Required:*
    - `action` (string, enum: `["list_wikis", "get_wiki", "list_pages", "get_page", "get_page_content"]`).
  - *Optional:*
    - `continuationToken` (string).
    - `pageViewsForDays` (integer).
    - `path` (string): Wiki page path (e.g. `/Architecture/MCP-Integration`).
    - `project` (string): Target project.
    - `recursionLevel` (string, enum: `["full", "oneLevel", "none"]`).
    - `top` (integer).
    - `url` (string).
    - `wikiIdentifier` (string): Wiki name or GUID.
- **Invocation Payload Example:**
```json
{
  "action": "get_page_content",
  "project": "Arthemis",
  "wikiIdentifier": "Arthemis.wiki",
  "path": "/Architecture/Security-Boundaries"
}
```
- **SoD Policy:** Read-only inspection open to all personas.
- **Failure Modes & Error Handling:**
  - 404 Page Not Found: Ensure leading slash `/` is included in `path`.

#### 30. `wiki_upsert_page`
- **Objective:** Create or overwrite wiki documentation pages to publish human-readable architecture blueprints and audit reports.
- **Parameters:**
  - *Required:*
    - `content` (string): Markdown content of the page.
    - `path` (string): Absolute wiki path (e.g. `/Specifications/MCP-Blueprint`).
    - `wikiIdentifier` (string): Target wiki identifier.
  - *Optional:*
    - `branch` (string): Wiki Git branch (default: `wikiMaster`).
    - `etag` (string): Optimistic locking concurrency ETag.
    - `project` (string): Target project.
- **Invocation Payload Example:**
```json
{
  "project": "Arthemis",
  "wikiIdentifier": "Arthemis.wiki",
  "path": "/Architecture/Deep-MCP-Blueprint",
  "content": "# Deep MCP Blueprint\nCanonical architecture documentation published by 04-solution-architect."
}
```
- **SoD Policy:** Executed by `04-solution-architect` or `12-technical-writer` under `squads@`. Publishing compliance manuals or governance baselines requires `arthemis@` sign-off.
- **Failure Modes & Error Handling:**
  - Concurrency conflict (ETag mismatch): Fetch latest page version using `wiki(action='get_page')`, merge updates, and re-publish.

---

### 1.8 Group 7: Test Management & Quality Gates (5 Tools)

#### 31. `testplan`
- **Objective:** Enumerate test plans, query test suites, and list test cases mapped to project features.
- **Parameters:**
  - *Required:*
    - `action` (string, enum: `["list_plans", "list_suites", "list_cases"]`).
    - `project` (string): Target project.
  - *Optional:*
    - `continuationToken` (string).
    - `filterActivePlans` (boolean): Filter active vs closed test plans.
    - `includePlanDetails` (boolean).
    - `planId` (integer): Test plan ID (required for `list_suites` and `list_cases`).
    - `suiteId` (integer): Test suite ID (required for `list_cases`).
- **Invocation Payload Example:**
```json
{
  "action": "list_cases",
  "project": "Arthemis",
  "planId": 501,
  "suiteId": 502
}
```
- **SoD Policy:** Read-only test plan inspection.
- **Failure Modes & Error Handling:**
  - Plan or suite not found returns error; verify IDs with `list_plans`.

#### 32. `testplan_show_test_results_from_build_id`
- **Objective:** Extract test run outcomes, execution duration, and stack traces associated with a specific CI build ID.
- **Parameters:**
  - *Required:*
    - `buildid` (integer): Build execution identifier.
    - `project` (string): Target project.
  - *Optional:*
    - `outcomes` (string): Comma-separated outcomes to filter (e.g. `Failed`, `Passed`, `Inconclusive`).
- **Invocation Payload Example:**
```json
{
  "project": "Arthemis",
  "buildid": 48201,
  "outcomes": "Failed"
}
```
- **SoD Policy:** Read-only test results extraction. Core gate input for `09-qa-engineer` during G4 evaluation.
- **Failure Modes & Error Handling:**
  - If no tests were run in build, returns empty array; ensure pipeline includes test reporting task.

#### 33. `testplan_test_plan_write`
- **Objective:** Create formal test plans mapped to iterations and area paths.
- **Parameters:**
  - *Required:*
    - `action` (string, enum: `["create"]`).
    - `project` (string): Target project.
  - *Optional:*
    - `areaPath` (string).
    - `description` (string).
    - `endDate`, `startDate` (string): ISO-8601 date range.
    - `iteration` (string): Iteration path (e.g. `Arthemis\Sprint 24`).
    - `name` (string): Name of test plan.
- **Invocation Payload Example:**
```json
{
  "action": "create",
  "project": "Arthemis",
  "name": "Sprint 24 - Deep MCP Integration Test Plan",
  "iteration": "Arthemis\Sprint 24",
  "startDate": "2026-09-15T00:00:00Z",
  "endDate": "2026-09-29T23:59:59Z"
}
```
- **SoD Policy:** Managed by `09-qa-engineer` or `14-governance-auditor`.
- **Failure Modes & Error Handling:**
  - Iteration path mismatch: Validate iteration path format against `work(action='list_iterations')`.

#### 34. `testplan_test_suite_write`
- **Objective:** Create static, requirement-based, or query-based test suites and bind test cases to them.
- **Parameters:**
  - *Required:*
    - `action` (string, enum: `["create", "add_test_cases"]`).
    - `project` (string): Target project.
  - *Optional:*
    - `name` (string): Name of test suite.
    - `parentSuiteId` (integer): Parent suite ID (root suite if omitted).
    - `planId` (integer): Test plan ID.
    - `suiteId` (integer): Target suite ID for `add_test_cases`.
    - `testCaseIds` (array of integers): Work item IDs of test cases.
- **Invocation Payload Example:**
```json
{
  "action": "add_test_cases",
  "project": "Arthemis",
  "planId": 501,
  "suiteId": 502,
  "testCaseIds": [601, 602, 603]
}
```
- **SoD Policy:** Executed by `09-qa-engineer` (`arthemis@` or `squads@`).
- **Failure Modes & Error Handling:**
  - Duplicate test case additions are ignored idempotently.

#### 35. `testplan_test_case_write`
- **Objective:** Author structured manual or automated test cases with step-by-step actions and expected results, linking them to parent stories.
- **Parameters:**
  - *Required:*
    - `action` (string, enum: `["create", "update_steps"]`).
  - *Optional:*
    - `areaPath`, `iterationPath` (string).
    - `id` (integer): Test case ID for `update_steps`.
    - `priority` (integer): Priority (1-4).
    - `project` (string): Target project.
    - `steps` (array of objects): `[{"action": "Call start_session", "expected": "Returns session_id and ttl"}]`.
    - `testsWorkItemId` (integer): Story work item ID to link.
    - `title` (string): Test case title.
- **Invocation Payload Example:**
```json
{
  "action": "create",
  "project": "Arthemis",
  "title": "TC-401-01: Verify agent-squad MCP session start and TTL expiration",
  "priority": 1,
  "testsWorkItemId": 401,
  "steps": [
    {
      "action": "Send JSON-RPC tools/call for start_session with valid host and work item.",
      "expected": "Status 200 OK, session_id UUID generated, status == 'active', TTL == 3600."
    },
    {
      "action": "Send tools/call with invalid project root path.",
      "expected": "JSON-RPC error code -32603 with message 'Project root does not exist'."
    }
  ]
}
```
- **SoD Policy:** Authored by `09-qa-engineer` (`squads@` or `arthemis@`).
- **Failure Modes & Error Handling:**
  - XML formatting of test steps: The MCP server handles step transformation into Azure DevOps test step XML internally.

---

### 1.9 Group 8: Semantic & Code Search (3 Tools)

#### 36. `search_code`
- **Objective:** Perform indexed code searches across repositories, branch refs, and file path filters.
- **Parameters:**
  - *Required:*
    - `searchText` (string): Search query string.
  - *Optional:*
    - `branch` (string): Branch filter.
    - `includeFacets` (boolean).
    - `path` (string): Path filter.
    - `project` (string): Target project.
    - `repository` (string): Repository filter.
    - `skip`, `top` (integer).
- **Invocation Payload Example:**
```json
{
  "searchText": "AgentSquadMCPServer",
  "project": "Arthemis",
  "repository": "agent-squad",
  "top": 5
}
```
- **SoD Policy:** Read-only code intelligence.
- **Failure Modes & Error Handling:**
  - Exact phrase vs tokenized search: Use quotes for exact string matching.

#### 37. `search_wiki`
- **Objective:** Search across all wiki pages in the organization for technical documentation, policies, and ADRs.
- **Parameters:**
  - *Required:*
    - `searchText` (string): Search terms.
  - *Optional:*
    - `includeFacets` (boolean).
    - `project` (string): Target project.
    - `skip`, `top` (integer).
    - `wiki` (string): Wiki name filter.
- **Invocation Payload Example:**
```json
{
  "searchText": "ADR-0001",
  "project": "Arthemis",
  "wiki": "Arthemis.wiki"
}
```
- **SoD Policy:** Read-only documentation search.
- **Failure Modes & Error Handling:**
  - Empty results if indexing is pending; fall back to `wiki(action='list_pages')`.

#### 38. `search_workitem`
- **Objective:** Full-text search across work item titles, descriptions, criteria, and discussion threads.
- **Parameters:**
  - *Required:*
    - `searchText` (string): Search terms.
  - *Optional:*
    - `areaPath`, `assignedTo` (string).
    - `includeFacets` (boolean).
    - `project` (string): Target project.
    - `skip`, `top` (integer).
    - `state` (string): Work item state filter.
    - `workItemType` (string): Type filter.
- **Invocation Payload Example:**
```json
{
  "searchText": "deep mcp",
  "project": "Arthemis",
  "state": "Active",
  "workItemType": "User Story"
}
```
- **SoD Policy:** Read-only discovery.
- **Failure Modes & Error Handling:**
  - Complex boolean logic supported (AND, OR, NOT).

---

### 1.10 Group 9: Advanced Security / AdvSec (2 Tools)

#### 39. `advsec_get_alerts`
- **Objective:** Retrieve repository vulnerability alerts including Secret Scanning leaks, CodeQL Static Analysis (SAST) findings, and Dependabot SCA alerts.
- **Parameters:**
  - *Required:*
    - `project` (string): Project name.
    - `repository` (string): Repository name.
  - *Optional:*
    - `alertType` (string, enum: `["codeql", "dependency", "secrets"]`).
    - `confidenceLevels` (string).
    - `continuationToken` (string).
    - `onlyDefaultBranch` (boolean).
    - `orderBy` (string).
    - `ref` (string): Branch ref filter (e.g. `refs/heads/main`).
    - `ruleId`, `ruleName` (string).
    - `severities` (string, comma-separated: `critical,high,medium,low`).
    - `states` (string, enum: `["active", "dismissed", "fixed"]`).
    - `toolName` (string).
    - `top` (integer).
    - `validity` (string).
- **Invocation Payload Example:**
```json
{
  "project": "Arthemis",
  "repository": "agent-squad",
  "states": "active",
  "severities": "critical,high",
  "onlyDefaultBranch": true
}
```
- **SoD Policy:** Inspected by `10-security-reviewer` (`arthemis@`) and `34-offensive-cyber-operator` (`cyber_red@`). Mandatory input for G3 and G5 security gates.
- **Failure Modes & Error Handling:**
  - Advanced Security not enabled on repository: Server returns HTTP 400 with feature disabled notice. Treat as policy blocker if repository hosts sensitive code.

#### 40. `advsec_get_alert_details`
- **Objective:** Retrieve deep forensic details of a specific security alert, including CWE identifiers, vulnerable line coordinates, remediation advice, and secret exposure timestamps.
- **Parameters:**
  - *Required:*
    - `alertId` (integer): Alert identifier.
    - `project` (string): Project name.
    - `repository` (string): Repository name.
  - *Optional:*
    - `ref` (string): Git branch reference.
- **Invocation Payload Example:**
```json
{
  "project": "Arthemis",
  "repository": "agent-squad",
  "alertId": 1082
}
```
- **SoD Policy:** Security auditor and red team evaluation (`arthemis@`, `cyber_red@`).
- **Failure Modes & Error Handling:**
  - Alert ID not found returns 404; check alert ID against `advsec_get_alerts`.
