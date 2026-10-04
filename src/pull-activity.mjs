function stamp(value) {
  return typeof value === 'string' && value ? value : null;
}

function same(a, b) {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}

export function activitySnapshot(activities = {}, identityVersion = '') {
  return {
    all: stamp(activities?.all),
    identityVersion: String(identityVersion || ''),
    playback: {
      moviesPaused: stamp(activities?.movies?.paused_at),
      episodesPaused: stamp(activities?.episodes?.paused_at),
    },
    state: {
      moviesWatched: stamp(activities?.movies?.watched_at),
      episodesWatched: stamp(activities?.episodes?.watched_at),
      moviesWatchlisted: stamp(activities?.movies?.watchlisted_at),
      showsWatchlisted: stamp(activities?.shows?.watchlisted_at),
      watchlistUpdated: stamp(activities?.watchlist?.updated_at),
    },
  };
}

/**
 * Decide which authoritative Trakt groups must be refreshed.
 *
 * The global all timestamp is the conservative escape hatch. If Trakt says
 * something changed but none of the watch-state timestamps we understand
 * moved, refresh both playback and watched/watchlist rather than risk serving
 * a stale removal or another external/native writer transition.
 */
export function activityDecision(previous, current) {
  if (!previous) {
    return {
      first: true,
      allChanged: true,
      identityChanged: true,
      playbackChanged: true,
      stateChanged: true,
      unknownChanged: false,
      fetchPlayback: true,
      fetchState: true,
    };
  }

  const allChanged = previous.all !== current.all;
  const identityChanged =
    String(previous.identityVersion || '') !== String(current.identityVersion || '');
  const playbackChanged =
    identityChanged || !same(previous.playback, current.playback);
  const stateChanged = !same(previous.state, current.state);
  const unknownChanged =
    allChanged && !playbackChanged && !stateChanged;

  return {
    first: false,
    allChanged,
    identityChanged,
    playbackChanged,
    stateChanged,
    unknownChanged,
    // A watched/unwatched transition may also add/remove an item from Trakt
    // playback, so state changes conservatively refresh playback too.
    fetchPlayback:
      playbackChanged || stateChanged || unknownChanged,
    fetchState:
      stateChanged || unknownChanged,
  };
}

export function reusablePlaybackItems(cache, current, decision) {
  if (
    !cache
    || decision.fetchPlayback
    || !Array.isArray(cache.items)
    || String(cache.identityVersion || '') !== String(current.identityVersion || '')
  ) {
    return null;
  }
  return structuredClone(cache.items);
}
