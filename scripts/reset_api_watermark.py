"""
Reset an API partner's fetch watermark (TradingPartner.api_config.last_fetched_at).

Needed when the watermark has advanced past data that was never actually fetched —
e.g. the Zepto adapter used to swallow fetch errors and return [], which looked like
"no new POs" and let the watermark move forward on every failed poll. Fixed in the
adapter (it now raises), but an already-poisoned watermark must be wound back by hand
or the missed POs stay outside the `days` window the next fetch requests.

Usage:
    python scripts/reset_api_watermark.py ZEPTO --to 2026-07-17T19:39:07+00:00
    python scripts/reset_api_watermark.py ZEPTO --days-ago 20
    python scripts/reset_api_watermark.py ZEPTO --clear      # -> defaults to 7 days
    python scripts/reset_api_watermark.py --show             # list all API partners
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db import SyncSessionLocal  # noqa: E402
from app.models.master_data import TradingPartner  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("partner_code", nargs="?", help="e.g. ZEPTO")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--to", help="ISO-8601 UTC timestamp to set")
    g.add_argument("--days-ago", type=int, help="set watermark N days before now")
    g.add_argument("--clear", action="store_true", help="remove it entirely")
    ap.add_argument("--show", action="store_true", help="list current watermarks")
    args = ap.parse_args()

    with SyncSessionLocal() as session:
        if args.show or not args.partner_code:
            rows = session.query(TradingPartner).filter(
                TradingPartner.deleted_at.is_(None)
            ).order_by(TradingPartner.code).all()
            for p in rows:
                wm = (p.api_config or {}).get("last_fetched_at")
                if wm or str(p.source_channel) in ("API", "WEBHOOK"):
                    print(f"  {p.code:16} {str(p.source_channel):8} {wm or '(none)'}")
            return 0

        partner = session.query(TradingPartner).filter(
            TradingPartner.code == args.partner_code.upper()
        ).one_or_none()
        if partner is None:
            print(f"partner '{args.partner_code}' not found")
            return 1

        cfg = dict(partner.api_config or {})
        before = cfg.get("last_fetched_at")

        if args.clear:
            cfg.pop("last_fetched_at", None)
            after = None
        else:
            if args.to:
                ts = args.to
            elif args.days_ago is not None:
                ts = (datetime.now(UTC) - timedelta(days=args.days_ago)).isoformat()
            else:
                print("specify --to, --days-ago or --clear")
                return 1
            cfg["last_fetched_at"] = ts
            after = ts

        partner.api_config = cfg
        session.commit()
        print(f"{partner.code}: {before or '(none)'}  ->  {after or '(none)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
