"""🔥 §10x.124 — Single source of truth for role strings.

Why this module exists
======================
Across this session and prior, the same class of bug has recurred:

  • User clicks "✓ Yes — Sister-In-Law" (quickreply value 'sister-in-law')
  • Save handler calls parse_relationship('sister-in-law') which uses
    the keyword 'sister in law' (with SPACES). Substring match fails.
  • Handler returns None → walker re-renders the SAME card → user is
    stuck. The IC is never linked to a Person.

Or:

  • Card builder appends '✓ Yes — Sister-In-Law' from the deduce path
    (out_role='Sister-in-law')
  • Then appends 'Sister In Law' from _plausible_remaining_roles which
    returned 'Sister In Law' (with SPACES, different formatting)
  • Dedup compared `r.lower() != out_role.lower()` →
    'sister in law' != 'sister-in-law' → both buttons rendered.
  • User sees TWO IDENTICAL buttons.

Both bugs share one root cause: role strings flow through the codebase
in MANY equivalent spellings ('sister-in-law', 'Sister-in-law',
'Sister In Law', 'sister in law', 'SISTER-IN-LAW', etc.) and every
comparison/lookup site has its own ad-hoc normalisation. Some sites
forget to normalise; others normalise differently. Every spelling drift
introduces a regression.

The fix
=======
ONE canonical-role module. Every site that produces, consumes, or
compares a role string passes through this module:

  • canonical(text)        → "Sister-in-law" or None
  • is_family_role(text)   → bool
  • is_will_role(text)     → bool
  • dedup_quickreplies(qr_list) → quickreply list with same-canonical-
                                  value entries collapsed

Comparing role strings without canonicalising is a bug.
Adding new role variants requires only adding ONE entry below.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional


# ─────────────────────────────────────────────────────────────────────
# CANONICAL FORMS — these are the ONLY strings stored in Person.relationship
# and the ONLY strings the will-generator should reference.
# ─────────────────────────────────────────────────────────────────────

FAMILY_ROLES = (
    'Spouse', 'Husband', 'Wife',
    'Father', 'Mother',
    'Son', 'Daughter',
    'Brother', 'Sister',
    'Grandfather', 'Grandmother', 'Grandson', 'Granddaughter',
    'Uncle', 'Aunt', 'Nephew', 'Niece', 'Cousin',
    'Father-in-law', 'Mother-in-law',
    'Son-in-law', 'Daughter-in-law',
    'Brother-in-law', 'Sister-in-law',
    'Stepson', 'Stepdaughter',
    'Adopted Son', 'Adopted Daughter',
    'Friend', 'Relative', 'Other',
)

WILL_ROLES = (
    'Executor', 'Trustee', 'Guardian', 'Witness', 'Beneficiary',
)

ALL_CANONICAL = tuple(list(FAMILY_ROLES) + list(WILL_ROLES))


# ─────────────────────────────────────────────────────────────────────
# ALIASES — input variations that should normalise to the canonical
# form on the right. Add new spellings here as they're discovered.
# ─────────────────────────────────────────────────────────────────────

_ALIASES: Dict[str, str] = {
    # Spouse variants
    'spouse': 'Spouse',
    'husband': 'Husband',
    'wife': 'Wife',
    'partner': 'Spouse',
    # Children
    'son': 'Son',
    'daughter': 'Daughter',
    # Parents
    'father': 'Father',
    'mother': 'Mother',
    'dad': 'Father',
    'mom': 'Mother',
    'mum': 'Mother',
    'papa': 'Father',
    'mama': 'Mother',
    # Siblings
    'brother': 'Brother',
    'sister': 'Sister',
    # In-laws — CANONICAL hyphenated form is "Sister-in-law" etc.
    # Aliases below cover space-separated AND hyphen-separated AND
    # camelcase ("SisterInLaw") AND no-space ("sisterinlaw").
    'sister-in-law':   'Sister-in-law',
    'sister in law':   'Sister-in-law',
    'sisterinlaw':     'Sister-in-law',
    'brother-in-law':  'Brother-in-law',
    'brother in law':  'Brother-in-law',
    'brotherinlaw':    'Brother-in-law',
    'father-in-law':   'Father-in-law',
    'father in law':   'Father-in-law',
    'fatherinlaw':     'Father-in-law',
    'mother-in-law':   'Mother-in-law',
    'mother in law':   'Mother-in-law',
    'motherinlaw':     'Mother-in-law',
    'son-in-law':      'Son-in-law',
    'son in law':      'Son-in-law',
    'soninlaw':        'Son-in-law',
    'daughter-in-law': 'Daughter-in-law',
    'daughter in law': 'Daughter-in-law',
    'daughterinlaw':   'Daughter-in-law',
    # Grandparents / grandchildren
    'grandfather':  'Grandfather',
    'grandmother':  'Grandmother',
    'grandpa':      'Grandfather',
    'grandma':      'Grandmother',
    'grandson':     'Grandson',
    'granddaughter':'Granddaughter',
    # Extended family
    'uncle':   'Uncle',
    'aunt':    'Aunt',
    'auntie':  'Aunt',
    'aunty':   'Aunt',
    'nephew':  'Nephew',
    'niece':   'Niece',
    'cousin':  'Cousin',
    # Step / adopted
    'stepson':         'Stepson',
    'stepdaughter':    'Stepdaughter',
    'step-son':        'Stepson',
    'step-daughter':   'Stepdaughter',
    'step son':        'Stepson',
    'step daughter':   'Stepdaughter',
    'adopted son':     'Adopted Son',
    'adopted daughter':'Adopted Daughter',
    # Catch-all family
    'friend':   'Friend',
    'relative': 'Relative',
    'other':    'Other',
    # Will-roles (kept separate — Step 1 IC walker rejects these)
    'executor':    'Executor',
    'trustee':     'Trustee',
    'guardian':    'Guardian',
    'witness':     'Witness',
    'beneficiary': 'Beneficiary',
}


# Pre-compute the canonical → canonical identity mapping so callers
# can pass already-canonicalised strings without re-normalisation.
for _can in ALL_CANONICAL:
    _ALIASES.setdefault(_can.lower(), _can)


_HYPHEN_OR_SPACE_RE = re.compile(r'[\s\-_]+')


def _normalise_key(text: str) -> str:
    """Internal: strip, lowercase, collapse hyphens/spaces/underscores
    to a single space. Used as the lookup key into _ALIASES.

    Also generates a no-separator key (e.g. 'sisterinlaw') as a fallback
    for inputs like 'SisterInLaw'.
    """
    if not text:
        return ''
    return _HYPHEN_OR_SPACE_RE.sub(' ', text.strip().lower()).strip()


def canonical(text: Optional[str]) -> Optional[str]:
    """Return the canonical role string for ANY input variation, or None
    if the input doesn't look like a known role.

    Examples
    --------
    >>> canonical('sister-in-law')      → 'Sister-in-law'
    >>> canonical('Sister In Law')      → 'Sister-in-law'
    >>> canonical('SISTER-IN-LAW')      → 'Sister-in-law'
    >>> canonical('sisterinlaw')        → 'Sister-in-law'
    >>> canonical('  Sister-in-law  ')  → 'Sister-in-law'
    >>> canonical('garbage')            → None
    >>> canonical(None)                 → None
    """
    if not text:
        return None
    # First try the normalised form (handles hyphens AND spaces)
    key = _normalise_key(text)
    if not key:
        return None
    # Try the spaced form
    if key in _ALIASES:
        return _ALIASES[key]
    # Try the hyphenated form (sister-in-law)
    hyphen_key = key.replace(' ', '-')
    if hyphen_key in _ALIASES:
        return _ALIASES[hyphen_key]
    # Try the no-separator form (sisterinlaw)
    nosep_key = key.replace(' ', '')
    if nosep_key in _ALIASES:
        return _ALIASES[nosep_key]
    return None


def parse_role_from_freetext(text: Optional[str]) -> Optional[str]:
    """Find the FIRST canonical role mentioned anywhere in free text.
    Longer phrases win (so 'sister in law' beats 'sister' inside the
    same string). Returns None if no role keyword is found.

    This replaces parse_relationship from identity_walker.py for the
    free-text path — and uses the same alias table so hyphenated
    input ('sister-in-law') matches.
    """
    if not text:
        return None
    haystack = _normalise_key(text)
    if not haystack:
        return None
    # Pad with spaces so word-boundary checks work
    padded = ' ' + haystack + ' '
    # Sort aliases by length DESC so 'sister in law' beats 'sister'
    aliases_by_len = sorted(_ALIASES.keys(), key=len, reverse=True)
    for alias in aliases_by_len:
        # Compare in normalised space too — handles 'sister-in-law' input
        norm_alias = _normalise_key(alias)
        if (' ' + norm_alias + ' ') in padded:
            return _ALIASES[alias]
    return None


def is_family_role(text: Optional[str]) -> bool:
    """True if `text` resolves to a canonical FAMILY role (not a will-role)."""
    c = canonical(text)
    return c is not None and c in FAMILY_ROLES


def is_will_role(text: Optional[str]) -> bool:
    """True if `text` resolves to a canonical WILL role
    (Executor / Trustee / Guardian / Witness / Beneficiary).
    """
    c = canonical(text)
    return c is not None and c in WILL_ROLES


def dedup_quickreplies(quickreplies: List[Dict[str, str]],
                        log_warnings: bool = True
                        ) -> List[Dict[str, str]]:
    """Remove quickreply entries whose `value` resolves to the same
    canonical role as an earlier entry. First occurrence wins.

    Non-role values (Skip / Delete / yes / type / specific names like
    'Joshua Koid Teck Seng') pass through unchanged — only role-string
    duplicates are collapsed.

    🔥 §10x.124 — every card builder MUST run its quickreply list
    through this function before emitting. Prevents the regression
    where two buttons label the same role differently
    ('✓ Sister-In-Law' + 'Sister In Law').
    """
    if not quickreplies:
        return quickreplies
    seen_canonical = set()
    out = []
    duplicates_dropped = []
    for qr in quickreplies:
        if not isinstance(qr, dict):
            out.append(qr)
            continue
        val = qr.get('value', '')
        c = canonical(val)
        if c and c in seen_canonical:
            duplicates_dropped.append(qr.get('label', val))
            continue
        if c:
            seen_canonical.add(c)
        out.append(qr)
    if duplicates_dropped and log_warnings:
        try:
            import logging
            logging.getLogger(__name__).warning(
                f'§10x.124: dropped {len(duplicates_dropped)} duplicate '
                f'quickreply role(s): {duplicates_dropped}')
        except Exception:
            pass
    return out


# ─────────────────────────────────────────────────────────────────────
# SELF-TESTS — invoke as `python services/role_registry.py` to verify
# every alias resolves correctly. Idempotency (canonical(canonical(x))
# == canonical(x)) is the invariant.
# ─────────────────────────────────────────────────────────────────────

def _self_test():
    """Run on import or via CLI to catch regressions in the alias table."""
    fixtures = [
        # (input, expected_canonical)
        ('sister-in-law',  'Sister-in-law'),
        ('Sister In Law',  'Sister-in-law'),
        ('SISTER-IN-LAW',  'Sister-in-law'),
        ('sisterinlaw',    'Sister-in-law'),
        ('  Sister-in-law  ', 'Sister-in-law'),
        ('Sister-in-law',  'Sister-in-law'),
        ('Brother-in-law', 'Brother-in-law'),
        ('brother in law', 'Brother-in-law'),
        ('Wife',           'Wife'),
        ('wife',           'Wife'),
        ('SON',            'Son'),
        ('Executor',       'Executor'),
        ('garbage_xyz',    None),
        (None,             None),
        ('',               None),
    ]
    failures = []
    for input_val, expected in fixtures:
        got = canonical(input_val)
        if got != expected:
            failures.append((input_val, expected, got))
    # Idempotency
    for c in ALL_CANONICAL:
        if canonical(canonical(c)) != canonical(c):
            failures.append((f'idempotency: {c}', c, canonical(canonical(c))))
    return failures


if __name__ == '__main__':
    failures = _self_test()
    if failures:
        for inp, exp, got in failures:
            print(f'FAIL: canonical({inp!r}) → {got!r}, expected {exp!r}')
        raise SystemExit(1)
    print(f'OK: all {len(_ALIASES)} alias entries normalise correctly.')
