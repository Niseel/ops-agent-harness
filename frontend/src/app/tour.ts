import {
  Component,
  ElementRef,
  afterRenderEffect,
  computed,
  output,
  signal,
  viewChild,
} from '@angular/core';

/** One stop of the tour: the element with this id, or the section around it, is highlighted. */
export interface TourStep {
  id: string;
  title: string;
  text: string;
}

export const TOUR_STEPS: TourStep[] = [
  {
    id: 'new-run',
    title: 'Start a run',
    text: 'Write an objective and press Run. For example: "payments-api is returning 5xx errors. Investigate and open an incident if needed." Fault switches and limits start the demo scenarios.',
  },
  {
    id: 'runs',
    title: 'Runs',
    text: 'Every run, newest first, with its status. Click a run to open it.',
  },
  {
    id: 'now',
    title: 'Now',
    text: 'Who works at this moment: the LLM thinks, the harness checks limits and runs tools, or a person must decide. When the run ends, this panel shows the result.',
  },
  {
    id: 'flow',
    title: 'Flow',
    text: 'The agent loop. The running node is lit, and each node shows its last status. The legend gives the colour of the LLM, the harness, the tools and the person.',
  },
  {
    id: 'run-title',
    title: 'Timeline',
    text: 'Each LLM decision and each tool call, with its arguments, attempts, result and time. Click a step to see its data in Detail.',
  },
  {
    id: 'approvals',
    title: 'Approvals',
    text: 'create_incident waits here for a person. Approve it, edit it, or reject it with a reason before the countdown ends.',
  },
  {
    id: 'budget',
    title: 'Budget',
    text: "Steps, tool calls and seconds used against the run's limits.",
  },
  {
    id: 'attention',
    title: 'Attention',
    text: 'What to look at: retries, failures, limits and degraded search, each with a colour, an icon and a word.',
  },
  {
    id: 'console',
    title: 'Console and Detail',
    text: 'Every event of the run, filtered by All, Tools or Attention. Click a line to see its data in Detail.',
  },
  {
    id: 'tabs',
    title: 'More tabs',
    text: 'Evaluation measures search quality on a golden set. Incidents lists the incidents that runs created.',
  },
];

export interface Box {
  top: number;
  left: number;
  width: number;
  height: number;
}

export interface Placement {
  ring: Box | null;
  place: 'top' | 'bottom' | 'center';
}

const RING_PAD = 4;

/**
 * Where the ring and the card go in a viewport of `width` × `height`: the ring is the target grown by 4 px and
 * kept on screen; the card sits on the half away from the target. No target, or one with no size: centre.
 */
export function placement(rect: Box | null | undefined, width: number, height: number): Placement {
  if (!rect || !rect.width || !rect.height) return { ring: null, place: 'center' };
  const left = Math.max(0, rect.left - RING_PAD);
  const top = Math.max(0, rect.top - RING_PAD);
  const right = Math.min(width, rect.left + rect.width + RING_PAD);
  const bottom = Math.min(height, rect.top + rect.height + RING_PAD);
  return {
    ring: { left, top, width: right - left, height: bottom - top },
    place: rect.top + rect.height / 2 < height / 2 ? 'bottom' : 'top',
  };
}

/**
 * The Help tour (spec: UI): a modal `<dialog>` that walks through the page, one panel at a time. Esc, × and Done
 * close it; nothing is stored, and only Help starts it.
 */
@Component({
  selector: 'app-tour',
  host: { '(window:resize)': 'measure()' },
  template: `
    <dialog
      #dialog
      class="tour"
      aria-labelledby="tour-title"
      aria-describedby="tour-text"
      (close)="onClose()"
    >
      @if (open()) {
        @if (spot().ring; as ring) {
          <div
            class="tour-ring"
            aria-hidden="true"
            [style.top.px]="ring.top"
            [style.left.px]="ring.left"
            [style.width.px]="ring.width"
            [style.height.px]="ring.height"
          ></div>
        }
        <div class="tour-card" [attr.data-place]="spot().place">
          <div class="panel-head">
            <h2 id="tour-title">{{ current().title }}</h2>
            <button type="button" class="close" aria-label="Close the tour" (click)="close()">
              ×
            </button>
          </div>
          <div aria-live="polite">
            <p id="tour-text">{{ current().text }}</p>
          </div>
          <div class="row">
            <span class="muted">Step {{ step() + 1 }} of {{ steps.length }}</span>
            <button type="button" [disabled]="step() === 0" (click)="back()">Back</button>
            <button #next type="button" (click)="forward()">{{ last() ? 'Done' : 'Next' }}</button>
          </div>
        </div>
      }
    </dialog>
  `,
})
export class Tour {
  readonly closed = output<void>();
  protected readonly steps = TOUR_STEPS;
  readonly step = signal(0);
  readonly open = signal(false);
  readonly spot = signal<Placement>({ ring: null, place: 'center' });
  protected readonly current = computed(() => TOUR_STEPS[this.step()]);
  protected readonly last = computed(() => this.step() === TOUR_STEPS.length - 1);
  private readonly dialog = viewChild.required<ElementRef<HTMLDialogElement>>('dialog');
  private readonly next = viewChild<ElementRef<HTMLButtonElement>>('next');
  private focusNext = false;

  constructor() {
    afterRenderEffect({
      write: () => {
        this.step(); // each step: find the target, then place the ring and the card
        if (!this.open()) return;
        this.measure();
        if (this.focusNext) {
          this.focusNext = false;
          this.next()?.nativeElement.focus();
        }
      },
    });
  }

  start(): void {
    this.step.set(0);
    this.open.set(true);
    this.focusNext = true;
    this.dialog().nativeElement.showModal();
  }

  /** Back to step 1 disables Back, which has focus: hand focus to Next, so it stays in the dialog. */
  back(): void {
    this.step.set(this.step() - 1);
    if (this.step() === 0) this.focusNext = true;
  }

  forward(): void {
    if (this.last()) this.close();
    else this.step.set(this.step() + 1);
  }

  /** Every way out (Done, ×, Esc) ends in the dialog's `close` event. */
  close(): void {
    this.dialog().nativeElement.close();
  }

  protected onClose(): void {
    this.open.set(false);
    this.step.set(0);
    this.closed.emit();
  }

  protected measure(): void {
    if (!this.open()) return;
    const element = document.getElementById(this.current().id);
    const target = element?.closest('section') ?? element;
    target?.scrollIntoView?.({ block: 'nearest' }); // narrow screens: the target first comes into view
    this.spot.set(
      placement(target?.getBoundingClientRect(), window.innerWidth, window.innerHeight),
    );
  }
}
