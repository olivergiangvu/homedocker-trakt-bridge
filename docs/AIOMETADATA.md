# AIOMetadata authority model for HomeDocker

This document records the production role split between AIOMetadata, AIOStreams and HomeDocker Trakt Bridge.

## Recommended setting

For the AIOMetadata Jellyfin user representing the HomeDocker owner:

```text
Trackers = This server only
```

This is a **read-source** choice, not a global tracker-disable switch.

## What changes

With `This server only`, AIOMetadata does not read external tracker history back into its Jellyfin Continue Watching / watched / Next Up surface.

Its local `jellyfin_playstate` still records plays reported through the server.

## What does not change

Enabled AIOMetadata Watch Tracking can still write playback events to connected secondary trackers according to each service's tracking switches, media filters and record policy.

Therefore the HomeDocker role split is:

```text
Trakt
  canonical watched/resume history
        ^
        |
Trakt Bridge
        ^
        |
AIOStreams
  Jellyfin state surface

AIOMetadata
  metadata/catalog
  local playstate
  secondary-tracker write/fan-out
        |
        +--> Simkl
        +--> MDBList
        +--> other enabled trackers
```

## Why this matters

Allowing AIOMetadata to read several secondary trackers and feed that history into a Jellyfin surface while AIOStreams also imports authoritative Trakt state creates competing history sources.

The production dual-IMDb incident demonstrated why this is unsafe: two valid provider spellings for the same show can survive as distinct Jellyfin identities when different history lanes choose different spellings.

`This server only` removes that secondary read lane without removing AIOMetadata's metadata or tracker-write value.

## Watchlist is separate

AIOMetadata's Watchlist picker is independent from the Trackers/history picker. It may remain configured to another source when desired, provided HomeDocker does not treat that watchlist source as an authoritative watched/resume history feed.

## Operational rule

If HomeDocker Trakt Bridge is the selected canonical history bridge:

```text
AIOMetadata tracker readback  = This server only
Trakt Bridge pull authority   = Trakt
AIOStreams client surface     = enabled
AIOMetadata tracker writes    = optional / enabled as desired
```

Changing AIOMetadata back to Automatic or to a specific external history tracker should be treated as an architecture change and re-tested for duplicate/resume convergence before production use.
