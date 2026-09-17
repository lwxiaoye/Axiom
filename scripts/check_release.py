"""Check Git-visible release files without printing sensitive values. Standard library only."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGAL_NAMES = {'LICENSE', 'NOTICE.md', 'NOTICE', 'COPYING', 'COPYRIGHT'}
PUBLIC_ENVS = {'.env', '.env.development', '.env.production'}
OLD_BRAND = re.compile('qing' + 'zhu|qz' + 'boot|cq' + 'jzc|青' + '竹|qzskin|\\bqza_|\\bqze_|\\bqze\\b|\\bqza\\b', re.I)
OLD_IP = re.compile(r'\b(?:10\.255\.57\.13|10\.8\.50\.35|192\.168\.5\.134|172\.16\.249\.181|172\.24\.137\.76)\b')
TOKEN = re.compile(r'(?<![\w-])(?:sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{24,}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)')
PRIVATE_KEY = re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')
CONFLICT = re.compile(r'^(?:<{7} |>{7} )', re.M)
AUTH_URL = re.compile(r'\w+(?:\+\w+)?://([^\s/@]*):([^\s/@]+)@')
PLACEHOLDERS = {'CHANGE_ME', 'test', 'pass', 'password', 'user', 'p', 'x', 'secret', 'pwd', 'postgres', 'url-encoded-password'}


def release_files() -> list[str]:
    result = subprocess.run(
        ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
        cwd=ROOT, check=True, capture_output=True,
    )
    return sorted(set(n for n in result.stdout.decode('utf-8').split('\0') if n))


def scan() -> dict:
    failures = []
    files = release_files()
    for name in files:
        path = ROOT / name
        if not path.is_file():
            continue
        parts = path.relative_to(ROOT).parts
        if any(p in {'__MACOSX', 'node_modules', '.venv', '.git', '__pycache__'} for p in parts) or parts[0].startswith('base-platform-'):
            failures.append({'path': name, 'rule': 'excluded_artifact'})
        if path.suffix.lower() in {'.pem', '.key', '.p12', '.pfx', '.crt', '.cer'}:
            failures.append({'path': name, 'rule': 'private_certificate'})
        if path.name.startswith('.env') and name not in PUBLIC_ENVS and not name.endswith('.example'):
            failures.append({'path': name, 'rule': 'private_environment'})
        try:
            text = path.read_text(encoding='utf-8')
        except UnicodeError:
            continue
        if path.name in LEGAL_NAMES or name == 'scripts/check_release.py':
            continue
        checks = [('private_key', PRIVATE_KEY), ('merge_conflict', CONFLICT), ('previous_deployment_ip', OLD_IP)]
        if name not in {'.gitignore', '.dockerignore'}:
            checks.append(('previous_brand', OLD_BRAND))
        fixture = '/tests/' in name or name.endswith('.test.ts')
        if not fixture:
            checks.append(('token_literal', TOKEN))
            for match in AUTH_URL.finditer(text):
                password = match.group(2).strip('"\'')
                if password not in PLACEHOLDERS and '${' not in password and '{' not in password:
                    failures.append({'path': name, 'line': text.count('\n', 0, match.start()) + 1, 'rule': 'credential_in_url'})
        for rule, pattern in checks:
            for match in pattern.finditer(text):
                failures.append({'path': name, 'line': text.count('\n', 0, match.start()) + 1, 'rule': rule})
    return {'files_checked': len(files), 'passed': not failures, 'findings': failures}


if __name__ == '__main__':
    result = scan()
    print(json.dumps(result, ensure_ascii=True, indent=2))
    sys.exit(0 if result['passed'] else 1)
