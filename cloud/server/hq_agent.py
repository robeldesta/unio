#!/usr/bin/env python3
"""Unio HQ server agent (cron, every 5 minutes, as root): measures the server and reports to HQ through the queue folder
shared with Odoo (QUEUE/hq/*.jsonl, read in by HQ every 5 minutes).

Reports: CPU, memory, disk, load; each Unio container's CPU and memory; new ERROR / CRITICAL lines of the Unio Odoo log;
the latest nightly backups; the expiry of the Unio certificates. Standard library only."""
import json
import os
import re
import shutil
import subprocess
import sys
import time

QUEUE = os.environ.get('UNIO_QUEUE', '/opt/unio/queue')
OUT = os.path.join(QUEUE, 'hq')
STATE = os.path.join(OUT, '.agent_state.json')
CONTAINER = os.environ.get('UNIO_ODOO_CONTAINER', 'unio-odoo-1')
BACKUPS = os.environ.get('UNIO_BACKUPS', '/opt/unio/backups/nightly')


def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ''


def cpu_percent():
    def snap():
        with open('/proc/stat') as f:
            v = [int(x) for x in f.readline().split()[1:]]
        return sum(v), v[3] + v[4]
    try:
        t1, i1 = snap()
        time.sleep(1)
        t2, i2 = snap()
        return round(100.0 * (1 - (i2 - i1) / max(t2 - t1, 1)), 1)
    except OSError:
        return None


def mem_percent():
    try:
        info = {}
        with open('/proc/meminfo') as f:
            for line in f:
                k, v = line.split(':')
                info[k] = int(v.split()[0])
        return round(100.0 * (1 - info['MemAvailable'] / info['MemTotal']), 1)
    except (OSError, KeyError):
        return None


def emit(name, records):
    if not records:
        return
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, name), 'a') as f:
        for r in records:
            f.write(json.dumps(r) + '\n')


def main():
    now = time.time()
    state = {}
    if os.path.exists(STATE):
        try:
            state = json.load(open(STATE))
        except ValueError:
            state = {}
    du = shutil.disk_usage('/')
    load1 = os.getloadavg()[0] if hasattr(os, 'getloadavg') else None
    containers = {}
    for line in run(['docker', 'stats', '--no-stream', '--format', '{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}']).splitlines():
        parts = line.split('|')
        if len(parts) == 3 and parts[0].startswith('unio'):
            containers[parts[0]] = {'cpu': parts[1], 'mem': parts[2].split('/')[0].strip()}
    emit('metrics.jsonl', [{'type': 'metric', 'ts': now, 'cpu': cpu_percent(), 'mem': mem_percent(),
                            'disk': round(100.0 * du.used / du.total, 1), 'load1': round(load1, 2) if load1 is not None else None,
                            'detail': {'containers': containers, 'disk_free_gb': round(du.free / 1e9, 1)}}])
    # new errors in the Unio Odoo log since the last run
    since = int(state.get('log_since', now - 600))
    events = []
    logs = ''
    if shutil.which('docker'):
        try:
            r = subprocess.run(['docker', 'logs', CONTAINER, '--since', str(since)], capture_output=True, text=True, timeout=60)
            logs = r.stdout + '\n' + r.stderr  # Odoo logs to stderr
        except (OSError, subprocess.TimeoutExpired):
            logs = ''
    for line in logs.splitlines():
        m = re.match(r'^\S+ \S+ \d+ (ERROR|CRITICAL) (\S+) (.*)$', line)
        if m:
            db = m.group(2) if m.group(2) != '?' else ''
            events.append({'type': 'event', 'ts': now, 'kind': 'error', 'database': db, 'message': m.group(3)[:2000],
                           'key': 'err:%s:%s' % (db, re.sub(r'\d+', '#', m.group(3))[:150])})
    state['log_since'] = int(now)
    # backups: once an hour
    if now - state.get('backup_at', 0) > 3600 and os.path.isdir(BACKUPS):
        files = sorted((os.path.join(BACKUPS, f) for f in os.listdir(BACKUPS)), key=os.path.getmtime, reverse=True)
        latest = {}
        for f in files:
            db = os.path.basename(f).split('-')[0]
            latest.setdefault(db, f)
        lines = ['%s: %s, %.1f MB, %.0f h ago' % (db, os.path.basename(f), os.path.getsize(f) / 1e6, (now - os.path.getmtime(f)) / 3600)
                 for db, f in sorted(latest.items())]
        old = [db for db, f in latest.items() if now - os.path.getmtime(f) > 30 * 3600]
        msg = ('NOT RECENT: %s\n' % ', '.join(old) if old else 'All backed up in the last 30 hours.\n') + '\n'.join(lines)
        events.append({'type': 'event', 'ts': now, 'kind': 'backup', 'message': msg, 'key': 'backup:%d' % (now // 3600)})
        if old:
            events.append({'type': 'event', 'ts': now, 'kind': 'error', 'message': 'Backups older than 30 hours: %s' % ', '.join(old),
                           'key': 'backup-old:%s' % ','.join(sorted(old))})
        state['backup_at'] = now
    # certificates: twice a day
    if now - state.get('cert_at', 0) > 12 * 3600 and shutil.which('certbot'):
        out = run(['certbot', 'certificates'])
        certs = re.findall(r'Certificate Name: (\S+).*?Expiry Date: (\S+ \S+)\S* \(([^)]*)\)', out, re.S)
        unio = [(n, d, v) for n, d, v in certs if 'unio' in n]
        soon = [n for n, d, v in unio if re.search(r'VALID: ([0-9]) day', v) or 'INVALID' in v]
        msg = '%d Unio certificates. %s\n' % (len(unio), ('Renew soon: %s' % ', '.join(soon)) if soon else 'None expiring within 10 days.') + \
              '\n'.join('%s: %s' % (n, v) for n, d, v in unio[:40])
        events.append({'type': 'event', 'ts': now, 'kind': 'cert', 'message': msg, 'key': 'cert:%d' % (now // 43200)})
        state['cert_at'] = now
    emit('events.jsonl', events)
    os.makedirs(OUT, exist_ok=True)
    json.dump(state, open(STATE, 'w'))
    # Odoo (uid 100 in its container) moves the files it has read
    for f in os.listdir(OUT):
        try:
            shutil.chown(os.path.join(OUT, f), 100, 101)
        except (OSError, LookupError):
            pass
    try:
        shutil.chown(OUT, 100, 101)
    except (OSError, LookupError):
        pass


if __name__ == '__main__':
    sys.exit(main())
