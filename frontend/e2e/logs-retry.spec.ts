import { test, expect } from '@playwright/test'

// Retry on the Logs page (M9 stage 8). The rules — which jobs qualify, that a scoring
// retry resumes, the refusals — live in the backend and are tested there
// (backend/tests/test_job_retry.py). A real stopped scoring job cannot be made here:
// the scorers need torch, which CI lacks, and a local run would load real models. So
// the History tab's list and the retry POST are answered in the page, and what is
// checked is the wiring: the button follows `retryable`, it posts for the right job,
// and both of the server's answers reach the user.

function fakeJob(id: string, label: string, retryable: boolean, over: Record<string, unknown> = {}) {
  return {
    id,
    job_type: 'quality_score',
    label,
    status: retryable ? 'failed' : 'completed',
    dataset_id: null,
    total_items: 10,
    // What a scoring job stores: nothing written. Only a caption job and an automated
    // run's own steps write the count.
    done_items: 0,
    error_msg: retryable ? 'Interrupted by server shutdown or restart' : null,
    result_data: {},
    config: {},
    created_at: new Date().toISOString(),
    started_at: null,
    finished_at: null,
    retryable,
    ...over,
  }
}

test('Retry shows on retryable jobs only and reports the server answer', async ({ page }) => {
  const stopped = fakeJob('e2e-retry-stopped', 'E2E stopped scoring', true)
  const finished = fakeJob('e2e-retry-finished', 'E2E finished scoring', false)
  // A caption job writes how far it got (M9 stage 10), so its stopped row can say so.
  const captioning = fakeJob('e2e-stopped-caption', 'E2E stopped captioning', false, {
    job_type: 'caption',
    status: 'cancelled',
    done_items: 4,
  })

  // Only the History tab's own request: TopBar's reconcile asks with `status`/`ids`,
  // and must keep reaching the real backend.
  await page.route(
    (url) =>
      url.pathname === '/api/v1/jobs/' &&
      !url.searchParams.has('status') &&
      !url.searchParams.has('ids'),
    (route) => route.fulfill({ json: [stopped, finished, captioning] }),
  )

  const posted: string[] = []
  let answer: { status: number; json: unknown } = {
    status: 200,
    json: { job_id: null, message: 'Nothing left to score' },
  }
  await page.route('**/api/v1/jobs/*/retry', async (route) => {
    posted.push(new URL(route.request().url()).pathname)
    await route.fulfill({ status: answer.status, json: answer.json })
  })

  await page.goto('/logs')
  const retry = page.getByRole('button', { name: 'Retry', exact: true })

  await expect(page.getByText('E2E stopped scoring')).toBeVisible()
  await expect(page.getByText('E2E finished scoring')).toBeVisible()
  // One button for two rows: `retryable` is the server's call, and the page follows it.
  // The POST below proves the button belongs to the stopped job's row.
  await expect(retry).toHaveCount(1)
  // The item count never claims `0/10` for work the row does not record: a completed
  // job did all of its items, a stopped one shows only how many it was given, and a
  // stopped caption job shows the count it stored.
  await expect(page.getByText('E2E stopped captioning')).toBeVisible()
  await expect(page.getByText('10/10', { exact: true })).toHaveCount(1)
  await expect(page.getByText('10 items', { exact: true })).toHaveCount(1)
  await expect(page.getByText('4/10', { exact: true })).toHaveCount(1)
  await expect(page.getByText('0/10', { exact: true })).toHaveCount(0)

  await retry.click()
  await expect(page.getByText('Nothing left to score')).toBeVisible()
  expect(posted).toEqual(['/api/v1/jobs/e2e-retry-stopped/retry'])

  // A refusal is shown, not swallowed (unlike cancel).
  answer = { status: 409, json: { detail: 'A retry of this job is already queued or running.' } }
  await retry.click()
  await expect(page.getByText('A retry of this job is already queued or running.')).toBeVisible()
})
