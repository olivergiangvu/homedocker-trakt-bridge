/**
 * One Trakt Bridge dashboard: integrated Gateway operational panel.
 * Profile-authorized ON/OFF only for read-only view filtering; no watch writes.
 * No media aliases/IDs, token values, profile IDs,
 * listener port, upstream URL or streaming paths are surfaced in this HTML.
 */
import { escapeHtml } from './html.mjs';

export function renderUnifiedGatewayPanel(status, { diagnosticsUrl = null, controlUrl = null, compact = false } = {}) {
  const s = value => escapeHtml(value ?? '—');
  const metric = key => s(status.telemetry?.[key] ?? '—');
  const listening = status.listening === true;
  const enabled = status.enabled === true;
  const filtering = status.filteringEnabled === true;
  const available = status.filterControlAvailable === true;
  const badge = !enabled ? 'muted' : listening ? 'warn' : 'bad';
  const readiness = !enabled ? 'Configured OFF' : listening
    ? 'Listener only · client route unverified' : 'Review configuration';
  const mode = status.nextUpMode ?? 'exact_unpaged_only';
  const source = ({ aiostreams: 'AIOStreams (primary)',
    remux: 'Remux (Jellyfin-compatible)',
    jellyfin: 'Native Jellyfin' })[status.sourceKind] || 'Jellyfin-compatible source';
  const control = controlUrl ? `<div class="gateway-switch" role="group" aria-label="Gateway identity filtering control">
    <div><strong>Identity filter: <span class="${filtering ? 'ok' : 'muted'}">${filtering ? 'ON' : 'OFF'}</span></strong>
    <p class="hint">AUTO ON for a configured, mapped source unless manually turned OFF. OFF is a persistent per-source kill switch and returns upstream NextUp untouched. Neither state changes nginx/client routes.</p></div>
    <form method="post" action="${escapeHtml(controlUrl)}">
      <button class="btn ${filtering ? 'bad' : ''}" type="submit"
        name="state" value="${filtering ? 'off' : 'on'}"
        ${available ? '' : 'disabled aria-disabled="true"'}
        aria-label="${filtering ? 'Turn Gateway identity filtering OFF' : 'Turn Gateway identity filtering ON'}">${filtering ? 'Turn OFF' : 'Turn ON'}</button>
    </form>
    ${available ? '' : '<span class="hint">Provision the selected Jellyfin upstream, same-user mapping and Gateway listener once before this switch becomes available.</span>'}
  </div>` : '';

  const diag = diagnosticsUrl
    ? `<a class="btn secondary" href="${escapeHtml(diagnosticsUrl)}">Read-only JSON diagnostics</a>`
    : '';
  const cell = (label, value, note = '') => `<div class="stat">
    <span class="label">${s(label)}</span><span class="value">${value}</span>
    ${note ? `<span class="hint">${s(note)}</span>` : ''}
  </div>`;
  const pipeline = `<div class="unified-pipeline" aria-label="Unified history and client flow">
    <span>Trakt <small>history authority</small></span>
    <span aria-hidden="true">↔</span>
    <span>Unified Bridge <small>one writer · one dashboard</small></span>
    <span aria-hidden="true">↔</span>
    <span>Jellyfin API source <small>AIOStreams primary · Remux / native Jellyfin optional</small></span>
    <span aria-hidden="true">→</span>
    <span>Jellyfin clients <small>Odin, Remux UI, other compatible FE</small></span>
  </div>`;
  return `<section class="card unified-gateway" id="aio-identity-gateway" aria-label="Jellyfin Identity Gateway overview">
    <div class="row">
      <div style="margin-right:auto"><span class="manifest-kicker">Unified Bridge · v1.4</span>
        <h2>Jellyfin Identity Gateway</h2></div>
      <span class="badge">v1.4 target</span>
      <span class="badge ${badge}">${s(readiness)}</span>
    </div>
    <p class="muted">Jellyfin API source → optional identity filter → Jellyfin clients. AIOStreams remains the primary source; compatible Remux/native Jellyfin can use the same adapter contract after separate user mapping. Production client routing is not qualified yet.</p>
    ${compact ? '' : pipeline}
    ${control}
    <div class="gateway-subhead">01 · Activation & safety</div>
    <div class="grid">
      ${cell('Gateway transport', s(enabled ? 'Provisioned' : 'Not provisioned'), 'Listening and filtering are independent')}
      ${cell('Identity filter', s(filtering ? 'ON' : 'OFF'), 'UI controlled · persistent')}
      ${cell('Gateway listener', s(listening ? 'Listening' : 'Not listening'), 'Listening is not Odin qualification')}
      ${cell('User identity mapping', s(status.userMappingConfigured ? 'Configured' : 'Not confirmed'), 'Same Jellyfin user required')}
      ${cell('Jellyfin API source', s(source), status.upstreamConfigured ? 'Configured backend' : 'Backend not configured')}
      ${cell('NextUp response policy', s(mode === 'short_first_page_only' ? 'Conservative first page' : 'Unpaged only'), 'Only complete unpaged/short first page; otherwise original source')}
      ${cell('Resume', s(status.resumeMode === 'raw_passthrough' ? 'Raw passthrough' : 'Off'), 'Never normalized or merged')}
    </div>
    <div class="gateway-subhead">02 · Verified identity</div>
    <div class="grid">
      ${cell('Verified shows', s(status.identity.verifiedShows))}
      ${cell('Verified aliases', s(status.identity.verifiedAliases))}
      ${cell('Verified alias groups', s(status.identity.aliasGroups))}
      ${cell('Conflicts quarantined', s(status.identity.quarantinedConflicts))}
    </div>
    <div class="gateway-subhead">03 · Gateway traffic since restart</div>
    <div class="grid">
      ${cell('NextUp requests', metric('nextUpRequests'))}
      ${cell('Normalized responses', metric('nextUpNormalizedResponses'), 'Verified and eligible responses only')}
      ${cell('Removed duplicate cards', metric('nextUpRemovedItems'), 'Never a database deletion')}
      ${cell('Upstream unchanged responses', metric('nextUpUnchangedResponses'))}
      ${cell('Paged requests passed through', metric('nextUpPagedPassThrough'), 'Paging safety policy')}
      ${cell('Unsafe unpaged passed through', metric('nextUpUnpagedUnsafePassThrough'), 'Full Limit page, incomplete count or unknown response')}
      ${cell('Fail-open normalizations', metric('failOpenNormalizationErrors'))}
      ${cell('Upstream transport failures', metric('upstreamTransportFailures'))}
      ${cell('Upstream non-200', metric('upstreamNon200Responses'))}
      ${cell('Response failures', metric('responseFailures'))}
      ${cell('Gateway mean latency', status.telemetry?.nextUpAverageMs == null ? '—' : s(status.telemetry.nextUpAverageMs) + ' ms', 'Completed local responses; not p95')}
      ${cell('Upstream mean latency', status.telemetry?.aioUpstreamAverageMs == null ? '—' : s(status.telemetry.aioUpstreamAverageMs) + ' ms', 'Upstream first response; not p95')}
      ${cell('Metadata active / queued', status.telemetry ? s(status.telemetry.metadataActive ?? 0) + ' / ' + s(status.telemetry.metadataQueued ?? 0) : '—', 'Bounded lookups')}
    </div>
    <div class="gateway-footer">
      <span class="hint">ON/OFF persists in Bridge settings only · No watch-history edits · Upstream remains autonomous · No live routing switch · V140_STABLE_RELEASE_HOLD</span>
      ${diag}
    </div>
  </section>`;
}
