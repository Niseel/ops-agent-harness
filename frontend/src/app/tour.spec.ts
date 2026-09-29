import { Component, viewChild } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { TOUR_STEPS, Tour, placement } from './tour';

// jsdom has no dialog methods: `showModal` sets `open`, `close` removes it and fires `close`, as browsers do.
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

describe('placement', () => {
  it('puts the card below a target in the top half and above one in the bottom half', () => {
    expect(placement({ top: 50, left: 20, width: 200, height: 100 }, 1280, 720)).toEqual({
      ring: { top: 46, left: 16, width: 208, height: 108 },
      place: 'bottom',
    });
    expect(placement({ top: 500, left: 20, width: 200, height: 100 }, 1280, 720).place).toBe('top');
  });

  it('keeps the ring on screen', () => {
    expect(placement({ top: 0, left: 0, width: 1280, height: 720 }, 1280, 720).ring).toEqual({
      top: 0,
      left: 0,
      width: 1280,
      height: 720,
    });
  });

  it('centres the card with no ring when there is no target or it has no size', () => {
    const centre = { ring: null, place: 'center' };
    expect(placement(null, 1280, 720)).toEqual(centre);
    expect(placement({ top: 0, left: 0, width: 0, height: 0 }, 1280, 720)).toEqual(centre);
  });
});

@Component({
  imports: [Tour],
  template: `
    <section><h2 id="new-run">New run</h2></section>
    <app-tour (closed)="closed = closed + 1" />
  `,
})
class Host {
  closed = 0;
  readonly tour = viewChild.required(Tour);
}

describe('Tour', () => {
  async function setup() {
    const fixture = TestBed.createComponent(Host);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    const host = fixture.componentInstance;
    const dialog = root.querySelector('dialog')!;
    const button = (text: string) =>
      [...root.querySelectorAll<HTMLButtonElement>('.tour-card button')].find(
        (b) => b.textContent?.trim() === text,
      )!;
    const click = async (text: string) => {
      button(text).click();
      await fixture.whenStable();
    };
    const start = async () => {
      host.tour().start();
      await fixture.whenStable();
    };
    return { fixture, root, host, dialog, button, click, start };
  }

  it('is a modal dialog named by its title and text, closed until Help starts it', async () => {
    const { dialog, root } = await setup();
    expect(dialog.classList.contains('tour')).toBe(true);
    expect(dialog.getAttribute('aria-labelledby')).toBe('tour-title');
    expect(dialog.getAttribute('aria-describedby')).toBe('tour-text');
    expect(dialog.open).toBe(false);
    expect(root.querySelector('.tour-card')).toBeNull();
  });

  it('opens at step 1 of 10 and focuses Next', async () => {
    const { dialog, root, start } = await setup();
    await start();
    expect(dialog.open).toBe(true);
    expect(root.querySelector('#tour-title')?.textContent).toBe(TOUR_STEPS[0].title);
    expect(root.querySelector('#tour-text')?.textContent).toBe(TOUR_STEPS[0].text);
    expect(root.querySelector('.tour-card')?.textContent).toContain('Step 1 of 10');
    expect(document.activeElement?.textContent?.trim()).toBe('Next');
  });

  it('walks the steps with Next and Back, and Done on the last step closes it', async () => {
    const { dialog, root, host, button, click, start } = await setup();
    await start();
    expect(button('Back').disabled).toBe(true);
    await click('Next');
    expect(root.querySelector('#tour-title')?.textContent).toBe(TOUR_STEPS[1].title);
    expect(button('Back').disabled).toBe(false);
    await click('Back');
    expect(root.querySelector('#tour-title')?.textContent).toBe(TOUR_STEPS[0].title);
    for (let i = 1; i < TOUR_STEPS.length; i++) await click('Next');
    expect(root.querySelector('.tour-card')?.textContent).toContain('Step 10 of 10');
    expect(button('Next')).toBeUndefined();
    await click('Done');
    expect(dialog.open).toBe(false);
    expect(host.closed).toBe(1);
    expect(root.querySelector('.tour-card')).toBeNull();
  });

  it('keeps focus in the dialog when Back reaches step 1', async () => {
    const { button, click, start } = await setup();
    await start();
    await click('Next');
    button('Back').focus(); // a keyboard user presses Back
    await click('Back'); // Back is now disabled and cannot keep focus
    expect(document.activeElement?.textContent?.trim()).toBe('Next');
  });

  it('rings a target that has a size, and places the card away from it', async () => {
    const { root, start } = await setup();
    const section = root.querySelector('section')!;
    section.getBoundingClientRect = () =>
      ({ top: 50, left: 20, width: 200, height: 100 }) as DOMRect;
    await start();
    const ring = root.querySelector<HTMLElement>('.tour-ring')!;
    expect(ring.getAttribute('aria-hidden')).toBe('true');
    expect([ring.style.top, ring.style.left, ring.style.width, ring.style.height]).toEqual([
      '46px',
      '16px',
      '208px',
      '108px',
    ]);
    expect(root.querySelector<HTMLElement>('.tour-card')?.dataset['place']).toBe('bottom');
  });

  it('centres the card with no ring when a target is missing or has no size, and still walks on', async () => {
    const { root, click, start } = await setup();
    await start(); // step 1's section has no size in jsdom
    expect(root.querySelector('.tour-ring')).toBeNull();
    expect(root.querySelector<HTMLElement>('.tour-card')?.dataset['place']).toBe('center');
    await click('Next'); // step 2's target is not on this page at all
    expect(root.querySelector('#tour-title')?.textContent).toBe(TOUR_STEPS[1].title);
    expect(root.querySelector<HTMLElement>('.tour-card')?.dataset['place']).toBe('center');
  });

  it('closes with the × button and with the dialog close event (Esc), each time emitting closed', async () => {
    const { dialog, host, root, start } = await setup();
    await start();
    const close = root.querySelector<HTMLButtonElement>('button[aria-label="Close the tour"]')!;
    expect(close.textContent?.trim()).toBe('×');
    close.click();
    expect(dialog.open).toBe(false);
    expect(host.closed).toBe(1);

    await start();
    dialog.close(); // what Esc ends in
    expect(host.closed).toBe(2);
  });

  it('starts again at step 1 after a close', async () => {
    const { root, click, start, dialog } = await setup();
    await start();
    await click('Next');
    dialog.close();
    await start();
    expect(root.querySelector('#tour-title')?.textContent).toBe(TOUR_STEPS[0].title);
  });

  it('resets the step and the open flag when it closes', async () => {
    const { host, click, start, dialog } = await setup();
    await start();
    await click('Next');
    expect(host.tour().step()).toBe(1);
    dialog.close();
    expect(host.tour().step()).toBe(0);
    expect(host.tour().open()).toBe(false);
  });

  it('has the ten steps of the plan, each with a target id, a title and a text', () => {
    expect(TOUR_STEPS.map((s) => s.id)).toEqual([
      'new-run',
      'runs',
      'now',
      'flow',
      'run-title',
      'approvals',
      'budget',
      'attention',
      'console',
      'tabs',
    ]);
    expect(TOUR_STEPS.every((s) => s.title && s.text)).toBe(true);
  });
});
