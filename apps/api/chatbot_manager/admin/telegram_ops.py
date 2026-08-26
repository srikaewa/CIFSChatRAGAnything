import json
import platform
import subprocess
from collections.abc import Callable
from urllib.parse import urlsplit

RunCallable = Callable[..., subprocess.CompletedProcess[str]]


def service_port(url: str) -> int:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("invalid_public_url")
    try:
        return parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError("invalid_public_url") from exc


def tailscale_command(action: str, port: int, platform_name: str | None = None) -> list[str]:
    platform_name = platform_name or platform.system()
    if action == "on":
        return ["tailscale", "funnel", "--bg", str(port)]
    if action == "off":
        return ["tailscale", "funnel", "off", str(port)]
    raise ValueError("unsupported_tailscale_action")


def discover_tailscale_host(run: RunCallable = subprocess.run) -> tuple[str | None, str | None]:
    try:
        result = run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return None, "tailscale_cli_missing"
    except subprocess.TimeoutExpired:
        return None, "tailscale_timeout"
    if result.returncode != 0:
        return None, "tailscale_status_failed"
    try:
        status = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None, "tailscale_status_invalid"
    self_status = status.get("Self") if isinstance(status, dict) else None
    if not isinstance(self_status, dict):
        return None, "tailscale_status_invalid"
    dns_name = self_status.get("DNSName", "")
    if not isinstance(dns_name, str) or not dns_name.strip():
        return None, "tailscale_dns_missing"
    return dns_name.strip().rstrip("."), None


def run_funnel(
    action: str,
    port: int,
    run: RunCallable = subprocess.run,
    platform_name: str | None = None,
) -> str | None:
    try:
        result = run(
            tailscale_command(action, port, platform_name),
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError:
        return "tailscale_cli_missing"
    except subprocess.TimeoutExpired:
        return "tailscale_timeout"
    if result.returncode == 0:
        return None
    stderr = (result.stderr or "").lower()
    if "access denied" in stderr or "permission denied" in stderr:
        return "tailscale_access_denied"
    return "tailscale_funnel_failed"
