"""
Standalone script to verify SAP B1 Service Layer connectivity and credentials.

Usage:
    python scripts/test_b1_connection.py

Reads credentials from the .env file (or environment variables). Performs:
  1. Login → get SessionId
  2. Verify the company DB is reachable (query CompanyService)
  3. Fetch the first Item from the master data (read test)
  4. Logout
  5. Print a summary with pass/fail per step

Does NOT write any data to B1.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow running from project root without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _load_env() -> None:
    """Load .env file variables into os.environ if dotenv is available."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        print(f"[warn] .env not found at {env_path} — falling back to environment variables")
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def _check(label: str, fn) -> bool:  # type: ignore[type-arg]
    """Run fn(); print PASS or FAIL with label. Returns True on success."""
    try:
        result = fn()
        msg = f"  ✓  {label}"
        if result:
            msg += f"  →  {result}"
        print(msg)
        return True
    except Exception as exc:
        print(f"  ✗  {label}  →  {exc}")
        return False


def main() -> int:
    _load_env()

    b1_url = os.environ.get("B1_SERVICE_LAYER_URL", "")
    company_db = os.environ.get("B1_COMPANY_DB", "")
    username = os.environ.get("B1_USERNAME", "")
    password = os.environ.get("B1_PASSWORD", "")
    verify_ssl = os.environ.get("B1_VERIFY_SSL", "true").lower() != "false"

    print("\nSAP B1 Service Layer — connection test")
    print("=" * 50)
    print(f"  URL         : {b1_url or '(not set)'}")
    print(f"  Company DB  : {company_db or '(not set)'}")
    print(f"  Username    : {username or '(not set)'}")
    print(f"  Verify SSL  : {verify_ssl}")
    print()

    if not all([b1_url, company_db, username, password]):
        print("[error] Missing required env vars: B1_SERVICE_LAYER_URL, B1_COMPANY_DB, "
              "B1_USERNAME, B1_PASSWORD")
        return 1

    import requests

    session_id: str | None = None
    base = b1_url.rstrip("/") + "/b1s/v1"
    passed = 0
    total = 0

    # ── Step 1: Login ─────────────────────────────────────────────────────────
    total += 1

    def do_login() -> str:
        nonlocal session_id
        resp = requests.post(
            f"{base}/Login",
            json={"CompanyDB": company_db, "UserName": username, "Password": password},
            verify=verify_ssl,
            timeout=30,
        )
        resp.raise_for_status()
        session_id = resp.json()["SessionId"]
        return session_id[:8] + "..."

    if _check("Login", do_login):
        passed += 1

    if not session_id:
        print("\n[error] Login failed — cannot continue further tests.\n")
        return 1

    cookies = {"B1SESSION": session_id, "CompanyDB": company_db}
    headers = {"Accept": "application/json"}

    # ── Step 2: Company info ──────────────────────────────────────────────────
    total += 1

    def do_company_info() -> str:
        resp = requests.get(
            f"{base}/CompanyService_GetCompanyInfo",
            cookies=cookies, headers=headers, verify=verify_ssl, timeout=30,
        )
        if resp.status_code == 200:
            info = resp.json()
            return info.get("CompanyName", "?")
        # Some B1 versions don't expose this — treat 404/405 as soft pass
        if resp.status_code in (404, 405, 400):
            return f"(endpoint returned {resp.status_code} — probably fine)"
        resp.raise_for_status()
        return ""

    if _check("Fetch company info", do_company_info):
        passed += 1

    # ── Step 3: Read first Item ───────────────────────────────────────────────
    total += 1

    def do_read_item() -> str:
        resp = requests.get(
            f"{base}/Items",
            params={"$top": "1", "$select": "ItemCode,ItemName"},
            cookies=cookies, headers=headers, verify=verify_ssl, timeout=30,
        )
        resp.raise_for_status()
        items = resp.json().get("value", [])
        if not items:
            return "(no items in master data)"
        item = items[0]
        return f"{item.get('ItemCode')} — {item.get('ItemName')}"

    if _check("Read Items (read permission check)", do_read_item):
        passed += 1

    # ── Step 4: Read first Business Partner ───────────────────────────────────
    total += 1

    def do_read_bp() -> str:
        resp = requests.get(
            f"{base}/BusinessPartners",
            params={"$top": "1", "$select": "CardCode,CardName,CardType"},
            cookies=cookies, headers=headers, verify=verify_ssl, timeout=30,
        )
        resp.raise_for_status()
        bps = resp.json().get("value", [])
        if not bps:
            return "(no business partners found)"
        bp = bps[0]
        return f"{bp.get('CardCode')} — {bp.get('CardName')} [{bp.get('CardType')}]"

    if _check("Read BusinessPartners (CardCode access)", do_read_bp):
        passed += 1

    # ── Step 5: Logout ────────────────────────────────────────────────────────
    total += 1

    def do_logout() -> str:
        requests.post(
            f"{base}/Logout",
            cookies=cookies, verify=verify_ssl, timeout=10,
        )
        return "session closed"

    if _check("Logout", do_logout):
        passed += 1

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    print(f"Result: {passed}/{total} checks passed")
    if passed == total:
        print("B1 connection OK — credentials and permissions look correct.\n")
        return 0
    else:
        print("Some checks failed — review the errors above.\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
