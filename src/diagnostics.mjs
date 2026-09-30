export function formatEventTime(epochSeconds, timeZone = 'Asia/Ho_Chi_Minh') {
  const value = Number(epochSeconds);
  if (!Number.isFinite(value)) return '';
  return new Intl.DateTimeFormat('en-GB', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(value * 1000));
}

export function shortenEventId(eventId, head = 24, tail = 8) {
  const value = String(eventId || '');
  if (value.length <= head + tail + 1) return value;
  return `${value.slice(0, head)}…${value.slice(-tail)}`;
}

export function summarizeRecentEvents(rows, limit = 12) {
  const groups = new Map();

  for (const row of rows || []) {
    const key = row.event_id || `row:${row.created_at}:${row.event || ''}`;
    let group = groups.get(key);
    if (!group) {
      group = {
        ...row,
        attempts: 0,
        hadError: false,
      };
      groups.set(key, group);
    }
    group.attempts += 1;
    if (row.status === 'error') group.hadError = true;
  }

  return [...groups.values()].slice(0, limit).map((group) => {
    let displayStatus = group.status;
    if (group.status === 'ok' && group.hadError) displayStatus = 'recovered';
    else if (group.status === 'error') displayStatus = 'retrying';

    return {
      ...group,
      displayStatus,
      shortEventId: shortenEventId(group.event_id),
    };
  });
}
