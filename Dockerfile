FROM node:24-alpine@sha256:ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1
WORKDIR /app
COPY package.json ./
COPY src ./src
RUN mkdir -p /app/data && chown -R node:node /app
USER node
ENV NODE_ENV=production PORT=7000 DATA_DIR=/app/data
EXPOSE 7000
VOLUME ["/app/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:7000/health').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"
CMD ["node", "src/index.mjs"]
