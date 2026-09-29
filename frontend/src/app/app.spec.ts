import { TestBed } from '@angular/core/testing';
import { App } from './app';

describe('App', () => {
  // The Runs tab loads its lists and the Evaluation tab its latest report at once: no network in tests.
  beforeEach(() =>
    vi.stubGlobal('fetch', async (url: string) =>
      url === '/api/eval/kb/latest'
        ? new Response('{"detail": "no evaluation report yet"}', { status: 404 })
        : new Response('[]', { status: 200 }),
    ),
  );
  afterEach(() => vi.unstubAllGlobals());

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

  it('keeps a running evaluation alive across a tab switch', async () => {
    let sendProgress!: () => void;
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      if (init?.method === 'POST') {
        return new Response(
          new ReadableStream({
            start(controller) {
              sendProgress = () =>
                controller.enqueue(
                  new TextEncoder().encode('event: progress\ndata: {"done":1,"total":5}\n\n'),
                );
            },
          }),
          { status: 200 },
        );
      }
      if (url === '/api/eval/kb/latest') {
        return new Response('{"detail": "no evaluation report yet"}', { status: 404 });
      }
      return new Response('[]', { status: 200 });
    });

    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    const buttons = [...root.querySelectorAll<HTMLButtonElement>('.tabs button')];

    buttons[1].click(); // Evaluation tab
    await fixture.whenStable();
    root.querySelector<HTMLButtonElement>('[data-tab="eval"] button')!.click(); // Run evaluation
    await fixture.whenStable();
    sendProgress();
    await vi.waitFor(async () => {
      await fixture.whenStable();
      expect(root.querySelector('[data-tab="eval"]')!.textContent).toContain('1/5 questions');
    });

    buttons[0].click(); // Runs tab
    await fixture.whenStable();
    buttons[1].click(); // back to Evaluation
    await fixture.whenStable();
    expect(root.querySelector('[data-tab="eval"]')!.textContent).toContain('1/5 questions');
  });

  describe('Help', () => {
    beforeEach(() => {
      HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
        this.open = true;
      };
      HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) {
        this.open = false;
        this.dispatchEvent(new Event('close'));
      };
    });
    afterEach(() => {
      delete (HTMLDialogElement.prototype as Partial<HTMLDialogElement>).showModal;
      delete (HTMLDialogElement.prototype as Partial<HTMLDialogElement>).close;
    });

    it('sits in the header after the tabs, outside them', async () => {
      const fixture = TestBed.createComponent(App);
      await fixture.whenStable();
      const root = fixture.nativeElement as HTMLElement;
      const header = root.querySelector('header.app-header')!;
      const help = header.querySelector<HTMLButtonElement>('button.help')!;
      expect(help.textContent?.trim()).toBe('Help');
      expect(help.getAttribute('aria-haspopup')).toBe('dialog');
      expect(help.closest('.tabs')).toBeNull();
      expect(header.querySelector('nav#tabs')?.nextElementSibling).toBe(help);
    });

    it('shows the Runs tab, opens the tour, and gives focus back to Help when the tour closes', async () => {
      const fixture = TestBed.createComponent(App);
      await fixture.whenStable();
      const root = fixture.nativeElement as HTMLElement;
      root.querySelectorAll<HTMLButtonElement>('.tabs button')[1].click(); // Evaluation
      await fixture.whenStable();
      const help = root.querySelector<HTMLButtonElement>('button.help')!;
      help.click();
      await fixture.whenStable();

      const dialog = root.querySelector('dialog.tour') as HTMLDialogElement;
      expect(dialog.open).toBe(true);
      expect(fixture.componentInstance.tab()).toBe('runs');
      expect(root.querySelector<HTMLElement>('main > section[data-tab="runs"]')!.hidden).toBe(
        false,
      );
      expect(document.activeElement?.textContent?.trim()).toBe('Next');

      dialog.close(); // Esc ends in this event
      await fixture.whenStable();
      expect(document.activeElement).toBe(help);
    });
  });
});
