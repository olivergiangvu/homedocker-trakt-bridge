FROM node:24-alpine@sha256:ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1 AS node-runtime

# Keep build-only binutils out of the final image while removing symbols that
# are unnecessary for this production runtime. CI smoke-tests Node, node:sqlite,
# health/readiness and the full application test suite before this is accepted.
FROM alpine:3.24.3@sha256:be2656061b536c1883c4034790e21d0a09c5aaaf51968b89368a3c0371ea2c9a AS node-slim
RUN apk add --no-cache binutils
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
RUN strip --strip-unneeded /usr/local/bin/node

FROM alpine:3.24.3@sha256:be2656061b536c1883c4034790e21d0a09c5aaaf51968b89368a3c0371ea2c9a
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
