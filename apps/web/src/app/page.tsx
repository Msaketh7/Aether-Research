import { redirect } from 'next/navigation';

/** The product starts at the dashboard; auth is enforced in Phase 2. */
export default function RootPage() {
  redirect('/dashboard');
}
