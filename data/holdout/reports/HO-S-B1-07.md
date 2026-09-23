detection request
name: burst-fail-then-success
event: failed login (per account)
count: 5 or more
window: 3 minutes
then: successful login for the same account
notify: soc-oncall
