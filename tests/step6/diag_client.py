"""Reusable diagnostic — dump everything I need to debug a client.

Usage:
    docker exec willcraft-web python /app/tests/step6/diag_client.py [client_id_or_name]

If no arg given, finds the most recently created client matching 'KOID'.
"""
import sys, json
from datetime import datetime, timedelta
sys.path.insert(0, '/app')

from app import app
from database import db, Client, Will, Document, ChatMessage, ChatSession, Person, ApiCallLog
from sqlalchemy import func


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else 'KOID'
    with app.app_context():
        # Resolve client
        if len(arg) == 36 and arg.count('-') == 4:
            c = Client.query.filter_by(id=arg).first()
        else:
            c = (Client.query.filter(Client.full_name.ilike(f'%{arg}%'))
                 .order_by(Client.created_at.desc()).first())
        if not c:
            print(f'No client matching {arg!r}')
            sys.exit(2)

        print('═' * 72)
        print(f'CLIENT: {c.id}  {c.full_name}  NRIC={c.nric_passport}')
        print(f'Created: {c.created_at}')
        print('═' * 72)

        # Documents
        docs = Document.query.filter_by(client_id=c.id).all()
        print(f'\n[1] DOCUMENTS — {len(docs)}')
        cats: dict = {}
        for d in docs:
            cats[d.category or '(none)'] = cats.get(d.category or '(none)', 0) + 1
        for k, v in sorted(cats.items()):
            print(f'    {k}: {v}')

        # First 5 docs detail
        if docs:
            print(f'\n    First 5 docs:')
            for d in docs[:5]:
                try:
                    ex = json.loads(d.extracted_data or '{}')
                except Exception:
                    ex = {}
                print(f'    - {d.id[:8]} cat={d.category!r} fn={(d.original_filename or "")[:40]!r}')
                print(f'      created={d.created_at}')
                print(f'      kind={ex.get("kind","")!r} attempts={ex.get("_classify_attempts")} terminal={ex.get("_terminal_reason")}')
                print(f'      vision_enriched={ex.get("_vision_enriched")} reason={(ex.get("reason") or "")[:60]!r}')

        # Will
        w = Will.query.filter_by(client_id=c.id, status='draft').first()
        print(f'\n[2] WILL')
        if not w:
            print(f'    (no draft will)')
        else:
            print(f'    will_id={w.id}')
            try:
                completed = json.loads(w.completed_steps or '[]')
            except Exception:
                completed = []
            print(f'    completed_steps: {completed}')
            try:
                s5 = json.loads(w.step5_data or '[]')
            except Exception:
                s5 = []
            print(f'    step5_data: {len(s5) if isinstance(s5, list) else "?"} gifts')
            try:
                s6 = json.loads(w.step6_data or '{}')
            except Exception:
                s6 = {}
            raw = s6.get('_raw_forward_text', '')
            print(f'    raw_forward_text len: {len(raw)}')

        # Persons
        persons = Person.query.filter_by(client_id=c.id).all()
        print(f'\n[3] PERSONS — {len(persons)}')
        for p in persons:
            print(f'    - {p.full_name}  rel={p.relationship!r}  nric={p.nric_passport!r}')

        # Last 6 chat messages
        sess = ChatSession.query.filter_by(client_id=c.id).first()
        print(f'\n[4] CHAT')
        if not sess:
            print(f'    (no chat session)')
        else:
            msgs = ChatMessage.query.filter_by(session_id=sess.id).order_by(
                ChatMessage.created_at.desc()).limit(6).all()
            for m in reversed(msgs):
                snippet = (m.content or '').replace('\n', ' / ')[:200]
                print(f'    [{m.created_at}] {m.role}: {snippet}')

        # Pipeline state
        print(f'\n[5] PIPELINE')
        try:
            from services.asset_pipeline import run_pipeline
            r = run_pipeline(c.id)
            print(f'    AssetItems: {len(r["asset_items"])}')
            print(f'    DocGroups:  {len(r["doc_groups"])}')
            print(f'    Bindings:   {len(r["bindings"])}  '
                  f'(auto-bound: {sum(1 for b in r["bindings"] if b["tier"] != "D")})')
            print(f'    Candidates: {sum(len(v) for v in (r.get("candidates_for_confirm") or {}).values())}')
            print(f'    Residuals:  {len(r["residuals"])}')
        except Exception as e:
            print(f'    Pipeline crashed: {e}')

        # Cost today
        print(f'\n[6] COST (last 24h)')
        cutoff = datetime.utcnow() - timedelta(hours=24)
        rows = (db.session.query(ApiCallLog.call_site, func.count(ApiCallLog.id),
                                    func.coalesce(func.sum(ApiCallLog.cost_usd), 0))
                .filter(ApiCallLog.client_id == c.id,
                        ApiCallLog.created_at >= cutoff)
                .group_by(ApiCallLog.call_site)
                .order_by(func.sum(ApiCallLog.cost_usd).desc()).limit(10).all())
        if not rows:
            print(f'    (no calls in last 24h)')
        else:
            total = 0.0
            for cs, n, cost in rows:
                total += float(cost)
                print(f'    {cs[:50]:50s}  {n:4d} calls  ${float(cost):.4f}')
            print(f'    {"TOTAL":50s}  {sum(r[1] for r in rows):4d} calls  ${total:.4f}  '
                  f'(ceiling \$2.00)')


if __name__ == '__main__':
    main()
