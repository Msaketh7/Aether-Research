'use client';

import { Eye, EyeOff, LockKeyhole, TriangleAlert, type LucideIcon } from 'lucide-react';
import { useState, type InputHTMLAttributes, type KeyboardEvent, type ReactNode } from 'react';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';

export interface AuthFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'id'> {
  id: string;
  label: string;
  icon: LucideIcon;
  /** The server's refusal for this field, from the error envelope's `details`. */
  error?: string[];
  /** Standing guidance. Replaced by `error` while there is one. */
  help?: ReactNode;
  /** A control inside the field's right edge, e.g. the show-password toggle. */
  trailing?: ReactNode;
  /** A second line under the message that is not an error, e.g. Caps Lock. */
  notice?: ReactNode;
}

/**
 * A labelled input with a leading icon and a light line that draws itself
 * across the field's foot on focus.
 *
 * The label stays above the input rather than floating inside it: a floating
 * label shrinks to 11px exactly when someone is checking what they typed, and
 * a placeholder that doubles as a label disappears on the first keystroke.
 *
 * One message slot, error first. The refusal arrives where the person is
 * looking and is wired to the input with `aria-describedby`, so a screen
 * reader hears it on returning to the field.
 */
export function AuthField({
  id,
  label,
  icon: Icon,
  error,
  help,
  trailing,
  notice,
  className,
  ...input
}: AuthFieldProps) {
  const message = error?.length ? error.join(' ') : help;
  const messageId = message ? `${id}-message` : undefined;
  const noticeId = notice ? `${id}-notice` : undefined;
  const describedBy = [messageId, noticeId].filter(Boolean).join(' ') || undefined;

  return (
    <div className="group/field flex flex-col gap-2">
      <Label htmlFor={id} className="text-[0.8125rem] text-foreground/90">
        {label}
      </Label>

      <div className="relative">
        <Icon
          aria-hidden
          className={cn(
            'pointer-events-none absolute top-1/2 left-3.5 size-4 -translate-y-1/2 text-muted-foreground',
            'transition-[color,transform] duration-[var(--duration-base)] ease-[var(--ease-spring)]',
            'group-focus-within/field:scale-110 group-focus-within/field:text-primary-strong',
            error?.length && 'text-destructive-strong',
          )}
        />
        <Input
          id={id}
          aria-invalid={Boolean(error?.length)}
          aria-describedby={describedBy}
          className={cn(
            'auth-input h-11 pl-10 text-[0.9375rem] placeholder:text-muted-foreground/60',
            trailing && 'pr-12',
            className,
          )}
          {...input}
        />
        {trailing ? (
          <div className="absolute inset-y-0 right-1.5 flex items-center">{trailing}</div>
        ) : null}
        <span aria-hidden className="auth-field-line" />
      </div>

      {message ? (
        <div
          id={messageId}
          className={cn(
            'text-xs leading-relaxed',
            error?.length ? 'reveal text-destructive-strong' : 'text-muted-foreground',
          )}
        >
          {message}
        </div>
      ) : null}

      {notice ? (
        <p
          id={noticeId}
          className="reveal flex items-center gap-1.5 text-xs font-medium text-warning-strong"
        >
          <TriangleAlert className="size-3.5" aria-hidden />
          {notice}
        </p>
      ) : null}
    </div>
  );
}

/**
 * A password field with a reveal toggle and a Caps Lock warning.
 *
 * The toggle keeps one accessible name and reports its state through
 * `aria-pressed`: a label that flips between "Show" and "Hide" while also
 * being a toggle announces itself as "Hide password, pressed", which says the
 * opposite of what is true. Caps Lock is read from the key events themselves -
 * the only reliable source - and is the commonest reason a correct password
 * is refused.
 */
export function PasswordField(
  props: Omit<AuthFieldProps, 'icon' | 'type' | 'trailing' | 'notice'>,
) {
  const [visible, setVisible] = useState(false);
  const [capsLock, setCapsLock] = useState(false);

  const readCapsLock = (event: KeyboardEvent<HTMLInputElement>) => {
    setCapsLock(event.getModifierState?.('CapsLock') ?? false);
  };

  return (
    <AuthField
      {...props}
      icon={LockKeyhole}
      type={visible ? 'text' : 'password'}
      onKeyDown={readCapsLock}
      onKeyUp={readCapsLock}
      onBlur={() => setCapsLock(false)}
      notice={capsLock ? 'Caps Lock is on' : undefined}
      trailing={
        <button
          type="button"
          onClick={() => setVisible((current) => !current)}
          aria-label="Show password"
          aria-pressed={visible}
          aria-controls={props.id}
          className={cn(
            'relative grid size-9 place-items-center rounded-md text-muted-foreground',
            'transition-[color,background-color] duration-[var(--duration-fast)] ease-[var(--ease-out-soft)]',
            'hover:bg-foreground/8 hover:text-foreground active:scale-95',
          )}
        >
          <Eye
            aria-hidden
            className={cn(
              'absolute size-4 transition-[opacity,transform] duration-[var(--duration-base)] ease-[var(--ease-out-soft)]',
              visible ? 'scale-50 rotate-45 opacity-0' : 'scale-100 rotate-0 opacity-100',
            )}
          />
          <EyeOff
            aria-hidden
            className={cn(
              'absolute size-4 transition-[opacity,transform] duration-[var(--duration-base)] ease-[var(--ease-out-soft)]',
              visible ? 'scale-100 rotate-0 opacity-100' : 'scale-50 -rotate-45 opacity-0',
            )}
          />
        </button>
      }
    />
  );
}
