/**
 * One Trakt Bridge dashboard: integrated Gateway operational panel.
 * Read-only presentation. No media aliases/IDs, token values, profile IDs,
 * listener port, upstream URL or streaming paths are surfaced in this HTML.
 */
import { escapeHtml } from './html.mjs';

export function renderUnifiedGatewayPanel(status, { diagnosticsUrl = null, compact = false } = {}) {
  const s = value => escapeHtml(value ?? '—');
  const metric = key => s(status.telemetry?.[key] ?? '—');
  const listening = status.listening === true;
  const enabled = status.enabled === true;
  const badge = !enabled ? 'muted' : listening ? 'warn' : 'bad';
  const readiness = !enabled ? 'Configured OFF' : listening
    ? 'Listener only · client route unverified' : 'Review configuration';
  const mode = status.nextUpMode ?? 'exact_unpaged_only';
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
    <span>AIOStreams <small>Jellyfin API source</small></span>
    <span aria-hidden="true">→</span>
    <span>Odin / clients <small>routing not yet qualified</small></span>
  </div>`;
  return `<section class="card unified-gateway" id="aio-identity-gateway" aria-label="AIO Identity Gateway overview">
    <div class="row">
      <div style="margin-right:auto"><span class="manifest-kicker">Unified Bridge · v1.4</span>
        <h2>AIO Identity Gateway</h2></div>
      <span class="badge">v1.4 target</span>
      <span class="badge ${badge}">${s(readiness)}</span>
    </div>
    <p class="muted">Read-only control center for one integrated product: verified identity continuity, NextUp policy, safety and runtime telemetry. Production client routing is not qualified.</p>
    ${compact ? '' : pipeline}
    <div class="gateway-subhead">01 · Activation & safety</div>
    <div class="grid">
      ${cell('Gateway module', s(enabled ? 'Enabled (opt-in)' : 'Installed · OFF'), 'Same Bridge service and dashboard')}
      ${cell('Gateway listener', s(listening ? 'Listening' : 'Not listening'), 'Listening is not Odin qualification')}
      ${cell('User identity mapping', s(status.userMappingConfigured ? 'Configured' : 'Not confirmed'), 'Same Jellyfin user required')}
      ${cell('Upstream', s(status.upstreamConfigured ? 'Configured' : 'Not configured'), 'AIOStreams remains the data source')}
      ${cell('NextUp response policy', s(mode === 'short_first_page_only' ? 'Conservative first page' : 'Unpaged only'), 'Other pagination is AIO passthrough')}
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
      ${cell('AIO unchanged responses', metric('nextUpUnchangedResponses'))}
      ${cell('Paged requests passed through', metric('nextUpPagedPassThrough'), 'Paging safety policy')}
      ${cell('Fail-open normalizations', metric('failOpenNormalizationErrors'))}
      ${cell('Upstream transport failures', metric('upstreamTransportFailures'))}
      ${cell('Upstream non-200', metric('upstreamNon200Responses'))}
    </div>
    <div class="gateway-footer">
      <span class="hint">View-only · No watch-history edits · AIO remains autonomous · No live routing switch · V140_STABLE_RELEASE_HOLD</span>
      ${diag}
    </div>
  </section>`;
}
