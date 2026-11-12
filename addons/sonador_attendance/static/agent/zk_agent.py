#!/usr/bin/env python3
"""Attendance sync agent, for ZKTeco devices that cannot push to the server themselves.

Run it on any PC on the same network as the devices (Windows Task Scheduler or cron, e.g. every 10 minutes):

    pip install pyzk requests
    python zk_agent.py --config zk_agent.json

zk_agent.json:
    {
      "server": "https://erp.example.com",
      "devices": [
        {"ip": "192.168.1.201", "port": 4370, "token": "<token from Attendance > Setup > Devices>"}
      ]
    }

The agent reads users and punches from each device and posts them; the server ignores punches it already has, so a full
re-send is harmless. Nothing is deleted from the device.
"""
import argparse
import json
import logging
import sys

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('zk_agent')


def read_device(ip, port=4370, timeout=30, password=0):
    from zk import ZK
    conn = ZK(ip, port=port, timeout=timeout, password=password, force_udp=False, ommit_ping=True).connect()
    try:
        users = []
        for u in conn.get_users() or []:
            name = u.name.decode('utf-8', 'replace') if isinstance(u.name, (bytes, bytearray)) else (u.name or '')
            users.append({'bio': str(u.user_id or u.uid), 'name': name.strip()})
        punches = [{'bio': str(a.user_id), 'time': a.timestamp.strftime('%Y-%m-%d %H:%M:%S'), 'status': str(a.punch)}
                   for a in conn.get_attendance() or [] if a.timestamp]
        return users, punches
    finally:
        conn.disconnect()


def main():
    import requests
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', required=True)
    cfg = json.load(open(ap.parse_args().config))
    url = (cfg.get('server') or cfg.get('sonador')).rstrip('/') + '/attendance/push'
    failed = 0
    for d in cfg['devices']:
        try:
            users, punches = read_device(d['ip'], int(d.get('port', 4370)), password=int(d.get('password', 0)))
            for i in range(0, max(len(punches), 1), 2000):
                r = requests.post(url, json={'token': d['token'], 'users': users if i == 0 else [], 'punches': punches[i:i + 2000]},
                                  timeout=120)
                r.raise_for_status()
                log.info('%s: %s', d['ip'], r.json())
        except Exception as e:
            failed += 1
            log.error('%s: %s', d['ip'], e)
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
