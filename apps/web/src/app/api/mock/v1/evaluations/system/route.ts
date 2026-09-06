import type { SystemMetrics } from '@aether/shared-types';
import { getSystemMetrics } from '@/mocks/store';
import { json } from '../../_lib/respond';

export const dynamic = 'force-dynamic';

const WINDOWS = new Set<SystemMetrics['window']>(['1h', '24h', '7d']);

export async function GET(request: Request) {
  const raw = new URL(request.url).searchParams.get('window') ?? '24h';
  const window = (
    WINDOWS.has(raw as SystemMetrics['window']) ? raw : '24h'
  ) as SystemMetrics['window'];
  return json(getSystemMetrics(window));
}
