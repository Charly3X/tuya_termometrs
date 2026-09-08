# Collector on the Oracle server

## Identify the machine first

```bash
cat /etc/os-release
```

Oracle Linux uses `firewalld`, Ubuntu and Debian use `ufw`. The commands below
show both.

## Install

```bash
sudo useradd -r -m -d /opt/tuya tuya
sudo -u tuya git clone <repo-url> /opt/tuya
cd /opt/tuya
sudo -u tuya python3 -m venv venv
sudo -u tuya ./venv/bin/pip install -r requirements.txt
```

## Configure

```bash
sudo -u tuya cp settings.json.example settings.json
sudo -u tuya cp config.json.example config.json
```

`settings.json` needs no edits on the server: `server.database` already points
at `/opt/tuya/readings.db`, and `history_server` is deliberately empty — that
one is for the desktop, which fills in `http://SERVER_IP:8080` so the widget
pulls its chart from here. Leaving it set on the server, or on a desktop with
no collector, only buys a `history_timeout` wait before the local fallback.

Edit `config.json`: fill in the device ids and `local_sockets`, and generate
the API token:

```bash
./venv/bin/python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Put that value in `config.json` as `history_token`, and the same value in the
desktop's `config.json`.

## Log in to Smart Life

Run this **on the server**, not by copying `sharing_token.json` from the
desktop. The refresh token rotates, and two clients sharing one session evict
each other.

```bash
sudo -u tuya ./venv/bin/python3 tuya_auth.py
```

## Import the old history (optional, once)

Copy `power_history.json` from the desktop, then:

```bash
sudo -u tuya ./venv/bin/python3 -m server.import_legacy power_history.json readings.db
```

`import_legacy` refuses to run a second time: before writing anything it
checks whether the target database already holds rows for any device present
in the source file, and if it does, it prints the conflicting device ids with
their row counts, exits non-zero, and writes nothing. There is no uniqueness
constraint on the table, so a second unguarded import would silently
duplicate every historical row rather than fail loudly. This is deliberate —
running the command twice by mistake (a repeated deploy step, a retried
script) must not corrupt history. Only pass `--force` to skip the check if
you have verified by hand that re-importing is what you actually want (for
example, the database was pruned and you are deliberately reloading the same
source):

```bash
sudo -u tuya ./venv/bin/python3 -m server.import_legacy power_history.json readings.db --force
```

`source` and `database` are positional arguments, in that order, on both
invocations.

## Start the services

```bash
sudo cp server/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tuya-collector tuya-api
journalctl -u tuya-collector -f
```

## Open the port

Two places, and the second is the one people forget. Oracle images ship with
iptables rules that drop everything except port 22, so opening the Security
List alone is not enough.

1. Oracle console: VCN → Security List → add an ingress rule for TCP 8080.
2. On the machine:

```bash
# Oracle Linux
sudo firewall-cmd --permanent --add-port=8080/tcp && sudo firewall-cmd --reload

# Ubuntu / Debian
sudo ufw allow 8080/tcp
```

Check from the desktop:

```bash
curl -H "Authorization: Bearer <token>" \
  "http://<server-ip>:8080/series?device=<socket-id>&metric=power&hours=1"
```

## Gaps in the data are normal

Tuya pushes only the data points whose value actually changed, so a plug
sitting at a constant load produces no `power` rows at all until the load
moves. Absence of rows means unchanged, not offline.

## Nightly retention

```bash
sudo -u tuya crontab -e
```

```
17 4 * * * cd /opt/tuya && /opt/tuya/venv/bin/python3 -m server.prune --database /opt/tuya/readings.db --yes
```

The age comes from `server.retention_days` in `settings.json`, so change it
there rather than in the cron line. This run does not `VACUUM`: in steady
state it deletes about as much as the collector inserts, so there is nothing
to reclaim, and vacuuming a multi-gigabyte database holds an exclusive lock
long enough for the collector's writes to time out and be lost.

**Lowering `retention_days` frees rows, not disk.** The most likely reason to
lower it is a full disk, but SQLite does not shrink the file on `DELETE` --
freed pages go to its freelist for reuse, not back to the filesystem. If you
actually need the space back, add `--vacuum` to force one regardless of
selection mode (same exclusive-lock cost as above, so pick a quiet moment):

```bash
sudo -u tuya /opt/tuya/venv/bin/python3 -m server.prune \
  --database /opt/tuya/readings.db --yes --vacuum
```

## Deleting a period by hand

Dry run first — this is the default:

```bash
sudo -u tuya /opt/tuya/venv/bin/python3 -m server.prune \
  --database /opt/tuya/readings.db --from 2026-09-01 --to 2026-09-03
```

Add `--yes` to actually delete, `--device` or `--metric` to narrow it. Unlike
the nightly run, this one vacuums afterwards, so the freed space actually
returns to the filesystem.
