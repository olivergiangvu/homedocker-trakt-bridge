import test from 'node:test';
import assert from 'node:assert/strict';
import { formatEventTime, shortenEventId, summarizeRecentEvents } from '../src/diagnostics.mjs';

test('formats event time in configured timezone', () => {
  const epoch = Date.parse('2026-09-30T04:00:00Z') / 1000;
  assert.equal(formatEventTime(epoch, 'Asia/Ho_Chi_Minh'), '30/09/2026, 11:00:00');
});

test('shortens long event IDs', () => {
  const value = 'e|tt7660850:2:6|stop|1115434';
  assert.match(shortenEventId(value), /^e\|tt7660850:2:6\|stop\|.*5434$/);
});

test('groups retry followed by success as recovered', () => {
  const rows = [
    { event_id: 'x', event: 'pause', status: 'ok', detail: 'done', created_at: 20 },
    { event_id: 'x', event: 'pause', status: 'error', detail: '429', created_at: 10 },
  ];
  const [event] = summarizeRecentEvents(rows);
  assert.equal(event.displayStatus, 'recovered');
  assert.equal(event.attempts, 2);
  assert.equal(event.detail, 'done');
});

test('latest unresolved error displays retrying', () => {
  const [event] = summarizeRecentEvents([
    { event_id: 'x', event: 'pause', status: 'error', detail: '429', created_at: 10 },
  ]);
  assert.equal(event.displayStatus, 'retrying');
});
