"""Address verification for Malaysian property addresses using OpenStreetMap Nominatim.

Free, no API key required. Used after OCR to confirm that an extracted
property address is a real location in Malaysia, and that it broadly matches
the mukim / daerah / negeri from the land title.
"""
import requests
import re
import time

# Rate-limit: Nominatim policy is max 1 request/second for automated use.
_last_call_ts: float = 0.0
_MIN_INTERVAL = 1.1  # seconds between calls


def _nominatim_search(query: str, timeout: int = 6) -> list:
    """Call Nominatim /search and return the raw result list."""
    global _last_call_ts
    # Enforce rate-limit
    gap = time.time() - _last_call_ts
    if gap < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - gap)
    try:
        resp = requests.get(
            'https://nominatim.openstreetmap.org/search',
            params={
                'q': query,
                'format': 'json',
                'limit': 3,
                'countrycodes': 'my',
                'addressdetails': 1,
            },
            headers={'User-Agent': 'WillCraftAI/1.0 (legal-document-system)'},
            timeout=timeout,
        )
        _last_call_ts = time.time()
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return []


def _normalise(s: str) -> str:
    """Lower-case, remove punctuation, collapse whitespace for fuzzy compare."""
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9\s]', ' ', (s or '').lower())).strip()


def _negeri_match(negeri_ocr: str, display_name: str) -> bool:
    """Check if the state name from OCR appears somewhere in Nominatim's display_name."""
    # Common aliases
    _ALIASES = {
        'johor': ['johor', 'johore'],
        'selangor': ['selangor'],
        'kuala lumpur': ['kuala lumpur', 'kl'],
        'perak': ['perak'],
        'kedah': ['kedah'],
        'kelantan': ['kelantan'],
        'pahang': ['pahang'],
        'melaka': ['melaka', 'malacca'],
        'negeri sembilan': ['negeri sembilan'],
        'terengganu': ['terengganu'],
        'sabah': ['sabah'],
        'sarawak': ['sarawak'],
        'penang': ['penang', 'pulau pinang'],
        'perlis': ['perlis'],
        'putrajaya': ['putrajaya'],
        'labuan': ['labuan'],
    }
    n_ocr = _normalise(negeri_ocr)
    dn_norm = _normalise(display_name)
    for canonical, aliases in _ALIASES.items():
        if any(a in n_ocr for a in aliases):
            if any(a in dn_norm for a in aliases):
                return True
            return False  # found the state but it's in wrong location
    # Generic: just check if OCR negeri appears in display_name
    return n_ocr in dn_norm if n_ocr else True


def verify_property_address(
    property_address: str,
    mukim: str = '',
    daerah: str = '',
    negeri: str = '',
) -> dict:
    """Verify a Malaysian property address via Nominatim.

    Returns a dict:
        verified      (bool | None)  — True = match found; False = not found;
                                        None = API unavailable
        level         (str)          — 'street', 'district', 'state', or 'none'
        canonical     (str)          — Nominatim's canonical address string
        lat           (str | None)
        lon           (str | None)
        note          (str)          — human-readable note for the property card
    """
    if not property_address and not daerah and not negeri:
        return {'verified': None, 'level': 'none', 'canonical': '',
                'lat': None, 'lon': None,
                'note': 'No address or location data to verify'}

    # ── Attempt 1: full address + daerah + negeri ──────────────────────
    parts = [p for p in [property_address, daerah, negeri, 'Malaysia'] if p]
    q1 = ', '.join(parts)
    results = _nominatim_search(q1)
    for r in results:
        dn = r.get('display_name', '')
        if _negeri_match(negeri, dn):
            return {
                'verified': True,
                'level': 'street',
                'canonical': dn,
                'lat': r.get('lat'),
                'lon': r.get('lon'),
                'note': f'✅ Address verified (street level)',
            }

    # ── Attempt 2: daerah + negeri only (district-level fallback) ──────
    if daerah or negeri:
        parts2 = [p for p in [daerah, negeri, 'Malaysia'] if p]
        q2 = ', '.join(parts2)
        results2 = _nominatim_search(q2)
        for r in results2:
            dn = r.get('display_name', '')
            if _negeri_match(negeri, dn):
                return {
                    'verified': True,
                    'level': 'district',
                    'canonical': dn,
                    'lat': r.get('lat'),
                    'lon': r.get('lon'),
                    'note': '⚠️ Street address unverified — district/state confirmed',
                }

    # ── No match ───────────────────────────────────────────────────────
    if results is None:  # API error (None returned from requests)
        return {'verified': None, 'level': 'none', 'canonical': '',
                'lat': None, 'lon': None,
                'note': '⚠️ Address verification unavailable (network error)'}

    return {
        'verified': False,
        'level': 'none',
        'canonical': '',
        'lat': None,
        'lon': None,
        'note': '⚠️ Address could not be verified — please check street name and state',
    }
