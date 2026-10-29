import { within } from '@testing-library/react';
import { expect } from 'vitest';

/**
 * Wide tables turn into labelled cards on narrow containers (see `.table--stack` in components.css),
 * where each cell shows its own `data-label`. This keeps those labels in step with the headers.
 */
export function expectCellsLabelledByHeaders(table: HTMLElement, trailingActionsColumn = false) {
  const headers = within(table)
    .getAllByRole('columnheader')
    .map((header) => header.textContent?.trim() ?? '');
  const firstRow = within(table).getAllByRole('row')[1]!;
  const cells = within(firstRow).getAllByRole('cell');
  expect(cells).toHaveLength(headers.length);
  cells.forEach((cell, index) => {
    const isActions = trailingActionsColumn && index === headers.length - 1;
    // The actions column's header is visually hidden, and its cell shows no label.
    expect(cell.getAttribute('data-label'), `cell ${index}`).toBe(isActions ? '' : headers[index]);
  });
}
