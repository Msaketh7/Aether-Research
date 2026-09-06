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
