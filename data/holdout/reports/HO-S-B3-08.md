rule: shared-credentials
event: successful login
group by: account
distinct hosts: 3 or more
window: 20 minutes
action: open ticket
