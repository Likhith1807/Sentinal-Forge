"""Holdout v2 - SUPPORTED reports, authored AFTER the v1 failure analysis and the parser fixes it drove.

Different layouts again (email, ticket acceptance criteria, runbook steps, YAML / Sigma-like rules, checklists, chat,
narrative with distractor numbers) and deliberately some forms the fixes did NOT target (`min_distinct: 6`, `>5`,
`2m`, "a quarter of an hour", "8 failed logins or more"). Those are expected to be refused; they are here to keep the
recall number honest. Quotes must occur exactly once.
"""

B1 = "repeated-failed-login-then-success"
B2 = "password-spray-across-accounts"
B3 = "multi-host-authentication"
B4 = "auth-method-policy-violation"
B5 = "mfa-missing-on-required-account"

SUPPORTED = [
    # ---- B1
    dict(id="HO2-S-B1-01", style="email", behaviour=B1, count=("more than 6", 7), window=("10-minute period", 600),
         text="Hi Sam,\n\nCould you set up a detection for me? If any single user account gets more than 6 failed logins in a 10-minute period and then logs in successfully, I want it flagged as a possible guessed password.\n\nThanks,\nLeila"),
    dict(id="HO2-S-B1-02", style="jira-acceptance", behaviour=B1, count=("at least 3 failed logins", 3), window=("30 seconds", 30),
         text="**Summary:** Detect credential stuffing success\n\n**Acceptance criteria**\n1. An alert is created when one account has at least 3 failed logins within 30 seconds.\n2. The same account must then log in successfully.\n3. Alerts include the account and the supporting event ids."),
    dict(id="HO2-S-B1-03", style="runbook-step", behaviour=B1, count=("four or more", 4), window=("3-minute window", 180),
         text="Step 4 - Triage trigger. This runbook applies when the SIEM reports four or more unsuccessful logons for one identity within any 3-minute window, immediately followed by a successful logon for that identity."),
    dict(id="HO2-S-B1-04", style="sigma-like-yaml", behaviour=B1, count=(">= 15", 15), window=("timeframe: 2m", 120),
         text="title: Guess then success\ndetection:\n  failures:\n    event: login_failure\n  threshold: '>= 15'\n  timeframe: 2m\n  followed_by: login_success\n  group_by: account\n"),
    dict(id="HO2-S-B1-05", style="narrative-with-distractors", behaviour=B1, count=("12 or more", 12), window=("inside 60 seconds", 60),
         text="During the exercise the red team tried passwords against the finance director's account. They failed twelve times in under a minute before getting in, and the SOC saw nothing until the next morning.\n\nWe want an alert whenever an account has 12 or more failed logins inside 60 seconds and then a successful login. Everything else about the exercise can be ignored."),
    dict(id="HO2-S-B1-06", style="checklist", behaviour=B1, count=("8 failed logins or more", 8), window=("90 second interval", 90),
         text="- [ ] Alert on brute force success\n  - [ ] 8 failed logins or more (same account)\n  - [ ] within a 90 second interval\n  - [ ] then a successful login\n"),

    # ---- B2
    dict(id="HO2-S-B2-01", style="email", behaviour=B2, count=("5 or more different user names", 5), window=("quarter of an hour", 900),
         text="Subject: Spray detection\n\nOne source host generating failed logins against 5 or more different user names inside a quarter of an hour is spraying. Please alert on that."),
    dict(id="HO2-S-B2-02", style="jira-acceptance", behaviour=B2, count=("at least 8 distinct accounts", 8), window=("5 minutes", 300),
         text="**Acceptance criteria**\n- An alert fires when a single host has failed authentication for at least 8 distinct accounts in 5 minutes.\n- The alert names the host and lists the accounts."),
    dict(id="HO2-S-B2-03", style="runbook-step", behaviour=B2, count=("10 or greater", 10), window=("1 hour", 3600),
         text="Detection logic: group failed logins by source host; when the number of unique accounts in the group is 10 or greater within 1 hour, raise an alert."),
    dict(id="HO2-S-B2-04", style="sigma-like-yaml", behaviour=B2, count=("min_distinct: 6", 6), window=("timeframe: 20 minutes", 1200),
         text="detection:\n  event: login_failure\n  group_by: source_host\n  distinct: account_id\n  min_distinct: 6\n  timeframe: 20 minutes\n"),
    dict(id="HO2-S-B2-05", style="narrative-with-distractors", behaviour=B2, count=("more than 3 distinct accounts", 4), window=("10 minute span", 600),
         text="The scanner tried 500 passwords over two days, one per account, from a single rented machine. Individually nothing looked unusual.\n\nRaise an alert if a host racks up failures on more than 3 distinct accounts over a 10 minute span."),
    dict(id="HO2-S-B2-06", style="chat-symbols", behaviour=B2, count=(">5 accounts", 6), window=("in 30 min", 1800),
         text="@soc-eng spray rule pls: >5 accounts failing from the same host in 30 min. account, outcome, time and host are all it needs"),

    # ---- B3
    dict(id="HO2-S-B3-01", style="email", behaviour=B3, count=("three or more different machines", 3), window=("20-minute window", 1200),
         text="Could we alert when one account authenticates successfully from three or more different machines within a 20-minute window? HR suspects a shared login."),
    dict(id="HO2-S-B3-02", style="jira-acceptance", behaviour=B3, count=("at least 2 distinct hosts", 2), window=("5 minutes", 300),
         text="**Acceptance criteria**\n1. Alert when the same user has successful logins on at least 2 distinct hosts within 5 minutes.\n2. Failed logins are ignored."),
    dict(id="HO2-S-B3-03", style="runbook-step", behaviour=B3, count=("4+ hosts", 4), window=("1 hour", 3600),
         text="Trigger: a single identity shows successful sign-ins from 4+ hosts inside 1 hour. Open a ticket and notify the account owner."),
    dict(id="HO2-S-B3-04", style="narrative-with-distractors", behaviour=B3, count=("no fewer than 3 distinct hosts", 3), window=("40 minutes", 2400),
         text="The credentials were shared between contractors. Each used a different laptop, so the same account ended up logging in from six different hosts over one afternoon.\n\nRule: same account, successful logins from no fewer than 3 distinct hosts in 40 minutes."),
    dict(id="HO2-S-B3-05", style="sigma-like-yaml", behaviour=B3, count=("distinct_hosts_min: 2", 2), window=("window: 10m", 600),
         text="rule: multi-host\nevent: login_success\ngroup_by: account\ndistinct_hosts_min: 2\nwindow: 10m\n"),
    dict(id="HO2-S-B3-06", style="formal-spec", behaviour=B3, count=("at least three (3) separate source hosts", 3), window=("ninety seconds", 90),
         text="SR-3. When successful authentications for one account originate from at least three (3) separate source hosts within ninety seconds, the SOC shall be notified."),

    # ---- B4
    dict(id="HO2-S-B4-01", style="email", behaviour=B4,
         text="Please alert us if anyone logs in with a different authentication method from what their account is set up for."),
    dict(id="HO2-S-B4-02", style="jira-acceptance", behaviour=B4,
         text="**Acceptance criteria**\n- An alert is raised when a successful login's auth_method does not equal the account's expected_auth_method in the policy table.\n- Direction does not matter."),
    dict(id="HO2-S-B4-03", style="runbook-step", behaviour=B4,
         text="Trigger: the authentication method on a successful login differs from the method recorded for that account in the identity system."),
    dict(id="HO2-S-B4-04", style="narrative-with-distractors", behaviour=B4,
         text="svc-etl should only ever use a certificate. On Tuesday it logged in with a password, from a host nobody recognised, at 03:12.\n\nWanted: a control that notices any login, by any account, that used something other than its registered method."),
    dict(id="HO2-S-B4-05", style="sigma-like-yaml", behaviour=B4,
         text="detect: successful_login\ncompare: auth_method != policy.expected_auth_method\nseverity: medium\n"),
    dict(id="HO2-S-B4-06", style="chat-shorthand", behaviour=B4,
         text="yo can u make an alert for logins where the auth type isnt what the policy says for that acct? any direction, either way"),

    # ---- B5
    dict(id="HO2-S-B5-01", style="email", behaviour=B5,
         text="Alert when an account that must use MFA signs in successfully but mfa_used is false."),
    dict(id="HO2-S-B5-02", style="jira-acceptance", behaviour=B5,
         text="**Acceptance criteria**\nGiven a successful login for an account whose policy requires MFA, when no second factor was used, then an alert is raised."),
    dict(id="HO2-S-B5-03", style="runbook-step", behaviour=B5,
         text="Trigger: login succeeded, MFA not used, policy.mfa_required = true."),
    dict(id="HO2-S-B5-04", style="narrative-with-distractors", behaviour=B5,
         text="Finance was told MFA is mandatory. Yet three of them logged in with only a password last month, and one of those logins came from a hotel network.\n\nWe need continuous detection of successful sign-ins where the user skipped MFA even though the policy demands it."),
    dict(id="HO2-S-B5-05", style="sigma-like-yaml", behaviour=B5,
         text="detect: login_success\nwhere:\n  mfa_used: false\n  policy.mfa_required: true\n"),
    dict(id="HO2-S-B5-06", style="chat-shorthand", behaviour=B5,
         text="mfa check pls - alert if login ok but no mfa and their policy says mfa is required"),
]
