# Release evidence and remaining work

- Public repository verified: https://github.com/17rajsal/JudgeForge
- MIT license present; required architecture, data-model and judging docs present.
- Automated suite: 23 passing in the previous verified run.
- Docker image build and new volume/container creation: operator-provided terminal evidence.
- HTTP health: ok; official acceptance: all seven PASS on port 8000.
- Offline restart plus acceptance: operator confirmed Wi-Fi was disabled.
- Container internal health: exact output still to be captured.
- Original spec.md, run.py and fixtures.json must remain unchanged.
- T1/T2 are the current checker claims; broader product gaps are documented in README. No T3/T4 claim.
- Required demo video is still missing. DEMO-SCRIPT.md is a recording plan, not a replacement for the video.
- Final submission has not been sent. Submitter/team identity, recording URL and correct event submission form are still needed.

Final operator commands (run in the project folder):

```powershell
docker inspect --format '{{.State.Health.Status}}' judgeforge-app
python run.py .dogfood.toml | Out-File -Encoding utf8 acceptance-report.txt
git diff --check
git add README.md acceptance-report.txt DEMO-SCRIPT.md RELEASE-CHECKLIST.md
git commit -m "docs: record Docker runtime verification and release checklist"
git push origin master
```

Do not delete database volumes for packaging. Do not claim public result publication or other missing features in the demo.
