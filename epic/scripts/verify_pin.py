"""Parse and mechanically re-check pin claims. No I/O."""
import re

_CLAIM = re.compile(
    r"^\s*-\s*(?P<kind>verified|assumption):\s*(?P<body>.+)$", re.MULTILINE
)
_LOCATOR = re.compile(r"^(?P<path>[^@\s]+)@(?P<ref>[^#\s]+)#(?P<symbol>\S+)")


def parse_claims(pin_text):
    """Every tagged claim in a pin, in document order."""
    claims = []
    for match in _CLAIM.finditer(pin_text or ""):
        body = match.group("body").strip()
        claim = {"kind": match.group("kind"), "path": None, "ref": None,
                 "symbol": None, "text": body}
        locator = _LOCATOR.match(body)
        if locator:
            claim.update(
                path=locator.group("path"),
                ref=locator.group("ref"),
                symbol=locator.group("symbol"),
            )
        claims.append(claim)
    return claims


def classify(claim, source):
    """verified | stale | unverifiable | assumption."""
    if claim["kind"] == "assumption":
        return "assumption"
    if not claim.get("symbol"):
        return "unverifiable"
    if source is None:
        return "unverifiable"
    return "verified" if re.search(rf"\b{re.escape(claim['symbol'])}\b", source) else "stale"
