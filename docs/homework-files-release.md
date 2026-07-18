# Homework files release checklist

1. Run `migrations/014_homework_submissions.sql` before starting the new app revision. The migration stops on duplicate homework sessions or duplicate non-null proctor groups and never merges them.
2. Set the S3 and VAPID variables from `.env.example`. Keep the bucket private, versioning disabled and do not expose credentials to the frontend.
3. Add an S3 lifecycle rule that permanently deletes `staging/` objects after 2 days. Application jobs normally remove successful/cancelled staging objects themselves; the rule is the terminal-error safety net.
4. Set `NEXT_PUBLIC_VAPID_PUBLIC_KEY` in the frontend to the public half of the same VAPID key.
5. Keep the existing Timeweb command unchanged. Each Gunicorn process starts an embedded runner; MySQL `GET_LOCK` permits one heavy PDF operation globally.
6. Run the smoke test with a key below `_test/` only. `tests/homework_s3_guard.py` rejects every other destructive test key.

Suggested lifecycle policy (adapt field names to the Timeweb S3 UI):

```json
{
  "Rules": [
    {
      "ID": "expire-homework-staging",
      "Status": "Enabled",
      "Filter": { "Prefix": "staging/" },
      "Expiration": { "Days": 2 }
    }
  ]
}
```
