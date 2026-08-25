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