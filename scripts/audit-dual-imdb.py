#!/usr/bin/env python3
"""Read-only dual-IMDb resume audit for the HomeDocker AIO/Trakt integration."""
import argparse
import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

AIO_DB = '/var/lib/docker/volumes/aiostreams_data/_data/db.sqlite'
BRIDGE_DB = '/var/lib/docker/volumes/homedocker-trakt-bridge_trakt_bridge_data/_data/bridge.db'
IMDB = re.compile(r'^tt\d+$')
EPISODE = re.compile(r'^e\|(tt\d+):(\d+):(\d+)$')
TZ = timezone(timedelta(hours=7))


def read_only(path):
    if not Path(path).is_file():
        raise SystemExit(f'STOP: SQLite database missing: {path}')
    db = sqlite3.connect('file:' + path + '?mode=ro', uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def local_time(ms):
    if not ms:
        return '-'
    try:
        return datetime.fromtimestamp(int(ms) / 1000, TZ).strftime('%d/%m %H:%M')
    except (ValueError, OverflowError, TypeError):
        return '-'


def valid_imdb(value):
    value = str(value or '').lower().strip()
    return value if IMDB.fullmatch(value) else None


def aliases_from_bridge(bridge, profile_id):
    row = bridge.execute(
        "SELECT payload_json FROM media_cache WHERE cache_key=? AND expires_at > CAST(strftime('%s','now') AS INT)",
        ('identity-alias:v1:' + profile_id,)
    ).fetchone()
    try:
        raw = json.loads(row['payload_json']) if row else {}
    except (ValueError, TypeError):
        raw = {}
    pairs = {}
    for show_id, value in (raw.get('shows') or {}).items():
        if not isinstance(value, dict) or not str(show_id).isdigit():
            continue
        preferred = valid_imdb(value.get('preferredMetaId'))
        canonical = valid_imdb(value.get('traktImdb'))
        if preferred and canonical and preferred != canonical:
            pairs[str(show_id)] = (preferred, canonical, 'learned-bridge')
    return pairs


def audit(aio, bridge, sink_name, sink_instance, include_fixture=True):
    profiles = bridge.execute(
        'SELECT id FROM profiles WHERE access_token_enc IS NOT NULL'
    ).fetchall()
    if len(profiles) != 1:
        raise SystemExit('STOP: expected exactly one connected Bridge profile')
    profile = profiles[0]['id']
    sinks = aio.execute(
        """SELECT uuid, persona FROM watch_sinks
           WHERE addon_name=? AND addon_instance_id=? AND retired_at IS NULL""",
        (sink_name, sink_instance)
    ).fetchall()
    if len(sinks) != 1:
        raise SystemExit('STOP: expected exactly one active AIO sink (name + instance)')
    sink = sinks[0]
    pairs = aliases_from_bridge(bridge, profile)
    # Historical *documented* regression fixture, not an inferred match.
    if include_fixture and '263102' not in pairs:
        pairs['263102'] = ('tt44094505', 'tt44051354', 'documented-fixture')
    rows = aio.execute(
        """SELECT item_key, played, position_ms, origin, updated_at
           FROM watch_state
           WHERE uuid=? AND persona=? AND item_key LIKE 'e|tt%'
           ORDER BY updated_at DESC""",
        (sink['uuid'], sink['persona'])
    ).fetchall()
    by_imdb = defaultdict(list)
    for row in rows:
        match = EPISODE.fullmatch(str(row['item_key']))
        if not match:
            continue
        imdb, season, episode = match.groups()
        by_imdb[imdb].append({
            'episode': f'S{int(season):02d}E{int(episode):02d}',
            'played': int(row['played'] or 0),
            'positionMs': int(row['position_ms'] or 0),
            'origin': row['origin'],
            'updatedAt': local_time(row['updated_at']),
        })
    scope_hash = hashlib.sha256(
        (sink['uuid'] + '\x1f' + (sink['persona'] or '')).encode()
    ).hexdigest()[:12]
    print('HOMEDOCKER_RC7_IDENTITY_AUDIT')
    print('MODE=READ_ONLY')
    print('AIO_SINK=CONNECTED_EXACT')
    print('SCOPE_HASH=' + scope_hash)
    print('ALIAS_PAIR_COUNT=' + str(len(pairs)))
    print('ANALYZED_EPISODE_ROWS=' + str(sum(map(len, by_imdb.values()))))
    collisions = 0
    for trakt_id, (a, b, source) in sorted(pairs.items(), key=lambda item: int(item[0])):
        first = [r for r in by_imdb[a] if r['played'] == 0 and r['positionMs'] > 0]
        second = [r for r in by_imdb[b] if r['played'] == 0 and r['positionMs'] > 0]
        overlap = sorted(set(r['episode'] for r in first) & set(r['episode'] for r in second))
        candidate = bool(first and second)
        collisions += int(candidate)
        if not (by_imdb[a] or by_imdb[b] or source == 'documented-fixture'):
            continue
        print(f'\nTRAKT_SHOW_ID={trakt_id} SOURCE={source}')
        print(f'ALIASES={a},{b}')
        print(f'RESUME_COUNTS={a}:{len(first)},{b}:{len(second)}')
        print('EXACT_EPISODE_OVERLAP=' + (','.join(overlap) if overlap else 'NONE'))
        print('DUAL_IDENTITY_RESUME_CANDIDATE=' + str(candidate).lower())
        for alias, items in ((a, first), (b, second)):
            for item in items[:10]:
                print(
                    f"ROW ID={alias} EP={item['episode']} ORIGIN={item['origin']} "
                    f"POS_MS={item['positionMs']} UPDATED={item['updatedAt']}"
                )
    print('\nDUAL_IDENTITY_RESUME_CANDIDATES=' + str(collisions))
    print('RESULT=READ_ONLY_COMPLETE')
    print('NOTE=Zero candidates does not rule out duplicates in remote clients or their native Trakt caches.')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--aio-db', default=AIO_DB)
    ap.add_argument('--bridge-db', default=BRIDGE_DB)
    ap.add_argument('--sink-name', default='homedocker-trakt-bridge')
    ap.add_argument('--sink-instance', default='e3fe3b0')
    ap.add_argument('--no-known-fixture', action='store_true')
    args = ap.parse_args()
    with read_only(args.aio_db) as aio, read_only(args.bridge_db) as bridge:
        audit(aio, bridge, args.sink_name, args.sink_instance, not args.no_known_fixture)


if __name__ == '__main__':
    main()
