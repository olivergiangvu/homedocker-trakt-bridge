export class BridgeError extends Error {
  constructor(message, {
    status = 500,
    retryAfter = null,
    code = 'bridge_error',
    cause = null,
    upstreamPath = null,
    upstreamStatus = null,
    upstreamDetail = null,
    rateLimit = null,
  } = {}) {
    super(message, { cause });
    this.name = 'BridgeError';
    this.status = status;
    this.retryAfter = retryAfter;
    this.code = code;
    this.upstreamPath = upstreamPath;
    this.upstreamStatus = upstreamStatus;
    this.upstreamDetail = upstreamDetail;
    this.rateLimit = rateLimit;
  }
}
