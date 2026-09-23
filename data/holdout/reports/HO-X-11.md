```
SELECT account_id FROM auth WHERE outcome = 'fail' GROUP BY account_id HAVING COUNT(*) >= 5
```
Please turn this query into a rule.
