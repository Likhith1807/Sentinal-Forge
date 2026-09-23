## Ticket INC-40112: service account authenticating the wrong way

The identity export lists svc-deploy as certificate-only. A successful login for that account was recorded using a password. This deviation from the provisioned method is what we want to catch.

**Requested detection:** flag any successful login whose authentication method does not match the method recorded for the account in the policy export - in either direction.

To evaluate this the log has to provide `account_id`, `event_type` and `auth_method`, and the policy export has to provide `expected_auth_method`.
