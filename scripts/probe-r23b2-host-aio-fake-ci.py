#!/usr/bin/env python3
"""Disposable local-only AIO/Jellyfin mock for the R23B2 Docker netns lab."""
import http.server
TOKEN="R23B2-FAKE-CI-TOKEN-ONLY"
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args):
        return
    def do_GET(self):
        if self.headers.get("X-Emby-Token") != TOKEN:
            self.send_response(401)
            self.end_headers()
            return
        if self.path.startswith("/jellyfin/Shows/NextUp"):
            payload=b'{"Items":[{"Id":"ep-ci","UserData":{"PlaybackPositionTicks":42}}],"TotalRecordCount":1}'
        elif self.path=="/jellyfin/Users/Me":
            payload=b'{"Id":"r23b2-fakeuser-ci"}'
        elif self.path=="/jellyfin/Items/series-ci":
            payload=b'{"Id":"series-ci","ProviderIds":{"Imdb":"tt1234567"}}'
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
    def do_POST(self):
        self.send_response(405)
        self.end_headers()
if __name__=="__main__":
    http.server.ThreadingHTTPServer(("127.0.0.1",3240),Handler).serve_forever()
