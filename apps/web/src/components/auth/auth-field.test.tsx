import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { PasswordField } from './auth-field';

/**
 * The password field's two additions over a plain input: a reveal toggle and
 * a Caps Lock warning. Both exist to stop a correct password being refused,
 * so what matters is that they change what the person sees and what a screen
 * reader hears - not how they are drawn.
 */
describe('PasswordField', () => {
  it('reveals and hides the password with a toggle that reports its state', async () => {
    const user = userEvent.setup();
    render(<PasswordField id="password" label="Password" defaultValue="hunter2" />);

    // The label resolves to the input alone: "Show password" must not
    // collide with it, or every `getByLabelText('Password')` in the suite breaks.
    const input = screen.getByLabelText('Password');
    const toggle = screen.getByRole('button', { name: 'Show password' });
    expect(input).toHaveAttribute('type', 'password');
    expect(toggle).toHaveAttribute('aria-pressed', 'false');

    await user.click(toggle);
    expect(input).toHaveAttribute('type', 'text');
    expect(toggle).toHaveAttribute('aria-pressed', 'true');

    await user.click(toggle);
    expect(input).toHaveAttribute('type', 'password');
  });

  it('warns about Caps Lock and ties the warning to the field', () => {
    render(<PasswordField id="password" label="Password" />);
    const input = screen.getByLabelText('Password');

    fireEvent.keyDown(input, { key: 'A', modifierCapsLock: true });

    expect(screen.getByText('Caps Lock is on')).toBeInTheDocument();
    expect(input.getAttribute('aria-describedby')).toContain('password-notice');

    fireEvent.blur(input);
    expect(screen.queryByText('Caps Lock is on')).not.toBeInTheDocument();
  });

  it("puts the server's refusal in place of the help, wired to the input", () => {
    render(
      <PasswordField
        id="password"
        label="Password"
        help="At least 12 characters."
        error={['Use at least 12 characters.']}
      />,
    );
    const input = screen.getByLabelText('Password');

    expect(screen.queryByText('At least 12 characters.')).not.toBeInTheDocument();
    expect(screen.getByText('Use at least 12 characters.')).toHaveAttribute(
      'id',
      'password-message',
    );
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(input.getAttribute('aria-describedby')).toContain('password-message');
  });
});
