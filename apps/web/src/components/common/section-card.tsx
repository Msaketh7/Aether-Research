import type { ReactNode } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { cn } from '@/lib/utils';

export function SectionCard({
  title,
  action,
  children,
  className,
  contentClassName,
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  contentClassName?: string;
}) {
  return (
    <Card className={cn('overflow-hidden', className)}>
      <CardHeader className="flex-row items-center justify-between gap-2 border-b border-border/60 bg-muted/25 py-3">
        <CardTitle>{title}</CardTitle>
        {action}
      </CardHeader>
      <CardContent className={cn('pt-4', contentClassName)}>{children}</CardContent>
    </Card>
  );
}
