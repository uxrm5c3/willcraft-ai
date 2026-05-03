"""Look at the user's free-form chat / email text and deduce each named
person's role (Son, Daughter, Executor, etc.) when the text mentions them.

Used by the directed identity walk-through so we can present a pre-filled
guess instead of asking the user cold ("Based on your email, CHAI MEI FUN
appears to be your Spouse — confirm?").
"""
import json
import anthropic
from typing import Dict, List
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL_CHEAP


CANONICAL_ROLES = [
    'Spouse', 'Husband', 'Wife', 'Son', 'Daughter', 'Father', 'Mother',
    'Brother', 'Sister', 'Sister-in-law', 'Brother-in-law', 'Father-in-law',
    'Mother-in-law', 'Son-in-law', 'Daughter-in-law', 'Grandson',
    'Granddaughter', 'Grandfather', 'Grandmother', 'Uncle', 'Aunt',
    'Nephew', 'Niece', 'Cousin', 'Stepson', 'Stepdaughter',
    'Adopted Son', 'Adopted Daughter', 'Friend', 'Relative',
    'Executor', 'Trustee', 'Guardian', 'Witness', 'Beneficiary',
]


def deduce_roles(text: str, names: List[str]) -> Dict[str, Dict[str, str]]:
    """Given free text + a list of names to look up, return a mapping of:
        { "FULL NAME": { "role": "Son", "evidence": "from 'Joshua (son)'" }, ... }

    Names without clear evidence are simply omitted from the result.
    Returns empty dict on any failure (caller falls back to asking cold).
    """
    if not text or not names:
        return {}
    text = text.strip()
    if len(text) > 8000:
        text = text[:8000]  # safety cap on token usage

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        roles_csv = ', '.join(CANONICAL_ROLES)
        names_list = '\n'.join(f"- {n}" for n in names)
        msg = client.messages.create(
            model=CLAUDE_MODEL_CHEAP,
            max_tokens=800,
            messages=[{
                "role": "user",
                "content": f"""You will read a piece of free-form text (typically a forwarded email
or WhatsApp message about a Malaysian will) and figure out the role of each
named person mentioned in it relative to the testator.

NAMES TO CHECK:
{names_list}

ALLOWED ROLES (use exact spelling):
{roles_csv}

TEXT:
\"\"\"{text}\"\"\"

For each name, look for clues in the text:
  - "Joshua Koid Teck Seng (son)" → role=Son
  - "My Executor: my Sister in law" → role=Executor (and Sister-in-law)
  - "my wife (Lim Bee Yan)" → role=Wife
  - "My beneficiary is X" → role=Beneficiary

If a name appears in the text with a role clue, include it in the output.
If a name does NOT appear or there's no clear role clue, OMIT it.

Return ONLY a JSON object, no commentary:
{{"FULL NAME": {{"role": "Son", "evidence": "short quoted snippet from the text"}}, ...}}
"""
            }]
        )
        try:
            from ai.cost_tracker import log_usage
            log_usage(msg, call_site='ai.role_deducer.deduce_roles')
        except Exception:
            pass
        raw = (msg.content[0].text or '').strip() if msg.content else ''
        # Strip code fences if any
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[1].rsplit('```', 1)[0].strip()
        result = json.loads(raw)
        if not isinstance(result, dict):
            return {}
        # Filter to allowed roles, drop bad entries
        cleaned = {}
        roleset = set(CANONICAL_ROLES)
        for name, info in result.items():
            if not isinstance(info, dict):
                continue
            role = (info.get('role') or '').strip()
            if role in roleset and name in names:
                cleaned[name] = {
                    'role': role,
                    'evidence': (info.get('evidence') or '')[:200],
                }
        return cleaned
    except Exception:
        return {}
