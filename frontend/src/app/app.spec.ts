import { TestBed } from '@angular/core/testing';
import { App } from './app';

describe('App', () => {
  it('renders the three tabs and shows only the chosen panel', async () => {
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    const buttons = [...root.querySelectorAll<HTMLButtonElement>('.tabs button')];
    expect(buttons.map((b) => b.textContent?.trim())).toEqual(['Runs', 'Evaluation', 'Incidents']);
    const visible = () =>
      [...root.querySelectorAll<HTMLElement>('main > section')]
        .filter((s) => !s.hidden)
        .map((s) => s.dataset['tab']);
    expect(visible()).toEqual(['runs']);
    expect(buttons[0].getAttribute('aria-current')).toBe('page');

    buttons[1].click();
    await fixture.whenStable();
    expect(visible()).toEqual(['eval']);
    expect(buttons[1].getAttribute('aria-current')).toBe('page');
    expect(buttons[0].hasAttribute('aria-current')).toBe(false);
  });
});
