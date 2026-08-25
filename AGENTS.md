# AGENTS.md

Preface: All development should be conducted in Simplified Chinese.

1. Think Before Coding
   - ambiguity, assumptions, alternatives, tradeoffs

2. Simplicity and Reachability
   - minimum implementation
   - no speculative abstractions/configuration
   - no speculative feature flags/compat/migrations/wrappers
   - handle unusual cases when reachable through supported use

3. Surgical Changes
   - smallest diff
   - no unrelated cleanup

4. Goal-Driven Verification
   - explicit success criteria
   - verify material criteria
   - checks must detect a specific failure and affect the next action
   - don't repeat settled checks

5. Project Threat Model / Anti-Overdefense
   - cooperating operator unless specified otherwise
   - no unnecessary security machinery
   - hashes/fingerprints only with material benefit
   - exercise judgment instead of procedural theater
   - report real problems, including rare-but-reachable ones
   - don't manufacture findings
   - higher-priority/user/project requirements override this section

6. Repository Automation Protocol
   - read `automation/README.md`, `automation/workflow.json`, and
     `automation/state.json` before starting an implementation stage
   - work only on the current ready stage and its reachable acceptance failures
   - use `dev` as the integration base and a short-lived `feat/<milestone>-<scope>`
     branch for implementation; never develop directly on `main`
   - subagents own disjoint paths and report evidence; the primary agent owns
     shared contracts, commits, pushes, merges, and state transitions
   - a stage becomes passed only after its acceptance commands ran successfully
     on the recorded commit and its evidence was saved; never infer success from
     documents, placeholders, or expected output
   - update implementation, tests, evidence, and `automation/state.json` as one
     coherent increment; stop at the human gates defined by the workflow
   - on failure, keep the stage active, record the failure, and make only the
     smallest change needed to pass that gate
