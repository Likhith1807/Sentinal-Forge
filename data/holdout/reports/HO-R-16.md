Ensure inactive accounts are disabled uniformly across the Active Directory and MFA systems.
Patch all systems. Prioritize patching for
For more general information on Russian state-sponsored malicious cyber activity, see CISA's
Russia Cyber Threat Overview and Advisories
webpage. For more information on the threat of Russian state-sponsored malicious cyber actors to U.S. critical infrastructure as well as additional mitigation recommendations, see joint CSA
Understanding and Mitigating Russian State-Sponsored Cyber Threats to U.S. Critical Infrastructure
This advisory uses the MITRE ATT&CK® for Enterprise framework, version 10. See Appendix A for a table of the threat actors’ activity mapped to MITRE ATT&CK tactics and techniques.
As early as May 2021, the FBI observed Russian state-sponsored cyber actors gain access to an NGO, exploit a flaw in default MFA protocols, and move laterally to the NGO’s cloud environment.
Russian state-sponsored cyber actors gained initial access [
] to the victim organization via compromised credentials [
] and enrolling a new device in the organization’s Duo MFA. The actors gained the credentials [
] via brute-force password guessing attack [
], allowing them access to a victim account with a simple, predictable password. The victim account had been un-enrolled from Duo due to a long period of inactivity but was not disabled in the Active Directory. As Duo’s default configuration settings allow for the re-enrollment of a new device for dormant accounts, the actors were able to enroll a new device for this account, complete the authentication requirements, and obtain access to the victim network.
Using the compromised account, Russian state-sponsored cyber actors performed privilege escalation [
] via exploitation of the “PrintNightmare” vulnerability (
] to obtain administrator privileges. The actors also modified a domain controller file,
]. This change prevented the MFA service from contacting its server to validate MFA login—this effectively disabled MFA for active domain accounts because the default policy of Duo for Windows is to “Fail open” if the MFA server is unreachable.
“fail open” can happen to any MFA implementation and is not exclusive to Duo.
After effectively disabling MFA, Russian state-sponsored cyber actors were able to successfully authenticate to the victim’s virtual private network (VPN) as non-administrator users and make Remote Desktop Protocol (RDP) connections to Windows domain controllers [
]. The actors ran commands to obtain credentials for additional domain accounts; then using the method described in the previous paragraph, changed the MFA configuration file and bypassed MFA for these newly compromised accounts. The actors leveraged mostly internal Windows utilities already present within the victim network to perform this activity.
Using these compromised accounts without MFA enforced, Russian state-sponsored cyber actors were able to move laterally [
] to the victim’s cloud storage and email accounts and access desired content.
Russian state-sponsored cyber actors executed the following processes:
- A core Windows Operating System process used to perform the Transmission Control Protocol (TCP)/IP Ping command; used to test network connectivity to a remote host [
] and is frequently used by actors for network discovery [
- A standard Windows executable file that opens the built-in registry editor [
- A data compression, encryption, and archiving tool [
]. Malicious cyber actors have traditionally sought to compromise MFA security protocols as doing so would provide access to accounts or information of interest.
- A command-line tool that provides management facilities for Active Directory Domain Services. It is possible this tool was used to enumerate Active Directory user accounts [
Actors modified the c:\windows\system32\drivers\etc\hosts file to prevent communication with the Duo MFA server:
The following access device IP addresses used by the actors have been identified to date:
The FBI and CISA recommend organizations remain cognizant of the threat of state-sponsored cyber actors exploiting default MFA protocols and exfiltrating sensitive information. Organizations should:
Enforce MFA for all users, without exception. Before implementing, organizations should review configuration policies to protect against “fail open” and re-enrollment scenarios.
Implement time-out and lock-out features in response to repeated failed login attempts.
Ensure inactive accounts are disabled uniformly across the Active Directory, MFA systems etc.
