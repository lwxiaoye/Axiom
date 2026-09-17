# AXIOM repository guide

- This repository is the sanitized AXIOM campus-agent derivative. Its remote is https://github.com/lwxiaoye/Axiom.git.
- The nested original source directory and __MACOSX are local archives, not part of this repository. Never stage or package them.
- Use pnpm for the Vue 3 / Vite frontend; keep pnpm-lock.yaml. Python code is in agent-api/.
- Never commit real .env files, credentials, private certificates, user data, generated artifacts, or personal infrastructure addresses. Safe frontend defaults and *.example templates are the only environment files intended for Git.
- Preserve LICENSE and third-party notices, dependency package names, Java API contracts, existing authentication, and ACL checks.
- Campus Services is currently a read-only official-knowledge assistant. Do not claim it performs transactions or implements ten collaborating agents.
- Read docs/AXIOM-项目拆解与复用路线.md before campus changes. The upstream Harness architecture document remains useful for the execution contracts; historical deployment statements are not current verification evidence.
- Validate changes with focused frontend/backend tests and scripts/check_release.py. Do not connect tests to the archived deployment.
- Do not restart existing user services or run deployment commands unless the current task requires it.
