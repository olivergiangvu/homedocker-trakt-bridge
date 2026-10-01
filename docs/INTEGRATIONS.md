# Integrations

This guide explains how HomeDocker Trakt Bridge coexists with AIOStreams, AIOMetadata and Jellyfin-compatible clients.

## Canonical authority model

HomeDocker uses one watched/resume read authority:

```text
Trakt
  canonical watched/resume history
        ^
        |
Trakt Bridge
        ^
        |
AIOStreams
  Jellyfin-compatible state surface
```

AIOMetadata is not used as a second tracker-history read authority.

## AIOMetadata

Recommended setting for the HomeDocker owner Jellyfin user:

```text
Trackers = This server only
```

This setting controls **tracker readback** into AIOMetadata's Jellyfin surface.

With `This server only`:

- Continue Watching is based on local AIOMetadata Jellyfin playstate
- watched ticks / Next Up do not read external tracker history back into that surface
- external tracker history cannot become a second competing source beside Trakt Bridge

It does **not** globally disable tracker writes.

If Watch Tracking is enabled, AIOMetadata can still fan playback writes out to secondary trackers such as Simkl or MDBList.

Recommended role split:

```text
AIOMetadata read role  = local server state only
AIOMetadata write role = optional secondary-tracker fan-out
Trakt Bridge read role = canonical external history -> AIOStreams
Trakt Bridge write role= AIOStreams playback/history -> Trakt
```

## AIOStreams

AIOStreams is the Jellyfin-compatible state/playback surface used by clients.

The bridge manifest advertises `watch_state` v2 push + pull endpoints.

Use the highlighted **Manifest URL** on the profile dashboard to connect the bridge to AIOStreams.

Treat the URL as a credential.

## Native Trakt clients

Some clients can use both:

- native Trakt integration
- AIOStreams / Jellyfin state

For HomeDocker, keep:

```env
PULL_IDENTITY_MODE=trakt
```

This preserves Trakt's IMDb spelling on Bridge pull and avoids the bridge creating a parallel identity namespace for clients that also read Trakt directly.

## Strand

Strand can run with native Trakt enabled while AIOStreams remains enabled.

The production requirement is that both paths converge on the same Trakt-side identity representation.

The historical dual-IMDb regression that motivated this rule is documented in [DEVELOPMENT.md](DEVELOPMENT.md), not exposed in the operator UI.

## Odin / Jellyfin-compatible clients without native Trakt

These clients can rely on AIOStreams state supplied by Trakt Bridge.

The bridge remains useful even when a client has no native Trakt integration because Trakt state is projected through AIOStreams.

## Remux / Trellis

Treat Remux/Trellis as Jellyfin-compatible client/service layers around the AIOStreams state surface.

They should not introduce a separate canonical tracker-history authority.

## Watchlist

Watchlist is logically separate from watched/resume authority.

The bridge supports movie/show Trakt watchlist push and pull. Other applications may also expose watchlist/catalog features, but avoid configuring multiple systems to write conflicting watched/resume state into AIOStreams.

## Practical rule

For HomeDocker, keep this simple:

```text
Trakt      owns long-term watched/resume truth
AIOStreams exposes that truth to Jellyfin-compatible clients
AIOMetadata enriches metadata and may fan writes out to secondary trackers
```
