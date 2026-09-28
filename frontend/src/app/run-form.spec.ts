import { TestBed } from '@angular/core/testing';
import { runBody, RunForm } from './run-form';

describe('runBody', () => {
  it('sends the objective alone when nothing else is set', () => {
    expect(
      runBody({
        objective: 'Check payments-api',
        llm: 'default',
        evaluate: 'default',
        'fault.get_service_status': 'off',
        'times.get_service_status': '',
        'limit.max_steps': '',
      }),
    ).toEqual({ objective: 'Check payments-api' });
  });

  it('sends limits and times as numbers, and leaves off faults out', () => {
    expect(
      runBody({
        objective: 'Check payments-api',
        llm: 'fake',
        evaluate: 'off',
        'fault.get_service_status': 'timeout',
        'times.get_service_status': '2',
        'fault.search_knowledge_base': 'off',
        'times.search_knowledge_base': '3',
        'fault.embeddings': 'error',
        'times.embeddings': '',
        'limit.max_steps': '5',
        'limit.max_tool_calls': '',
      }),
    ).toEqual({
      objective: 'Check payments-api',
      llm: 'fake',
      options: {
        evaluate: false,
        faults: {
          get_service_status: { mode: 'timeout', times: 2 },
          embeddings: { mode: 'error' },
        },
        limits: { max_steps: 5 },
      },
    });
  });

  it('sends evaluate on as true', () => {
    expect(runBody({ objective: 'x', evaluate: 'on' })).toEqual({
      objective: 'x',
      options: { evaluate: true },
    });
  });
});

describe('RunForm', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('posts the body and emits created', async () => {
    const fetch = vi.fn(
      async () => new Response(JSON.stringify({ run_id: 'r1' }), { status: 202 }),
    );
    vi.stubGlobal('fetch', fetch);
    const fixture = TestBed.createComponent(RunForm);
    await fixture.whenStable();
    const created: string[] = [];
    fixture.componentInstance.created.subscribe((id: string) => created.push(id));

    const form = fixture.nativeElement.querySelector('form') as HTMLFormElement;
    (form.querySelector('textarea[name=objective]') as HTMLTextAreaElement).value = 'Check it';
    form.dispatchEvent(new SubmitEvent('submit', { bubbles: true, cancelable: true }));
    await fixture.whenStable();

    expect(fetch).toHaveBeenCalledWith(
      '/api/runs',
      expect.objectContaining({ body: JSON.stringify({ objective: 'Check it' }) }),
    );
    expect(created).toEqual(['r1']);
    expect(fixture.nativeElement.querySelector('.error')).toBeNull();
  });

  it('shows the detail of a 422', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: 'objective: Field required' }), { status: 422 }),
      ),
    );
    const fixture = TestBed.createComponent(RunForm);
    await fixture.whenStable();
    const created: string[] = [];
    fixture.componentInstance.created.subscribe((id: string) => created.push(id));

    const form = fixture.nativeElement.querySelector('form') as HTMLFormElement;
    form.dispatchEvent(new SubmitEvent('submit', { bubbles: true, cancelable: true }));
    await fixture.whenStable();

    expect(fixture.nativeElement.querySelector('.error')?.textContent).toBe(
      'objective: Field required',
    );
    expect(created).toEqual([]);
  });
});
