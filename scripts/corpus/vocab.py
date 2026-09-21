"""Field vocabulary (single-sourced from nlp/src/schema_fields.py) and surface-form pools."""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_schema_fields():
    spec = importlib.util.spec_from_file_location("schema_fields", REPO_ROOT / "nlp" / "src" / "schema_fields.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_sf = _load_schema_fields()
ALL_FIELDS = list(_sf.ALL_FIELDS)
BEHAVIOUR_IDS = list(_sf.BEHAVIOUR_IDS)

B1, B2, B3, B4, B5 = BEHAVIOUR_IDS

# Gold threshold key per behaviour (the names the bridge accepts; see spec_bridge.EXPECTED_THRESHOLD_KEYS).
THRESHOLD_KEY = {B1: "failureCount", B2: "distinctAccountCount", B3: "successCount"}

# How each schema field can be mentioned in natural language (no backticks).
NATURAL_PHRASES = {
    "account_id": ["the account identifier", "the username", "the account being authenticated", "the account name"],
    "event_type": ["the sign-in outcome", "whether each attempt succeeded or failed", "the login result",
                   "the success/failure status of each event"],
    "timestamp": ["the event time", "the time of each attempt", "event timestamps", "when each event occurred"],
    "source_host": ["the originating host", "the machine each attempt came from", "the source workstation or gateway",
                    "the host the request originated from"],
    "source_ip": ["the source IP address", "the originating IP", "the client IP"],
    "auth_method": ["the authentication method", "how the account authenticated", "the credential type used"],
    "mfa_used": ["whether a second factor was used", "MFA usage on the sign-in",
                 "whether multi-factor authentication was presented"],
    "policy.expected_auth_method": ["the method the account is provisioned for in the identity policy",
                                    "the expected authentication method from the policy export"],
    "policy.mfa_required": ["whether policy requires MFA for the account", "the account's MFA requirement in the policy"],
}

CODE_FORM = {f: (f"`{f.split('.')[-1]}`" if f.startswith("policy.") else f"`{f}`") for f in ALL_FIELDS}

NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight",
                9: "nine", 10: "ten", 11: "eleven", 12: "twelve", 15: "fifteen", 20: "twenty", 30: "thirty"}

FIRST = ["amir", "bianca", "carlos", "dana", "elif", "farid", "grace", "hiro", "ines", "jamal", "kavya", "liam",
         "mei", "nadia", "omar", "priya", "quinn", "rosa", "sanjay", "tara", "uma", "viktor", "wen", "ximena",
         "yusuf", "zoe"]
LAST = ["okafor", "petrov", "nakamura", "silva", "haddad", "lindqvist", "mensah", "iyer", "duarte", "kowalski",
        "tanaka", "rahman", "moreau", "fischer", "osei", "vargas", "chen", "novak", "abdi", "brennan"]
SERVICE_PREFIX = ["svc-backup", "svc-etl", "svc-report", "svc-sync", "svc-monitor", "svc-deploy", "svc-notify",
                  "svc-batch", "svc-index", "svc-archive"]
HOST_PREFIX = ["EXT-VPN", "WIN-LAB", "BASTION", "SSO-GW", "CITRIX-GW", "RDP-EDGE", "JUMP", "MAIL-GW"]
ANALYSTS = ["R. Ibarra", "S. Whitfield", "T. Nguyen", "L. Adeyemi", "K. Sorensen", "M. Delacroix", "P. Banerjee"]
