# FetchIQ UI. Built by compose.yaml at the repository root; no deploy target is implied.
FROM node:24-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:24-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Both values are read at build time: the /api rewrite target is written into the
# route manifest, and NEXT_PUBLIC_* values are inlined into the client bundle.
ARG API_PROXY_TARGET=http://api:8000
ARG NEXT_PUBLIC_API_TOKEN=
ENV API_PROXY_TARGET=$API_PROXY_TARGET \
    NEXT_PUBLIC_API_TOKEN=$NEXT_PUBLIC_API_TOKEN \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:24-alpine AS run
WORKDIR /app
ENV NODE_ENV=production \
    HOSTNAME=0.0.0.0 \
    PORT=3000 \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=build /app/public ./public
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
