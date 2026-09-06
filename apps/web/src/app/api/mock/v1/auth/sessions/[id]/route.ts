export const dynamic = 'force-dynamic';

export async function DELETE(_request: Request, context: { params: Promise<{ id: string }> }) {
  await context.params;
  return new Response(null, { status: 204 });
}
