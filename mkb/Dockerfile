# MKB website: Node server (no dependencies) + static site.
#   docker build -t mkb .
#   docker run -p 8080:8080 -e MKB_ADMIN_PASSWORD='a-long-password' -v mkb-data:/data mkb
FROM node:22-alpine
WORKDIR /app
COPY package.json ./
COPY server ./server
COPY public ./public
RUN mkdir -p /data && chown node:node /data
ENV NODE_ENV=production PORT=8080 MKB_DATA_DIR=/data
VOLUME ["/data"]
USER node
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD wget -qO- http://127.0.0.1:8080/api/health || exit 1
CMD ["node", "server/server.js"]
