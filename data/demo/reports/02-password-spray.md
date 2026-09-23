# Threat brief: low-and-slow password spraying from a rented host

An external host (we saw it as ATTACKER-x in the auth log) tried one password each against many different staff accounts over about a quarter of an hour, deliberately staying under the per-account lockout threshold. Individually each account only failed once, so per-account rules never fired.

**Detection guidance:** treat **4 or more distinct accounts failing from the same source host within 10 minutes** as password spraying. Count accounts, not attempts.

Fields required: `account_id`, `event_type`, `timestamp` and `source_host`.
