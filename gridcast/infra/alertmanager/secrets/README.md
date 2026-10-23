# Alertmanager secrets (never committed)

Git ignores everything in this folder except this file. Alertmanager reads two files from here:

| File | Contents |
|---|---|
| `lumis-url` | The Core's Alertmanager endpoint for your estate, on one line: `http://host.docker.internal:8200/v1/estates/<estate>/alerts/alertmanager` |
| `lumis-key` | The key from the Lumis console's onboarding (**Send alerts → Make the key**). It is shown once; paste it here, on one line. |

`host.docker.internal` is how the Alertmanager container reaches a Core running on your Mac.

```sh
cd gridcast/infra/alertmanager/secrets
printf '%s' 'http://host.docker.internal:8200/v1/estates/<estate>/alerts/alertmanager' > lumis-url
pbpaste > lumis-key        # after copying the key from the console
chmod 600 lumis-key
```

Both files are read when a notification is sent, so there is no restart. Check delivery in
Alertmanager's log: `docker logs gridcast-alertmanager`.
