"""Holdout v2 - real public passages (CISA/FBI joint advisories; US Government works, public domain).

Extracted verbatim between anchor phrases from pages fetched on the build date (scripts/holdout/fetch_sources.py).
Correct behaviour for every entry: no compiled rule - they are tactics and recommendations, not detection conditions.
"""

A23_320 = dict(file="aa23-320a.txt", url="https://www.cisa.gov/news-events/cybersecurity-advisories/aa23-320a",
               title="Scattered Spider (AA23-320A)")
A22_321 = dict(file="aa22-321a.txt", url="https://www.cisa.gov/news-events/cybersecurity-advisories/aa22-321a",
               title="#StopRansomware: Hive Ransomware (AA22-321A)")

REAL = [
    dict(id="HO2-R-01", src=A23_320, start="Sent repeated MFA notification prompts", end="(also known as MFA fatigue)", reasonClass="unsupported-behaviour",
         why="Describes MFA push fatigue; the MFA provider's prompt events are not in the authentication log."),
    dict(id="HO2-R-02", src=A23_320, start="Enforce account lockouts after a specified number of attempts.", end="specified number of attempts.", reasonClass="not-a-rule",
         why="A preventive control, not a detection."),
    dict(id="HO2-R-03", src=A23_320, start="Implement FIDO/WebAuthn authentication", end="based MFA.", reasonClass="not-a-rule", why="A recommendation."),
    dict(id="HO2-R-04", src=A23_320, start="Scattered Spider threat actors use voice communications", end="reset passwords and/or MFA tokens.", reasonClass="not-a-rule",
         why="Social-engineering narrative."),
    dict(id="HO2-R-05", src=A22_321, start="Monitor remote access/RDP logs", end="remote access/RDP ports.", reasonClass="ambiguous",
         why="Asks for monitoring and lockouts but states no count or window."),
    dict(id="HO2-R-06", src=A22_321, start="Implement multiple failed login attempt account lockouts.", end="account lockouts.", reasonClass="not-a-rule",
         why="A preventive control."),
    dict(id="HO2-R-07", src=A22_321, start="After assessing risks, if you deem RDP operationally necessary", end="credential theft and reuse.", reasonClass="not-a-rule",
         why="A recommendation."),
    dict(id="HO2-R-08", src=A22_321, start="Enable and enforce multifactor authentication with strong passwords.", end="strong passwords.", reasonClass="not-a-rule",
         why="A recommendation."),
    dict(id="HO2-R-09", src=A23_320, start="Finally, the threat actors conduct spearphising", end="transfer MFA tokens", reasonClass="not-a-rule", why="Narrative."),
    dict(id="HO2-R-10", src=A23_320, start="Scattered Spider threat actors may modify MFA tokens", end="targeted organization", reasonClass="not-a-rule", why="Technique description."),
    dict(id="HO2-R-11", src=A23_320, start="Scattered Spider sends repeated MFA notification prompts", end="target network.", reasonClass="unsupported-behaviour",
         why="MFA push fatigue again, described as a technique."),
]
