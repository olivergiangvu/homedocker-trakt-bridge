import { TraktClient } from './trakt.mjs';

export class ManagedTraktClient extends TraktClient {
  constructor(config, db) {
    super(config, db);
    this.db = db;
  }

  async refresh(profileId, force = false) {
    try {
      return await super.refresh(profileId, force);
    } catch (err) {
      if (err?.code === 'reconnect_required') {
        this.db.clearTokens(profileId, 'reconnect_required');
      }
      throw err;
    }
  }
}
