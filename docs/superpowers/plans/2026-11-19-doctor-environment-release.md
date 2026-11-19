# Redmine doctor environment recovery release plan

Goal: merge the verified local repair into official v0.18.3 and publish through the existing release workflow.

Scope: scripts/install-local-service.ps1, doctor diagnostics, Windows installer tests, recovery documentation, synchronized package manifests and locks, Windows CI coverage.

- [x] Add cross-shell encrypted environment regressions and observe the original failure.
- [x] Merge the local repair; preserve token/configuration/process/port guards and explicitly document stopped-service adoption for legacy manifests.
- [x] Cover corrupt snapshots, legacy manifests, adoption guards and live Node lifecycle recovery; run Python and Node checks plus PowerShell 5.1/7.
- [ ] Obtain independent review, address findings, synchronize versions to 0.18.3, and publish main/tag with passing GitHub Actions.

Review focus: no plaintext secrets, no unverified process termination, caller environment always restored, old manifests fail safely, configuration/token changes remain blocked.

Validation: Python 1053 passed / 3 platform skips; Windows lifecycle 17 passed; bidirectional PowerShell snapshot tests 4 passed. Independent review finding on empty strings fixed and re-reviewed. Release identity and diff checks passed.
