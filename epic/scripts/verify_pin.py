"""Parse and mechanically re-check pin claims. The pure `parse_claims()` /
`classify()` functions do no I/O; `main()` is the thin impure shell."""
import argparse
import base64
import json
import re
import sys
import urllib.parse

import gh

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


def _fetch_source(repo, path, ref):
    """File content at `path@ref` in `repo`, or None if it can't be resolved.

    A 404 (or any other fetch failure) yields None, never an exception — a
    path that does not resolve is `unverifiable`, not a CLI error.

    The ref MUST travel in the query string (`?ref=<ref>`), never as a `-f`
    form value — `gh api` treats any `-f`/`-F` flag as "this is a POST", and
    the contents endpoint rejects POST outright, which silently turned every
    claim `unverifiable` regardless of whether it was actually stale. The
    ref is percent-encoded before it lands in the query string — an
    unencoded `&`/`#`/`=` in a ref would otherwise corrupt the query string
    (start a new param or truncate into a URL fragment).
    """
    encoded_ref = urllib.parse.quote(ref, safe="")
    try:
        data = gh.run_json(["api", f"repos/{repo}/contents/{path}?ref={encoded_ref}"])
    except gh.GhError:
        return None
    content_b64 = data.get("content")
    if not content_b64:
        return None
    try:
        return base64.b64decode(content_b64).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Classify every claim in a pin against reality.")
    parser.add_argument("--pin", required=True, help="path to the pin file")
    # `--repo` is optional (SKILL.md invokes this as `verify_pin.py --pin
    # <file>` from inside the child's worktree checkout): when omitted, the
    # repo is resolved lazily from cwd's `origin` remote, and only if a claim
    # actually needs a content fetch.
    parser.add_argument("--repo")
    args = parser.parse_args(argv)

    try:
        with open(args.pin, encoding="utf-8") as handle:
            pin_text = handle.read()
    except OSError as exc:
        message = str(exc)
        print(json.dumps({"error": message}))
        sys.stderr.write(message + "\n")
        return 2

    resolved_repo = [args.repo]

    def _repo():
        if resolved_repo[0] is None:
            resolved_repo[0] = gh.resolve_repo_from_cwd()
        return resolved_repo[0]

    claims = parse_claims(pin_text)
    results = []
    any_stale = False
    for claim in claims:
        source = None
        if claim.get("path") and claim.get("ref"):
            try:
                source = _fetch_source(_repo(), claim["path"], claim["ref"])
            except gh.GhError:
                source = None  # repo unresolvable -> unverifiable, never an error
        verdict = classify(claim, source)
        if verdict == "stale":
            any_stale = True
        results.append({"text": claim["text"], "verdict": verdict})

    print(json.dumps({"claims": results}))
    return 1 if any_stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
