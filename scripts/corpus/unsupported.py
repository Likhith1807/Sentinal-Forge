"""Out-of-scope / unavailable-field report families: the correct answer is to abstain.

Several deliberately resemble an in-scope behaviour (impossible travel looks like B3, account
lockouts look like B1, a spray keyed on a user agent looks like B2) but need a field the
authentication log schema does not provide. A system that maps them to an in-scope behaviour
has overreached; Stage 3 is supposed to reject them.
"""
from __future__ import annotations

import random

from .doc import Doc, Tagged
from .facts import Facts, _common_meta, _host, _person

# name, title, context, incident, rule, needs [(schema-external field, natural phrase)], looks_like
TOPICS = [
    ("dns-tunnelling", "Suspected DNS Tunnelling From {host}",
     "{host} generated an unusual volume of long DNS lookups to a single domain.",
     "Analysts saw {n} queries with names over {len} characters in a short period.",
     "Alert when one host issues more than {t} DNS queries with names longer than {len} characters within {w} minutes.",
     [("dns_query", "the DNS query name"), ("query_length", "the length of each query")], None),
    ("data-exfiltration-volume", "Large Outbound Transfer From {host}",
     "Egress monitoring flagged sustained high-volume traffic leaving {host}.",
     "About {n} MB left the host toward one external address in under an hour.",
     "Alert when outbound transfer from a single host exceeds {t} MB within {w} minutes.",
     [("bytes_out", "the outbound byte count"), ("destination_ip", "the destination address")], None),
    ("encoded-powershell", "Encoded PowerShell Launched on {host}",
     "Endpoint telemetry recorded PowerShell starting with an encoded command on {host}.",
     "The parent process was a document reader and the command line was {n} characters long.",
     "Alert whenever powershell.exe starts with an encoded command argument.",
     [("process_name", "the process name"), ("command_line", "the full command line")], None),
    ("impossible-travel", "Impossible Travel for {acct}",
     "The account {acct} signed in successfully from two countries within a short window.",
     "The first success geolocated to one country and the second, {gap} minutes later, to another.",
     "Alert when the same account has successful logins from two different countries within {w} minutes.",
     [("geo_country", "the country each sign-in geolocates to")], "multi-host-authentication"),
    ("ransomware-encryption", "Mass File Renames Consistent With Ransomware on {host}",
     "File-server auditing showed many files renamed with a new extension on {host}.",
     "In the observed burst, {n} files were renamed within a few minutes.",
     "Alert when more than {t} files are renamed to a new extension within {w} minutes on one host.",
     [("file_path", "the path of each file"), ("file_extension", "the new file extension")], None),
    ("phishing-click", "Phishing Link Clicked by {acct}",
     "A user reported an email that led to a credential-harvesting page.",
     "Proxy logs show {acct} followed the link and {n} other recipients received the message.",
     "Alert when a user visits a URL that appears in a message from a newly registered sender domain.",
     [("email_subject", "the message subject"), ("sender_domain", "the sender's domain"), ("url_clicked", "the URL visited")], None),
    ("privilege-escalation", "Unexpected Addition to Domain Admins",
     "Directory auditing recorded {acct} being added to a privileged group outside change control.",
     "The change came from a workstation at {host} and {gap} minutes later the account was used.",
     "Alert when any user is added to the Domain Admins group outside an approved change window.",
     [("group_name", "the group being modified"), ("target_user", "the account that was added")], None),
    ("port-scan", "Internal Port Scan From {host}",
     "Network sensors saw {host} probing many ports on neighbouring machines.",
     "It touched {n} distinct destination ports in well under a minute.",
     "Alert when one host connects to more than {t} distinct destination ports within {w} minutes.",
     [("destination_port", "the destination port"), ("dst_ip", "the destination host")], None),
    ("lockout-storm", "Account Lockout Storm Against the Directory",
     "Helpdesk received a wave of lockout tickets that matched a guessing campaign.",
     "The directory recorded {n} lockouts across the day, concentrated in one hour.",
     "Alert when {t} account lockout events (event 4740) occur within {w} minutes.",
     [("lockout_reason", "the lockout reason code"), ("locked_account", "the locked account")], "repeated-failed-login-then-success"),
    ("usb-mass-storage", "Removable Storage Attached to {host}",
     "Endpoint policy flagged a USB mass-storage device connecting to {host}.",
     "The device was attached for {gap} minutes outside working hours.",
     "Alert when a removable storage device is attached to any server.",
     [("device_id", "the device identifier"), ("device_class", "the device class")], None),
]


# What a real report about each topic would cite. Deliberately NOT "n/a": a report that announced it was
# outside the supported behaviours would leak the label into the text.
TECHNIQUE = {
    "dns-tunnelling": "T1071.004 (Application Layer Protocol: DNS)",
    "data-exfiltration-volume": "T1048 (Exfiltration Over Alternative Protocol)",
    "encoded-powershell": "T1059.001 (Command and Scripting Interpreter: PowerShell)",
    "impossible-travel": "T1078 (Valid Accounts)",
    "ransomware-encryption": "T1486 (Data Encrypted for Impact)",
    "phishing-click": "T1566.002 (Phishing: Spearphishing Link)",
    "privilege-escalation": "T1098 (Account Manipulation)",
    "port-scan": "T1046 (Network Service Discovery)",
    "lockout-storm": "T1110 (Brute Force)",
    "usb-mass-storage": "T1091 (Replication Through Removable Media)",
}


def sample_unsupported(family_no: int, rng: random.Random) -> Facts:
    name, *_rest = TOPICS[(family_no - 1) % len(TOPICS)]
    needs, looks_like = _rest[4], _rest[5]      # (title, context, incident, rule, needs, looks_like)
    return Facts(
        family_id=f"F-U-{family_no:03d}", behaviour=None, technique=TECHNIQUE[name],
        required_fields=[], field_mode="natural" if rng.random() < 0.5 else "code",
        num_mode="digits", meta=_common_meta(rng), supported=False,
        entities={"host": _host(rng), "acct": _person(rng), "topic": name},
        observed={"n": rng.randint(40, 900), "len": rng.choice([50, 60, 80]), "t": rng.choice([50, 100, 200, 500]),
                  "w": rng.choice([5, 10, 30]), "gap": rng.randint(5, 90)},
        unsupported={"unsupportedReason": "requires fields the authentication log schema does not provide",
                     "unavailableFields": [n for n, _ in needs], "looksLike": looks_like, "topic": name})


def unsupported_pieces(f: Facts, rng: random.Random) -> dict:
    topic = next(t for t in TOPICS if t[0] == f.entities["topic"])
    _, title, context, incident, rule, needs, _looks = topic
    slots = {**f.entities, **f.observed}
    items = []
    for name, phrase in needs:
        items.append(["`", Tagged.of(name, "unavailable_field", field=name), "`"] if f.field_mode == "code"
                     else [Tagged.of(phrase, "unavailable_field", field=name)])
    lst: list = []
    for i, item in enumerate(items):
        if i:
            lst.append(" and " if i == len(items) - 1 else ", ")
        lst += item
    fields_template = ("Required log fields: {lst}." if f.field_mode == "code"
                       else "Evaluating the rule requires {lst}.")
    return {
        "title": lambda doc: doc.add(title.format(**slots)),
        "context": lambda doc: doc.fill(context, **slots),
        "incident": lambda doc: doc.fill(incident, **slots),
        "rule": lambda doc: doc.fill(rule, **slots),
        "fields": lambda doc: doc.fill(fields_template, lst=lst),
        "excluded": None,
        "distractor": lambda doc: doc.fill("Ticket {ticket} was opened at {hhmm} UTC and assigned to {analyst}.", **f.meta),
    }
