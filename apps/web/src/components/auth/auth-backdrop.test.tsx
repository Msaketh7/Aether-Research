import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AUTH_BACKDROP_VIDEO, AuthBackdrop } from './auth-backdrop';

function preferReducedMotion(reduce: boolean) {
  vi.spyOn(window, 'matchMedia').mockImplementation((query: string) => ({
    matches: reduce && query.includes('reduce'),
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}

afterEach(() => {
  vi.restoreAllMocks();
});

/**
 * The backdrop's promises are about what it costs a visitor, not how it
 * looks: somebody who asked for no motion never downloads six megabytes of
 * it, and somebody who gets the motion gets a way to stop it.
 */
describe('AuthBackdrop', () => {
  it('never mounts the video for a visitor who asked for reduced motion', () => {
    preferReducedMotion(true);
    const { container } = render(<AuthBackdrop />);

    expect(container.querySelector('video')).toBeNull();
    // Nothing moves, so there is nothing to pause.
    expect(screen.queryByRole('button', { name: /background animation/ })).toBeNull();
  });

  it('plays the loop muted and offers a pause control when motion is allowed', () => {
    preferReducedMotion(false);
    const { container } = render(<AuthBackdrop />);

    const video = container.querySelector('video');
    expect(video).not.toBeNull();
    expect(video).toHaveAttribute('src', AUTH_BACKDROP_VIDEO);
    expect(video?.muted).toBe(true);
    expect(video?.loop).toBe(true);
    // WCAG 2.2.2: anything moving for more than five seconds can be stopped.
    expect(screen.getByRole('button', { name: 'Pause background animation' })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
  });
});
