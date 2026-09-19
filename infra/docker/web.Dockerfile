# The Next.js frontend.
#
# Build context is the repository root, because @aether/shared-types is a
# workspace package consumed as TypeScript source (next.config.ts transpiles
# it) rather than something npm could fetch.
#
#   docker build -f infra/docker/web.Dockerfile -t aether-web .

# --- dependencies ------------------------------------------------------------
# Only the manifests, so this layer survives every change that is not a
# dependency change. `npm ci` and not `npm install`: the lockfile decides, and
# the build fails rather than drifting if the two disagree.
FROM node:22-alpine AS deps
WORKDIR /srv/aether

COPY package.json package-lock.json ./
COPY apps/web/package.json ./apps/web/
COPY packages/shared-types/package.json ./packages/shared-types/

RUN --mount=type=cache,target=/root/.npm npm ci

# --- build -------------------------------------------------------------------
FROM node:22-alpine AS builder
WORKDIR /srv/aether

# The whole installed tree, not just node_modules/: npm workspaces links
# @aether/shared-types into node_modules as a symlink and may or may not create
# a per-workspace node_modules depending on what hoists, and a COPY naming a
# directory that npm decided not to create fails the build. Source then lands
# on top - .dockerignore keeps node_modules out of the context, so nothing
# installed is overwritten.
COPY --from=deps /srv/aether ./
COPY packages/shared-types ./packages/shared-types
COPY apps/web ./apps/web

# NEXT_PUBLIC_* is inlined into the client bundle at build time, so these are
# build arguments and not runtime environment: setting NEXT_PUBLIC_API_BASE_URL
# on a running container changes nothing. The defaults are what makes one image
# serve every environment - a *relative* base URL, resolved by the browser
# against whatever origin served the page. The deployed topology puts the API
# behind the same load balancer under /api (infra/terraform/modules/alb), which
# also means the session cookie is first-party and CORS never enters into it.
ARG NEXT_PUBLIC_API_MODE=live
ARG NEXT_PUBLIC_API_BASE_URL=/api/v1
ARG NEXT_PUBLIC_APP_NAME="Aether Research"

ENV NEXT_PUBLIC_API_MODE=$NEXT_PUBLIC_API_MODE \
    NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL \
    NEXT_PUBLIC_APP_NAME=$NEXT_PUBLIC_APP_NAME \
    NEXT_TELEMETRY_DISABLED=1 \
    NEXT_BUILD_STANDALONE=1

RUN npm run build --workspace @aether/web

# --- runtime -----------------------------------------------------------------
FROM node:22-alpine AS runtime

# The Next standalone server writes nothing and needs no package manager, so
# the runtime has neither npm's cache nor the workspace install: 32 MB of
# traced dependencies instead of a 1.2 GB node_modules.
RUN apk add --no-cache tini \
    && addgroup -g 10001 aether \
    && adduser -u 10001 -G aether -D -h /home/aether aether

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

WORKDIR /srv/aether

# The standalone tree is rooted at the *workspace* root because
# outputFileTracingRoot points there, so the server entry is nested under
# apps/web and node_modules sits beside it. Copying the tree whole preserves
# that relationship; flattening it breaks module resolution at startup.
COPY --from=builder --chown=10001:10001 /srv/aether/apps/web/.next/standalone ./
COPY --from=builder --chown=10001:10001 /srv/aether/apps/web/.next/static ./apps/web/.next/static

# No public/ directory exists in this app, so none is copied. A COPY of a
# missing path fails the build, which is the correct behaviour to leave in
# place: if one is added, this file must be updated to ship it.

USER 10001:10001

EXPOSE 3000

# Next serves a 200 from any route once it is listening; `/login` is the one
# page that renders without a session and without calling the API, so it tests
# the server rather than the backend.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:'+(process.env.PORT||3000)+'/login').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]

ENTRYPOINT ["/sbin/tini", "--"]
CMD ["node", "apps/web/server.js"]
