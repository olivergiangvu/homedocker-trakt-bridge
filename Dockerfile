FROM node:26-alpine@sha256:0b36e8c136b94cd4fcf02188228e76c31ad5872eef3fec8cbd2eee500cfd9e80 AS node-runtime

# Keep build-only binutils out of the final image while removing symbols that
# are unnecessary for this production runtime. CI smoke-tests Node, node:sqlite,
# health/readiness and the full application test suite before this is accepted.
FROM alpine:3.24.2@sha256:d56c381f961d307a21b3ca004cf1e3910f106644aefb1f43e654c8a56c4fd395 AS node-slim
RUN apk add --no-cache binutils
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
RUN strip --strip-unneeded /usr/local/bin/node

FROM alpine:3.24.2@sha256:d56c381f961d307a21b3ca004cf1e3910f106644aefb1f43e654c8a56c4fd395
RUN apk add --no-cache ca-certificates libstdc++ \
  && addgroup -g 1000 -S node \
  && adduser -u 1000 -S -G node -H -h /app node \
  && mkdir -p /app/data \
  && chown -R node:node /app
COPY --from=node-slim /usr/local/bin/node /usr/local/bin/node
WORKDIR /app
COPY --chown=node:node package.json ./
COPY --chown=node:node src ./src
USER node
ENV NODE_ENV=production PORT=7000 DATA_DIR=/app/data
EXPOSE 7000
VOLUME ["/app/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:7000/health || exit 1
CMD ["node", "src/index.mjs"]
