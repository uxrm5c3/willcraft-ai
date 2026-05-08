"""§10x.72 — Anthropic billing reconciliation.

Compares our internal ApiCallLog (cost_tracker totals) against the
Anthropic dashboard's reported spend. Alarms if they diverge by > 10%.

Anthropic doesn't expose a public billing API for individual API keys,
so this script accepts a reference number from the user (read off the
dashboard) and compares it.

Usage:
    docker exec willcraft-web python /app/tests/step6/reconcile_cost.py \\
        --period today --anthropic-actual 25.00

    docker exec willcraft-web python /app/tests/step6/reconcile_cost.py \\
        --period month --anthropic-actual 46.67

Output:
  ✅ if internal log matches Anthropic within 10%
  ⚠️  if divergence is 10-30%
  ❌ if divergence is > 30% — points to specific call_sites likely missing log_usage

Exit codes:
  0  match (≤ 10% diff)
  1  divergence (> 10% diff)
  2  error
"""
import sys, argparse
from decimal import Decimal
from datetime import datetime, timedelta
sys.path.insert(0, '/app')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--period', choices=['today', 'month', 'last24h'],
                   default='today',
                   help='Time period to reconcile')
    p.add_argument('--anthropic-actual', type=float, required=True,
                   help='The cost shown on Anthropic dashboard for this period (USD)')
    p.add_argument('--api-key-name', default='willcraft',
                   help='Name of the Anthropic API key (just for the report)')
    p.add_argument('--threshold', type=float, default=10.0,
                   help='Divergence percentage threshold (default 10%%)')
    args = p.parse_args()

    from app import app
    from database import db, ApiCallLog
    from sqlalchemy import func

    if args.period == 'today':
        cutoff = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        period_label = f'today (UTC since {cutoff.isoformat()})'
    elif args.period == 'last24h':
        cutoff = datetime.utcnow() - timedelta(hours=24)
        period_label = 'last 24 hours'
    elif args.period == 'month':
        cutoff = datetime.utcnow().replace(
            day=1, hour=0, minute=0, second=0, microsecond=0)
        period_label = f'month-to-date (since {cutoff.isoformat()})'
    else:
        cutoff = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        period_label = 'today'

    with app.app_context():
        # Internal total
        internal = float(db.session.query(
            func.coalesce(func.sum(ApiCallLog.cost_usd), 0)
        ).filter(ApiCallLog.created_at >= cutoff).scalar() or 0)
        internal_calls = int(db.session.query(
            func.count(ApiCallLog.id)
        ).filter(ApiCallLog.created_at >= cutoff).scalar() or 0)

        anthropic = float(args.anthropic_actual)
        gap = anthropic - internal
        if anthropic > 0:
            divergence_pct = abs(gap) / anthropic * 100
        else:
            divergence_pct = 0

        print('═' * 64)
        print(f'§10x.72 cost reconciliation — {period_label}')
        print(f'API key: {args.api_key_name}')
        print('═' * 64)
        print(f'  Anthropic dashboard: ${anthropic:.4f}')
        print(f'  Internal log:        ${internal:.4f}  ({internal_calls} calls)')
        print(f'  Gap:                 ${gap:.4f}')
        print(f'  Divergence:          {divergence_pct:.1f}%')
        print()

        if divergence_pct <= args.threshold:
            print(f'✅ PASS — within {args.threshold}% threshold')
            return 0
        elif divergence_pct <= 30:
            print(f'⚠️  WARN — divergence {divergence_pct:.1f}% > {args.threshold}% threshold')
        else:
            print(f'❌ FAIL — divergence {divergence_pct:.1f}% > 30%')

        # Diagnostic: show top call_sites + flag possibly-untracked sources
        print()
        print('Top call_sites in this period (internal log):')
        rows = db.session.query(
            ApiCallLog.call_site, func.count(ApiCallLog.id),
            func.coalesce(func.sum(ApiCallLog.cost_usd), 0),
        ).filter(ApiCallLog.created_at >= cutoff
        ).group_by(ApiCallLog.call_site
        ).order_by(func.sum(ApiCallLog.cost_usd).desc()).limit(15).all()
        for cs, n, cost in rows:
            print(f'    {cs[:55]:55s}  {n:5d}c  ${float(cost):.4f}')

        print()
        if gap > 0:
            print(f'Untracked: ${gap:.4f}. Likely missed:')
            print('  - web_search cost (now tracked via §10x.71 — re-test after deploy)')
            print('  - calls failing inside log_usage (silent except)')
            print('  - calls bypassing track_context (client_id=None)')
            null_calls = float(db.session.query(
                func.coalesce(func.sum(ApiCallLog.cost_usd), 0)
            ).filter(ApiCallLog.created_at >= cutoff,
                      ApiCallLog.client_id.is_(None)).scalar() or 0)
            print(f'  - null-client calls in this period: ${null_calls:.4f}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
