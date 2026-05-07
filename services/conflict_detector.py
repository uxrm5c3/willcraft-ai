"""§10x.18 — detect text vs image mismatches across saved gifts.

For each gift in step5_data, look up the bound Document(s) and compare
the OCR'd identifiers against the text-stated values from the AI Summary.
Returns a list of conflicts that should trigger a clarification card.

Public API:
    detect_text_image_mismatches(client_id) -> list[Conflict]

Where Conflict = {
    'gift_idx': int,             # index into step5_data
    'kind': str,                 # 'bank' | 'insurance' | 'property'
    'field': str,                # 'account_number' | 'policy_number' | 'lot_number' etc.
    'text_value': str,           # what the user said in the message
    'image_value': str,          # what OCR extracted
    'document_id': str,          # the image source
}
"""
from __future__ import annotations
import re
import json
from typing import Any, Dict, List, Optional


def _digits(s: str) -> str:
    return re.sub(r'\D', '', s or '')


def _norm(s: str) -> str:
    return re.sub(r'[^a-z0-9]', '', (s or '').lower())


def _is_noise(s: str) -> bool:
    if not s: return True
    junk_tokens = ('UNREADABLE', 'CANNOT READ', 'NOT VISIBLE',
                   '(unreadable)', '(blurred)', '(not visible)',
                   'VALUE:')
    s_up = s.upper()
    return any(j in s_up for j in junk_tokens)


def detect_text_image_mismatches(client_id: str) -> List[Dict[str, Any]]:
    """Compare OCR'd values from Documents against AI Summary / saved gift
    values. Return any disagreements as Conflict dicts.
    """
    if not client_id:
        return []
    try:
        from database import db, Will, Document
    except Exception:
        return []
    will = (Will.query.filter_by(client_id=client_id, status='draft')
            .filter(Will.deleted_at.is_(None))
            .order_by(Will.updated_at.desc()).first())
    if not will or not will.step5_data:
        return []
    try:
        s5 = json.loads(will.step5_data)
        if isinstance(s5, dict):
            s5 = s5.get('gifts') or []
    except Exception:
        s5 = []
    if not isinstance(s5, list):
        return []

    conflicts: List[Dict[str, Any]] = []

    for gi, g in enumerate(s5):
        if not isinstance(g, dict):
            continue
        if g.get('skipped') or g.get('_user_rejected'):
            continue
        kind = g.get('kind')
        # Only check gifts that have a bound Document
        doc_id = g.get('document_id')
        if not doc_id:
            continue
        try:
            d = db.session.get(Document, doc_id)
        except Exception:
            continue
        if not d or not d.extracted_data:
            continue
        try:
            ex = json.loads(d.extracted_data)
        except Exception:
            ex = {}
        if not isinstance(ex, dict):
            continue

        # ── Property ────────────────────────────────────────────────
        if kind == 'property':
            pi = g.get('property_info') or g.get('property_details') or {}
            for field, label in (
                ('lot_number',   'Lot/PTD number'),
                ('title_number', 'Title number'),
            ):
                text_v = (pi.get(field) or g.get(field) or '').strip()
                ocr_v  = (ex.get(field) or '').strip()
                if not text_v or not ocr_v:
                    continue
                if _is_noise(text_v) or _is_noise(ocr_v):
                    continue
                if _digits(text_v) and _digits(ocr_v):
                    if _digits(text_v) != _digits(ocr_v):
                        conflicts.append({
                            'gift_idx': gi, 'kind': 'property',
                            'field': field, 'field_label': label,
                            'text_value': text_v, 'image_value': ocr_v,
                            'document_id': doc_id,
                            'asset_label': pi.get('property_address') or '?',
                        })

        # ── Bank ────────────────────────────────────────────────────
        elif kind == 'bank':
            text_acct = (g.get('account_number') or '').strip()
            ocr_acct  = (ex.get('account_number') or '').strip()
            if text_acct and ocr_acct and not _is_noise(text_acct) and not _is_noise(ocr_acct):
                # Compare digit-only forms (account numbers may have hyphens)
                if _digits(text_acct) and _digits(ocr_acct):
                    if _digits(text_acct) != _digits(ocr_acct):
                        conflicts.append({
                            'gift_idx': gi, 'kind': 'bank',
                            'field': 'account_number', 'field_label': 'Account number',
                            'text_value': text_acct, 'image_value': ocr_acct,
                            'document_id': doc_id,
                            'asset_label': g.get('bank_name', 'Bank'),
                        })
            text_inst = (g.get('bank_name') or '').strip()
            ocr_inst  = (ex.get('bank_name') or '').strip()
            if text_inst and ocr_inst:
                if _norm(text_inst) and _norm(ocr_inst) and _norm(text_inst) != _norm(ocr_inst):
                    conflicts.append({
                        'gift_idx': gi, 'kind': 'bank',
                        'field': 'bank_name', 'field_label': 'Bank / institution',
                        'text_value': text_inst, 'image_value': ocr_inst,
                        'document_id': doc_id,
                        'asset_label': g.get('bank_name', 'Bank'),
                    })

        # ── Insurance ───────────────────────────────────────────────
        elif kind == 'insurance':
            text_pol = (g.get('policy_number') or '').strip()
            ocr_pol  = (ex.get('policy_number') or '').strip()
            if text_pol and ocr_pol and not _is_noise(text_pol) and not _is_noise(ocr_pol):
                if _norm(text_pol) and _norm(ocr_pol) and _norm(text_pol) != _norm(ocr_pol):
                    conflicts.append({
                        'gift_idx': gi, 'kind': 'insurance',
                        'field': 'policy_number', 'field_label': 'Policy number',
                        'text_value': text_pol, 'image_value': ocr_pol,
                        'document_id': doc_id,
                        'asset_label': g.get('insurer', 'Insurance'),
                    })

    return conflicts
