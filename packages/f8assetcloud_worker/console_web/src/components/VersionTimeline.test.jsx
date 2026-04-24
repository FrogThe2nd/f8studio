import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { VersionTimeline } from './VersionTimeline.jsx';

describe('VersionTimeline', () => {
  afterEach(() => {
    cleanup();
  });

  it('shows compact plain-text summaries for Markdown release notes', () => {
    render(
      <VersionTimeline
        versions={[{
          versionNumber: 2,
          changeSummary: '## Release\n\nFixed **timing** in [player](https://example.com).',
          createdAt: '2026-04-21T10:00:00.000Z',
        }]}
        selectedVersionNumber={2}
        onSelect={vi.fn()}
      />,
    );

    expect(screen.getByText('Version 2')).toBeTruthy();
    expect(screen.getByText((_, element) => element?.textContent === 'Release\nFixed timing in player.')).toBeTruthy();
    expect(screen.queryByText(/Fixed \*\*timing\*\*/)).toBeNull();
  });
});
