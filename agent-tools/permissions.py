"""DwarfStar command approval categories; OS confinement remains authoritative.
This is an intent gate, not a proof of arbitrary program behavior. Unknown shell
indirection is confirmed; existing workspace-local programs keep normal access.
"""

from pathlib import Path
import json, os, re, shlex, sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dwarfstar.approval_policy import mode


def decision(command, workspace, approval_mode=None):
    selected = mode(approval_mode)
    if selected == "full_auto":
        return {"tier": 0, "category": "full_auto", "policy": "allow", "sandbox_blocked": False}
    if selected == "ask":
        return {"tier": 0, "category": "ask", "policy": "prompt", "sandbox_blocked": False}

    def answer(tier, category, policy="prompt", blocked=False):
        return {"tier": tier, "category": category, "policy": policy, "sandbox_blocked": blocked}

    text = str(command)
    low = text.lower()
    root = Path(workspace).resolve()
    if re.search(
        r"\b(sudo|security|dscl|csrutil|spctl)\b|keychain|\.ssh\b|id_(?:rsa|ed25519)|\.aws\b|\.netrc\b|\.git-credentials|cookies?(?:\.sqlite|\.db|\b)|login data|password|credential|secret[_-]?access|authorization|bearer|access[_-]?token|api[_-]?key",
        low,
    ):
        return answer(4, "credentials_or_security", blocked=True)
    if re.search(r"\b(?:launchctl|crontab)\b|launchagents|launchdaemons|\.(?:zshrc|zprofile|bashrc|bash_profile|profile)\b|/etc/", low):
        return answer(3, "persistent_system_configuration", blocked=True)
    if re.search(
        r"\bbrew\b|\b(?:npm|pnpm|bun)\b[^\n;&|]*(?:--global\b|(?:^|\s)-g(?:\s|$))|\bpip\d?\s+install\b(?![^\n;&|]*--target)|\binstaller\b",
        low,
    ):
        return answer(3, "system_package_install", blocked=True)
    if re.search(
        r"\bgit\b[^\n;]*(?:\bpush\b|\bsend-email\b)|\b(?:npm|pnpm|bun)\b[^\n;]*\bpublish\b|\b(?:gh|glab)\b[^\n;]*\b(?:create|delete|upload|merge|edit|close)\b|\b(?:vercel|netlify|firebase|flyctl|wrangler|terraform|kubectl|aws|gcloud|az|ssh|scp|sftp|rsync)\b|\bdeploy\b",
        low,
    ):
        return answer(3, "remote_side_effect")
    if re.search(
        r"\b(?:curl|wget)\b[^\n;]*(?:\s-[xX]\s*(?:POST|PUT|PATCH|DELETE)|--request\s+(?:POST|PUT|PATCH|DELETE)|\s-(?:d|F|T)(?:\s|[^\s])|--(?:data|form|upload-file|post-data))|\b(?:method[\x22\x27]?\s*[:=]\s*[\x22\x27]?(?:post|put|patch|delete)|sendmail|osascript|imessage)\b",
        text,
        re.I,
    ):
        return answer(3, "external_write_or_message")
    if re.search(r"0\.0\.0\.0|--host\s+(?:::|\*)|--user-data-dir[^\n;]*(?:application support|library/)", low):
        return answer(4, "public_bind_or_personal_browser")
    if re.search(
        r"\beval\b|\b(?:bash|zsh|sh)\s+-c\b|\$\(|`|\bbase64\b[^\n;]*\b(?:--decode|-d)\b|\b(?:curl|wget)\b[^\n]*\|\s*(?:sh|bash|zsh)", text
    ):
        return answer(3, "shell_indirection")
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        return answer(3, "unparsed_shell")
    # Explicit path targets outside the workspace require confirmation. Executable,
    # installed toolchain and public URL references are not write targets.
    tools = Path(__file__).resolve().parent
    for token in tokens:
        if token in ["..", "~", "/"] or token.startswith(("../", "~/")):
            return answer(4, "outside_workspace_path", blocked=True)
        if token.startswith("/") and not token.startswith(("/opt/homebrew/bin/", "/usr/bin/", "/bin/", "/usr/sbin/", "/dev/null")):
            target = Path(token).resolve()
            if not target.is_relative_to(root) and not target.is_relative_to(tools):
                return answer(4, "outside_workspace_path", blocked=True)
    if re.search(r"\b(?:rm|rmdir|unlink|find)\b", low):
        return answer(1, "workspace_file_management", "allow")
    if re.search(r"\b(?:npm|npx|pnpm|bunx?|pip|uv)\b", low):
        return answer(2, "workspace_dependencies", "allow")
    if re.search(r"\b(?:dwarf-browser|playwright|puppeteer|chrome|http\.server)\b|127\.0\.0\.1|localhost", low):
        return answer(2, "browser_or_localhost", "allow")
    if re.search(r"\b(?:curl|wget)\b|https?://", low):
        return answer(2, "public_fetch", "allow")
    if re.search(r"\bgit\b", low):
        return answer(1, "local_git", "allow")
    return answer(1, "workspace_development", "allow")


if __name__ == "__main__":
    try:
        data = json.load(sys.stdin)
        print(json.dumps(decision(data["command"], data["workspace"], data.get("approval_mode"))))
    except Exception:
        print(json.dumps({"tier": 4, "category": "policy_unavailable", "policy": "prompt", "sandbox_blocked": True}))
