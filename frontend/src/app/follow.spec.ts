import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Follow } from './follow';

@Component({
  imports: [Follow],
  template: `<ol [appFollow]="count()"></ol>`,
})
class Host {
  readonly count = signal(0);
}

/** jsdom has no layout: give the list a height and a content height, and let `scrollTop` be a plain field. */
function sized(list: HTMLElement, scrollHeight: number) {
  Object.defineProperty(list, 'clientHeight', { configurable: true, value: 100 });
  Object.defineProperty(list, 'scrollHeight', { configurable: true, value: scrollHeight });
}

describe('Follow', () => {
  async function setup() {
    const fixture = TestBed.createComponent(Host);
    await fixture.whenStable();
    const list = (fixture.nativeElement as HTMLElement).querySelector('ol')!;
    const add = async (count: number, scrollHeight: number) => {
      sized(list, scrollHeight);
      fixture.componentInstance.count.set(count);
      await fixture.whenStable();
    };
    const scrollTo = (top: number) => {
      list.scrollTop = top;
      list.dispatchEvent(new Event('scroll'));
    };
    return { list, add, scrollTo };
  }

  it('follows the newest item', async () => {
    const { list, add } = await setup();
    await add(1, 500);
    expect(list.scrollTop).toBe(500);
    await add(2, 600);
    expect(list.scrollTop).toBe(600);
  });

  it('stays where the user scrolled back', async () => {
    const { list, add, scrollTo } = await setup();
    await add(1, 500);
    scrollTo(100);
    await add(2, 600);
    expect(list.scrollTop).toBe(100);
  });

  it('follows again once the user is back at the end', async () => {
    const { list, add, scrollTo } = await setup();
    await add(1, 500);
    scrollTo(100);
    await add(2, 600);
    scrollTo(480); // 600 - 480 - 100 = 20 px from the end
    await add(3, 700);
    expect(list.scrollTop).toBe(700);
  });

  it('starts at the end of a new, shorter list', async () => {
    const { list, add, scrollTo } = await setup();
    await add(5, 900);
    scrollTo(100);
    await add(1, 300);
    expect(list.scrollTop).toBe(300);
  });
});
