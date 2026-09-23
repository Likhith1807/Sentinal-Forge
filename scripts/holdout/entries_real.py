"""Real public report passages for the frozen holdout.

Source: CISA / FBI joint cybersecurity advisories. Works of the US Government are public domain (17 U.S.C. 105;
see https://www.cisa.gov/privacy-policy#copyright), so short verbatim excerpts are permitted. Every passage is
extracted verbatim from the page as fetched on the build date (data/holdout/_src/*.txt, not committed) between
two anchor phrases, and cited with URL and title in the manifest.

Real advisories almost never state an executable threshold ("5 failures in 2 minutes"): they describe tactics and
give recommendations. The correct behaviour for a system that compiles rules is therefore to REFUSE - and that is
exactly what these entries test: fluent, on-topic security text that must not be turned into a rule.
"""

AA21 = dict(file="aa21-116a.txt", url="https://www.cisa.gov/news-events/cybersecurity-advisories/aa21-116a",
            title="Russian Foreign Intelligence Service (SVR) Cyber Operations: Trends and Best Practices for Network Defenders (AA21-116A)")
AA22 = dict(file="aa22-074a.txt", url="https://www.cisa.gov/news-events/cybersecurity-advisories/aa22-074a",
            title="Russian State-Sponsored Cyber Actors Gain Network Access by Exploiting Default Multifactor Authentication Protocols and PrintNightmare Vulnerability (AA22-074A)")

REAL = [
    dict(id="HO-R-01", src=AA21, start="In one 2018 compromise of a large network", end="possibly to avoid detection.", reasonClass="ambiguous",
         why="Describes password spraying but states no count or window; a rule would need invented numbers."),
    dict(id="HO-R-02", src=AA21, start="The organization unintentionally exempted", end="requirements.", reasonClass="not-a-rule",
         why="An observation about a misconfiguration, not a detection condition."),
    dict(id="HO-R-03", src=AA21, start="Mandatory use of an approved multi-factor", end="remote locations.", reasonClass="not-a-rule",
         why="A preventive recommendation."),
    dict(id="HO-R-04", src=AA21, start="Ensure the organization", end="user account lockouts.", reasonClass="not-a-rule",
         why="An operational recommendation about lockout procedures."),
    dict(id="HO-R-05", src=AA21, start="In a separate incident, SVR actors used CVE-2019-19781", end="using the exposed credentials.", reasonClass="unsupported-behaviour",
         why="Exploitation of a VPN appliance; not an authentication-log pattern."),
    dict(id="HO-R-06", src=AA21, start="Require use of multi-factor authentication to access internal systems.", end="internal systems.", reasonClass="not-a-rule",
         why="A preventive recommendation."),
    dict(id="HO-R-07", src=AA22, start="MFA is one of the most important cybersecurity practices", end="account compromised.", reasonClass="not-a-rule",
         why="A statistic and an opinion."),
    dict(id="HO-R-08", src=AA22, start="Organizations that implement MFA should review default configurations", end="circumvent this control.", reasonClass="not-a-rule",
         why="A recommendation about configuration review."),
    dict(id="HO-R-09", src=AA22, start="As early as May 2021, the FBI observed Russian state-sponsored", end="cloud environment.", reasonClass="not-a-rule",
         why="A narrative of an incident."),
    dict(id="HO-R-10", src=AA22, start="“fail open” can happen to any MFA implementation", end="exclusive to Duo.", reasonClass="not-a-rule",
         why="A general observation about MFA failure modes."),
    dict(id="HO-R-11", src=AA22, start="After effectively disabling MFA, Russian state-sponsored", end="to Windows domain controllers", reasonClass="ambiguous",
         why="Describes logins without MFA in an incident, but states no policy-comparison rule."),
    dict(id="HO-R-12", src=AA22, start="Implement time-out and lock-out features", end="failed login attempts.", reasonClass="not-a-rule",
         why="A preventive control (lockout), not a detection."),
    dict(id="HO-R-13", src=AA22, start="Continuously monitor network logs", end="unusual login attempts.", reasonClass="ambiguous",
         why="Asks for monitoring of 'unusual login attempts' with no definition of unusual."),
    dict(id="HO-R-14", src=AA22, start="Enforce MFA for all users, without exception.", end="re-enrollment scenarios.", reasonClass="not-a-rule",
         why="A preventive recommendation."),
    dict(id="HO-R-15", src=AA22, start="When possible, implement multi-factor authentication on all VPN connections.", end="use strong passwords.", reasonClass="not-a-rule",
         why="A preventive recommendation."),
    dict(id="HO-R-16", src=AA22, start="Ensure inactive accounts are disabled uniformly", end="MFA systems etc.", reasonClass="not-a-rule",
         why="An account-hygiene recommendation."),
]
