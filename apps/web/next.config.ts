import path from 'node:path';
import type { NextConfig } from 'next';

/**
 * Security headers are set here rather than in a proxy so that they apply in
 * local development too - a header that only exists in production is a header
 * nobody tests. The API's own headers are set independently in Phase 20.
 */
const securityHeaders = [
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'X-Frame-Options', value: 'DENY' },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
];

const nextConfig: NextConfig = {
  reactStrictMode: true,
  /**
   * Standalone output is what the container image copies: a self-contained
   * server plus only the node_modules it traced, rather than the whole
   * workspace install (Phase 23).
   *
   * It is opt-in by variable rather than always on, because Next refuses
   * `next start` when it is set - and `next start` is how `npm run start` and
   * Playwright's webServer run the built app. One flag, set by
   * infra/docker/web.Dockerfile, keeps both paths working.
   */
  output: process.env.NEXT_BUILD_STANDALONE === '1' ? 'standalone' : undefined,
  /**
   * The standalone tracer starts from this directory by default, which in a
   * workspace misses the hoisted node_modules at the repository root. Pointing
   * it at the monorepo root is what makes @aether/shared-types and the hoisted
   * dependencies land in the traced output.
   */
  outputFileTracingRoot: path.join(import.meta.dirname, '../..'),
  // The shared contract is consumed as TypeScript source; there is no build
  // step for it, so a contract change is a compile error here immediately.
  transpilePackages: ['@aether/shared-types'],
  // typedRoutes is deliberately off: it types Link hrefs against generated
  // route literals, which fights template-literal hrefs like `/research/${id}`
  // for no safety we do not already get from the route constants.
  typedRoutes: false,
  async headers() {
    return [{ source: '/:path*', headers: securityHeaders }];
  },
};

export default nextConfig;
