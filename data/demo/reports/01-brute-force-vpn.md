# Advisory: Credential guessing against the VPN concentrator

**Summary.** Over the weekend our identity team saw a run of failed sign-ins against a single contractor account that ended in a successful login from an unfamiliar workstation. The account had 14 failures across roughly nine minutes before it got in, which is well outside normal typo behaviour (most users fail once or twice).

**What to detect.** Raise an alert when an account accumulates **5 or more failed logins inside a 2-minute window** and is then followed by a successful login for the same account. Earlier activity in the week (a couple of isolated typos, two successful logins) should not trigger anything.

**Data needed.** The detection reads `account_id`, `event_type` and `timestamp` from the authentication log. The `source_ip` field is often empty for internal sessions and is not used here.
