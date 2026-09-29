import { Directive, ElementRef, afterRenderEffect, inject, input } from '@angular/core';

/** Within this many pixels of the end counts as "at the end". */
export const AT_END_PX = 24;

/**
 * Keeps a scrolling list at its newest item while the user is at the end. When the user scrolls back, the list
 * stays where the user is; at the end again, it follows again. The input changes when items are added.
 */
@Directive({ selector: '[appFollow]', host: { '(scroll)': 'onScroll()' } })
export class Follow {
  readonly appFollow = input(0);
  private readonly el: HTMLElement = inject(ElementRef).nativeElement;
  private atEnd = true;
  private count = 0;

  constructor() {
    afterRenderEffect({
      write: () => {
        const count = this.appFollow();
        if (count < this.count) this.atEnd = true; // a new list (another run): start at its end
        this.count = count;
        if (this.atEnd) this.el.scrollTop = this.el.scrollHeight;
      },
    });
  }

  protected onScroll(): void {
    const el = this.el;
    this.atEnd = el.scrollHeight - el.scrollTop - el.clientHeight <= AT_END_PX;
  }
}
