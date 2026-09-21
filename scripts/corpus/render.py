"""Render a fact sheet into a report (four structural styles) plus its exact gold and spans.

Diversity comes from (a) sentence pools per behaviour, (b) four structurally different frames,
(c) code-style vs natural-language field mentions, (d) digit vs number-word numerals and five
threshold phrasings ("at least 5", "5 or more", "5+", "no fewer than 5", "more than 4"),
(e) spaced vs hyphenated windows, and (f) distractor numbers. It is still template-driven;
docs/corpus.md reports the measured template similarity and what the LLM tier adds.
"""
from __future__ import annotations

import random

from .doc import Doc, Tagged
from .facts import Facts
from .vocab import B1, B2, B3, B4, B5, CODE_FORM, NATURAL_PHRASES, NUMBER_WORDS

STYLES = ["advisory", "soc-ticket", "postmortem", "handoff-chat"]
FORMS = {"at-least": ("at least ", ""), "or-more": ("", " or more"), "plus": ("", "+"),
         "no-fewer": ("no fewer than ", ""), "more-than": ("more than ", "")}
PARAM_HEADINGS = ["Detection guidance", "Analyst-confirmed detection parameters", "Recommended detection"]


def _numeral(n: int, words: bool) -> str:
    return NUMBER_WORDS[n] if words and n in NUMBER_WORDS else str(n)


def thr_slot(f: Facts) -> list:
    pre, post = FORMS[f.threshold_form]
    text = _numeral(f.threshold_shown, f.num_mode == "words" and f.threshold_form != "plus")
    return [pre, Tagged.of(text, "threshold", value=f.threshold, form=f.threshold_form), post]


def win_slot(f: Facts) -> list:
    amount = _numeral(f.window_amount, f.num_mode == "words")
    singular = f.window_unit[:-1]
    if f.window_form == "hyphen":
        return [Tagged.of(amount, "window_amount", value=f.window_amount), "-",
                Tagged.of(singular, "window_unit", value=f.window_unit)]
    unit_text = singular if f.window_amount == 1 else f.window_unit
    return [Tagged.of(amount, "window_amount", value=f.window_amount), " ",
            Tagged.of(unit_text, "window_unit", value=f.window_unit)]


def _plain(segments: list) -> str:
    return "".join(s.text if isinstance(s, Tagged) else s for s in segments)


def thr_expression(f: Facts) -> str | None:
    return _plain(thr_slot(f)) if f.threshold is not None else None


def win_expression(f: Facts) -> str | None:
    return _plain(win_slot(f)) if f.window_amount is not None else None


# --------------------------------------------------------------------------- shared pieces
def _mention(name: str, label: str, f: Facts, rng: random.Random) -> list:
    if f.field_mode == "code":
        return ["`", Tagged.of(name.split(".")[-1], label, field=name), "`"]
    return [Tagged.of(rng.choice(NATURAL_PHRASES[name]), label, field=name)]


def _join(items: list[list]) -> list:
    out: list = []
    for i, item in enumerate(items):
        if i:
            out.append(" and " if i == len(items) - 1 else ", ")
        out += item
    return out


_FIELDS_CODE = ["Required log fields: {lst}.", "The rule reads {lst} from the authentication log.", "Fields needed: {lst}."]
_FIELDS_NAT = ["To evaluate this, the log has to provide {lst}.", "The detection depends on {lst}.",
               "Evaluating the rule requires {lst}."]
_EXCLUDED = ["In this case {ex} varied between events and is not part of the pattern, so the rule must not depend on it.",
             "Note that {ex} is incidental here; do not condition the detection on it.",
             "For scoping, {ex} is not relevant to the behaviour and should be left out of the rule."]
_DISTRACTORS = ["Ticket {ticket} was opened at {hhmm} UTC and assigned to {analyst}.",
                "The affected subnet is {subnet}.",
                "Earlier the same day the account had {earlier_logins} unremarkable successful logins."]


def _fields_piece(f: Facts, rng: random.Random, names: list[str], label: str = "required_field"):
    items = [_mention(n, label, f, rng) for n in names]
    template = rng.choice(_FIELDS_CODE if f.field_mode == "code" else _FIELDS_NAT)
    return lambda doc: doc.fill(template, lst=_join(items))


def _excluded_piece(f: Facts, rng: random.Random):
    if not f.excluded_fields:
        return None
    template = rng.choice(_EXCLUDED)
    mention = _mention(f.excluded_fields[0], "excluded_field", f, rng)
    return lambda doc: doc.fill(template, ex=mention)


def _distractor_piece(f: Facts, rng: random.Random):
    pool = _DISTRACTORS if f.behaviour in (B1, B3, B4, B5) else _DISTRACTORS[:2]
    template = rng.choice(pool)
    return lambda doc: doc.fill(template, **f.meta)


def _text_piece(template: str, **slots):
    return lambda doc: doc.fill(template, **slots)


# --------------------------------------------------------------------------- per-behaviour pools
def _supported_pieces(f: Facts, rng: random.Random) -> dict:
    e, o = f.entities, f.observed
    thr, win = thr_slot(f) if f.threshold else None, win_slot(f) if f.window_amount else None
    pick = lambda pool: rng.choice(pool)          # noqa: E731 - tiny local helper
    adj = f.window_form == "hyphen"

    def rule(pool):
        template = pick([t for t, is_adj in pool if is_adj == adj])
        return _text_piece(template, thr=thr, win=win)

    if f.behaviour == B1:
        title = pick(["Repeated Failed Logins Followed by Success on {acct}", "Credential Guessing Against {acct}",
                      "Brute-Force Pattern on {gw}", "Suspected Account Takeover: {acct}"]).format(**e)
        context = _text_piece(pick([
            "Overnight monitoring flagged repeated failed sign-ins against {acct} on {gw}.",
            "The identity team reported a burst of failed logins on {gw} for the account {acct}.",
            "{acct} was targeted through {gw}: a run of rejected password attempts was followed by a successful session.",
            "Analysts reviewing {gw} authentication logs found guessing activity aimed at {acct}."]), **e)
        incident = _text_piece(pick([
            "In total {n} failed attempts were recorded over about {span_s} seconds before a login as {acct} was accepted.",
            "The account failed {n} times in roughly {span_s} seconds and then authenticated successfully.",
            "After {n} rejected attempts spread across {span_s} seconds, {acct} logged in."]), **e, **o)
        rule_piece = rule([
            ("Detection guidance: raise an alert when {thr} failed sign-ins for the same account occur within {win}, "
             "followed by a successful sign-in for that account.", False),
            ("The rule should fire once one account accumulates {thr} failed logins inside a {win} window "
             "and then logs in successfully.", True),
            ("Alert condition: {thr} failed logins on one account within {win}, then a success on that account.", False),
            ("Flag any account that fails to authenticate {thr} times within {win} and subsequently succeeds.", False),
            ("Treat {thr} failures against a single account inside a {win} window, followed by a successful login, "
             "as a brute-force success.", True)])
        names = list(f.required_fields)
    elif f.behaviour == B2:
        title = pick(["Password Spraying From {gw}", "Credential Guessing Across Many Accounts via {gw}",
                      "Low-and-Slow Spray Against the Directory", "Distributed Failed Logins Seen at {gw}"]).format(**e)
        context = _text_piece(pick([
            "{gw} recorded failed sign-ins against many different accounts in a short period.",
            "Authentication logs show one gateway, {gw}, being used to try a small set of passwords across the directory.",
            "The SOC noticed a wide, shallow pattern of failures originating at {gw}."]), **e)
        incident = _text_piece(pick([
            "{n} distinct accounts were tried, mostly once each, over about {span_min} minutes; among them {a0}, {a1} and {a2}.",
            "Over roughly {span_min} minutes the host touched {n} different accounts, including {a0} and {a1}; "
            "none of them went on to a successful login."]),
            **e, **o, a0=e["accts"][0], a1=e["accts"][1], a2=e["accts"][2])
        rule_piece = rule([
            ("Alert when {thr} distinct accounts fail to authenticate from the same source host within {win}.", False),
            ("The detection should trigger when one source host produces failed sign-ins for {thr} different accounts "
             "inside a {win} window.", True),
            ("Flag a source host once failed logins span {thr} distinct accounts within {win}; repeated failures "
             "on a single account do not count.", False),
            ("Treat {thr} distinct accounts failing from one host within a {win} window as password spraying.", True)])
        names = list(f.required_fields)
    elif f.behaviour == B3:
        title = pick(["Concurrent Sessions for {acct}", "One Account, Two Hosts: {acct}",
                      "Possible Credential Sharing on {acct}"]).format(**e)
        context = _text_piece(pick([
            "The account {acct} appeared to hold live sessions from different machines at nearly the same time.",
            "Access review flagged {acct} for authenticating from more than one host in quick succession.",
            "{acct} logged in from two places without any logout in between."]), **e)
        incident = _text_piece(pick([
            "Successful sign-ins for {acct} came from {h1} and then {h2}, about {gap_min} minutes apart.",
            "{acct} authenticated at {h1}; roughly {gap_min} minutes later a second success arrived from {h2}."]),
            **e, **o)
        rule_piece = rule([
            ("Alert when the same account has successful logins from {thr} different hosts within {win}.", False),
            ("The rule should fire if one account holds live sessions on {thr} distinct hosts inside a {win} window.", True),
            ("Flag concurrent use: successful authentication for one account from {thr} separate machines within {win}.", False),
            ("Treat successful sign-ins for a single account on {thr} distinct hosts within a {win} window "
             "as possible credential sharing.", True)])
        names = list(f.required_fields)
    elif f.behaviour == B4:
        title = pick(["Service Account {acct} Used an Unexpected Method", "Interactive Authentication by {acct}",
                      "Provisioning Mismatch on {acct}"]).format(**e)
        context = _text_piece(pick([
            "The service account {acct} is provisioned for {expected} authentication only, per the identity export.",
            "Per policy, {acct} should only ever authenticate with a {expected}.",
            "Identity records list {expected} as the sole approved method for {acct}."]), **e, **o)
        incident = _text_piece(pick([
            "A successful login for {acct} was recorded on {gw} using {used} instead.",
            "On {gw}, {acct} signed in successfully with {used}, which does not match its provisioning."]), **e, **o)
        rule_piece = _text_piece(pick([
            "Alert on any successful login where the authentication method differs from the method the account is "
            "provisioned for; any direction of mismatch counts.",
            "The rule should fire when a successful sign-in uses a different authentication method than the "
            "account's expected one.",
            "Flag successful logins that do not use the account's expected authentication method."]))
        names = list(f.required_fields) + list(f.policy_fields)
    else:  # B5
        title = pick(["MFA Missing on {acct}", "Sign-In Without a Second Factor: {acct}",
                      "MFA Bypass on a Protected Account"]).format(**e)
        context = _text_piece(pick([
            "{acct} is required to use multi-factor authentication by policy.",
            "The policy export marks {acct} as MFA-required.",
            "Security policy mandates a second factor for {acct}."]), **e)
        incident = _text_piece(pick([
            "A successful sign-in from {host} showed no second factor.",
            "{acct} logged in from {host} and the event records that MFA was not used.",
            "The login from {host} succeeded without any MFA challenge being satisfied."]), **e)
        rule_piece = _text_piece(pick([
            "Alert when a successful login is recorded without MFA for an account whose policy requires MFA.",
            "The rule should fire on a successful sign-in with no second factor when the account's policy mandates it.",
            "Flag any successful login lacking MFA on an MFA-required account."]))
        names = list(f.required_fields) + list(f.policy_fields)

    return {"title": _text_piece(title), "context": context, "incident": incident, "rule": rule_piece,
            "fields": _fields_piece(f, rng, names), "excluded": _excluded_piece(f, rng),
            "distractor": _distractor_piece(f, rng)}


def pieces_for(f: Facts, rng: random.Random) -> dict:
    if f.supported:
        return _supported_pieces(f, rng)
    from .unsupported import unsupported_pieces
    return unsupported_pieces(f, rng)


# --------------------------------------------------------------------------- styles
def _opt(doc: Doc, piece, prefix: str = "") -> None:
    if piece is not None:
        doc.add(prefix)
        piece(doc)


def render(f: Facts, style: str, rng: random.Random, report_id: str) -> tuple[str, list, dict]:
    """Return ``(text, spans, structure)``; the doc is verified before it is returned."""
    p = pieces_for(f, rng)
    doc = Doc()
    heading = rng.choice(PARAM_HEADINGS)
    m = f.meta
    if style == "advisory":
        doc.add("# ")
        p["title"](doc)
        doc.add(f"\n\n- **Report ID:** {report_id}\n- **Source:** Synthetic corpus report (family {f.family_id})\n"
                f"- **ATT&CK Technique:** {f.technique}\n\n## Narrative\n\n")
        p["context"](doc); doc.add(" "); p["incident"](doc); _opt(doc, p["distractor"], " ")
        doc.add(f"\n\n## {heading}\n\n- ")
        p["rule"](doc); doc.add("\n- "); p["fields"](doc); _opt(doc, p["excluded"], "\n- ")
        if "note" in p:
            _opt(doc, p["note"], "\n- ")
    elif style == "soc-ticket":
        doc.add(f"**Ticket {m['ticket']}** | Severity: {m['severity']} | Analyst: {m['analyst']}\n\n**Summary:** ")
        p["context"](doc); doc.add("\n\n**Observed:** "); p["incident"](doc)
        doc.add("\n\n**Requested detection:** "); p["rule"](doc)
        doc.add("\n\n**Data needed:** "); p["fields"](doc)
        if p["excluded"] is not None or p["distractor"] is not None:
            doc.add("\n\n**Notes:** ")
            _opt(doc, p["excluded"]); _opt(doc, p["distractor"], " " if p["excluded"] else "")
        if "note" in p:
            _opt(doc, p["note"], "\n\n")
    elif style == "postmortem":
        doc.add("# "); p["title"](doc); doc.add("\n\nSummary\n\n")
        p["context"](doc); doc.add(" "); p["incident"](doc); _opt(doc, p["distractor"], " ")
        doc.add("\n\nDetection follow-up\n\n"); p["rule"](doc); doc.add(" "); p["fields"](doc)
        _opt(doc, p["excluded"], " ")
        if "note" in p:
            _opt(doc, p["note"], " ")
    else:  # handoff-chat
        doc.add(f"quick handoff on {m['ticket']} ({m['analyst']} to next shift):\n\n")
        p["context"](doc); doc.add(" "); p["incident"](doc)
        doc.add("\n\nask: "); p["rule"](doc); doc.add("\n"); p["fields"](doc); _opt(doc, p["excluded"], " ")
        if "note" in p:
            _opt(doc, p["note"], " ")
    doc.verify()
    return doc.text(), doc.spans, {"hasParamSection": style == "advisory" and heading == PARAM_HEADINGS[1]}


# --------------------------------------------------------------------------- gold
def gold_record(f: Facts, report_id: str, report_file: str, style: str, tier: str, spans: list,
                structure: dict) -> dict:
    base = {"reportId": report_id, "reportFile": report_file, "familyId": f.family_id, "style": style, "tier": tier,
            "hasParamSection": structure["hasParamSection"], "supported": f.supported, "spans": spans}
    if not f.supported:
        return {**base, "behaviourId": None, "requiredFields": [], "policyFields": [], "excludedFields": [],
                "threshold": None, "timeWindow": None, **f.unsupported}
    return {**base, "behaviourId": f.behaviour,
            "requiredFields": sorted(f.required_fields), "policyFields": sorted(f.policy_fields),
            "excludedFields": sorted(f.excluded_fields),
            "threshold": {f.threshold_key: f.threshold} if f.threshold is not None else None,
            "timeWindow": {"amount": f.window_amount, "unit": f.window_unit} if f.window_amount is not None else None,
            "fieldMode": f.field_mode, "numMode": f.num_mode, "thresholdForm": f.threshold_form,
            "windowForm": f.window_form}
