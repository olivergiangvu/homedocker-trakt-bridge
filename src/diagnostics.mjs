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

/**
 * Group delivery retries for push events, but keep pull polls as separate rows.
 *
 * Push event ids are idempotency keys and remain identical across retries, so
 * grouping them describes one delivery accurately. Pull ids instead describe
 * the cursor (for example `pull|<since>`), and many independent successful
 * polls can legitimately reuse the same cursor. Grouping those polls made the
 * UI incorrectly show "2 attempts", "4 attempts", and so on.
 */
export function summarizeRecentEvents(rows, limit = 12) {
  const groups = new Map();
  let rowNumber = 0;

  for (const row of rows || []) {
    const currentRow = rowNumber++;
    const isPull = row.event === 'pull';
    const key = isPull
      ? `pull-row:${currentRow}:${row.created_at}:${row.event_id || ''}`
      : (row.event_id || `row:${currentRow}:${row.created_at}:${row.event || ''}`);

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
