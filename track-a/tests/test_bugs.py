"""Regression tests for the six seeded defects in ClearLedger.

Each test targets a specific bug that was identified and fixed.
Includes one failing-before/passing-after reproduction (test_mixed_csv_partial_import)
and one custom input case (test_payment_for_nonexistent_invoice_stays_unmatched).
"""
import tempfile
import unittest
from pathlib import Path
from ledger import storage, reporting, importing


class BugRegressionTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = storage.connect(Path(self.tmp.name) / 'test.sqlite3')
        storage.seed(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    # ------------------------------------------------------------------
    # Bug 1: Payment matching must use (customer_id, invoice_number),
    #         not amount. Two invoices of INR 1250 exist (HARBOR/INV-100
    #         and MAPLE/INV-200). A payment for MAPLE/INV-200 must attach
    #         to MAPLE, not to HARBOR just because the amount matches.
    # ------------------------------------------------------------------

    def test_payment_matched_by_identity_not_amount(self):
        """Payment for MAPLE/INV-200 at INR 1250 must attach to MAPLE, not HARBOR."""
        csv_text = (
            'payment_id,customer_id,invoice_number,amount\n'
            'PAY-BUG1,MAPLE,INV-200,1250.00\n'
        )
        result = importing.import_csv(self.db, csv_text, 'payments')
        self.assertEqual(result['imported'], 1)

        # Verify the payment is on MAPLE/INV-200
        maple_inv = next(r for r in reporting.invoices(self.db)
                         if r['customer_id'] == 'MAPLE' and r['invoice_number'] == 'INV-200')
        self.assertEqual(maple_inv['paid'], 1250.00)
        self.assertEqual(maple_inv['status'], 'paid')

        # HARBOR/INV-100 must be unaffected
        harbor_inv = next(r for r in reporting.invoices(self.db)
                          if r['customer_id'] == 'HARBOR' and r['invoice_number'] == 'INV-100')
        self.assertEqual(harbor_inv['paid'], 0.00)
        self.assertEqual(harbor_inv['status'], 'open')

    def test_payment_amount_match_does_not_override_identity(self):
        """A payment whose amount happens to match a different invoice must still
        use the identity fields, not the amount, for matching."""
        # INV-300 (NORTH) has amount 19.99. Import a payment that references
        # NORTH/INV-300 but with a different amount (9.99). It must attach to
        # NORTH/INV-300 by identity, not wander to some other invoice.
        csv_text = (
            'payment_id,customer_id,invoice_number,amount\n'
            'PAY-BUG1B,NORTH,INV-300,9.99\n'
        )
        result = importing.import_csv(self.db, csv_text, 'payments')
        self.assertEqual(result['imported'], 1)

        north_inv = next(r for r in reporting.invoices(self.db)
                         if r['customer_id'] == 'NORTH' and r['invoice_number'] == 'INV-300')
        # Seed already has SEED-2 paying 10.00 on INV-300, so total paid = 10.00 + 9.99
        self.assertAlmostEqual(north_inv['paid'], 19.99, places=2)
        self.assertEqual(north_inv['status'], 'paid')

    # ------------------------------------------------------------------
    # Bug 2: Status filter must return correct records.
    #         'open' must return only open invoices, 'paid' only paid.
    # ------------------------------------------------------------------

    def test_open_filter_returns_only_open_invoices(self):
        """Filtering by 'open' must return only invoices with positive balance."""
        open_invoices = reporting.invoices(self.db, 'open')
        for inv in open_invoices:
            self.assertEqual(inv['status'], 'open',
                             f"Open filter returned a {inv['status']} invoice: {inv['invoice_number']}")
        # Seed has 5 open invoices
        self.assertEqual(len(open_invoices), 5)

    def test_paid_filter_returns_only_paid_invoices(self):
        """Filtering by 'paid' must return only invoices with zero or negative balance."""
        paid_invoices = reporting.invoices(self.db, 'paid')
        for inv in paid_invoices:
            self.assertEqual(inv['status'], 'paid',
                             f"Paid filter returned a {inv['status']} invoice: {inv['invoice_number']}")
        # Seed has 1 paid invoice (INV-101, paid 300/300)
        self.assertEqual(len(paid_invoices), 1)

    def test_open_and_paid_counts_match_overview(self):
        """Open count from filter must agree with overview summary."""
        overview = reporting.overview(self.db)
        open_invoices = reporting.invoices(self.db, 'open')
        paid_invoices = reporting.invoices(self.db, 'paid')
        all_invoices = reporting.invoices(self.db, 'all')
        self.assertEqual(overview['summary']['open_count'], len(open_invoices))
        self.assertEqual(len(all_invoices), len(open_invoices) + len(paid_invoices))

    # ------------------------------------------------------------------
    # Bug 3 (FAILING-BEFORE / PASSING-AFTER REPRODUCTION):
    #         A mixed CSV with some invalid rows must import valid rows and
    #         reject only the invalid ones, not crash the entire import.
    # ------------------------------------------------------------------

    def test_mixed_csv_partial_import(self):
        """Import a CSV where one row has an invalid amount.
        Valid rows must be imported; the bad row must be rejected with its line number.

        BEFORE FIX: This raised ValueError and returned HTTP 400, importing nothing.
        AFTER FIX:  Returns HTTP 200 with imported=2, rejected=1, and error for line 3.
        """
        csv_text = (
            'customer_id,invoice_number,amount,due_date\n'
            'HARBOR,INV-103,84.00,2026-09-12\n'
            'NORTH,INV-302,not-a-number,2026-09-12\n'
            'MAPLE,INV-203,100.00,2026-09-13\n'
        )
        result = importing.import_csv(self.db, csv_text, 'invoices')
        self.assertEqual(result['imported'], 2)
        self.assertEqual(result['rejected'], 1)
        self.assertEqual(len(result['errors']), 1)
        self.assertEqual(result['errors'][0]['line'], 3)

        # Verify the valid invoices were actually stored
        inv103 = storage.invoice_by_key(self.db, 'HARBOR', 'INV-103')
        self.assertIsNotNone(inv103)
        self.assertEqual(inv103['amount'], 84.00)

        inv203 = storage.invoice_by_key(self.db, 'MAPLE', 'INV-203')
        self.assertIsNotNone(inv203)
        self.assertEqual(inv203['amount'], 100.00)

    # ------------------------------------------------------------------
    # Bug 4: Re-importing an identical invoice must skip; re-importing
    #         the same identity with different details must reject.
    # ------------------------------------------------------------------

    def test_reimport_identical_invoice_skips(self):
        """Re-importing the exact same invoice must skip without changing totals."""
        csv_text = (
            'customer_id,invoice_number,amount,due_date\n'
            'HARBOR,INV-100,1250.00,2026-09-01\n'
        )
        before = reporting.overview(self.db)['summary']['outstanding']
        result = importing.import_csv(self.db, csv_text, 'invoices')
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(result['imported'], 0)
        after = reporting.overview(self.db)['summary']['outstanding']
        self.assertEqual(before, after, 'Outstanding total must not change on re-import')

    def test_reimport_same_identity_different_details_rejects(self):
        """Re-importing the same (customer, invoice) with a different amount must reject."""
        csv_text = (
            'customer_id,invoice_number,amount,due_date\n'
            'HARBOR,INV-100,9999.00,2026-09-01\n'
        )
        result = importing.import_csv(self.db, csv_text, 'invoices')
        self.assertEqual(result['rejected'], 1)
        self.assertEqual(result['imported'], 0)
        # Original must be preserved
        inv = storage.invoice_by_key(self.db, 'HARBOR', 'INV-100')
        self.assertEqual(inv['amount'], 1250.00)

    def test_reimport_does_not_double_totals(self):
        """Importing the same invoices twice must not double the outstanding amount."""
        csv_text = (
            'customer_id,invoice_number,amount,due_date\n'
            'HARBOR,INV-102,80.00,2026-09-10\n'
            'MAPLE,INV-202,200.00,2026-09-11\n'
        )
        importing.import_csv(self.db, csv_text, 'invoices')
        after_first = reporting.overview(self.db)['summary']['outstanding']
        result = importing.import_csv(self.db, csv_text, 'invoices')
        self.assertEqual(result['skipped'], 2)
        self.assertEqual(result['imported'], 0)
        after_second = reporting.overview(self.db)['summary']['outstanding']
        self.assertEqual(after_first, after_second)

    # ------------------------------------------------------------------
    # Bug 5: Browser feedback (tested as a response check — the JS fix
    #         ensures the parsed JSON is shown, but we verify the API
    #         returns proper counts and errors in the JSON response).
    # ------------------------------------------------------------------

    def test_import_returns_counts_on_partial_rejection(self):
        """API must return imported/skipped/rejected counts and error details."""
        csv_text = (
            'customer_id,invoice_number,amount,due_date\n'
            'HARBOR,INV-110,50.00,2026-09-20\n'
            'HARBOR,INV-111,,2026-09-20\n'
        )
        result = importing.import_csv(self.db, csv_text, 'invoices')
        self.assertIn('imported', result)
        self.assertIn('skipped', result)
        self.assertIn('rejected', result)
        self.assertIn('errors', result)
        self.assertEqual(result['imported'], 1)
        self.assertEqual(result['rejected'], 1)
        self.assertEqual(result['errors'][0]['line'], 3)
        self.assertIn('required', result['errors'][0]['reason'].lower())

    def test_wrong_header_raises(self):
        """A CSV with wrong headers must raise ValueError (HTTP 400)."""
        csv_text = 'customer,invoice,value\nHARBOR,INV-110,50.00\n'
        with self.assertRaises(ValueError):
            importing.import_csv(self.db, csv_text, 'invoices')

    # ------------------------------------------------------------------
    # Bug 6: CSV export must round correctly, not truncate.
    # ------------------------------------------------------------------

    def test_csv_export_rounds_correctly(self):
        """Export must use proper rounding, not truncation.
        INV-300 has amount 19.99 — with float math, int(19.99*100) could give 1998
        instead of 1999. round() fixes this.
        """
        csv_text = reporting.export_csv(self.db)
        lines = csv_text.strip().splitlines()
        for line in lines[1:]:
            fields = line.strip().split(',')
            # amount is field index 2, paid is 3, balance is 4
            for value in fields[2:5]:
                # Must have exactly 2 decimal places
                self.assertRegex(value, r'^\-?\d+\.\d{2}$',
                                 f'Money value {value} does not have exactly 2 decimal places')

        # Specifically check INV-300 (amount 19.99)
        inv300_line = next(l for l in lines[1:] if 'INV-300' in l)
        fields = inv300_line.split(',')
        self.assertEqual(fields[2], '19.99', 'INV-300 amount must be 19.99 not 19.98')

    def test_csv_export_agrees_with_api_data(self):
        """CSV export values must agree with the reporting API data."""
        csv_text = reporting.export_csv(self.db)
        api_invoices = reporting.invoices(self.db)
        csv_lines = [l.strip() for l in csv_text.strip().splitlines()[1:]]  # skip header

        self.assertEqual(len(csv_lines), len(api_invoices))
        for csv_line, api_row in zip(csv_lines, api_invoices):
            fields = csv_line.split(',')
            self.assertEqual(fields[2], f"{round(api_row['amount'], 2):.2f}")
            self.assertEqual(fields[3], f"{round(api_row['paid'], 2):.2f}")
            self.assertEqual(fields[4], f"{round(api_row['balance'], 2):.2f}")
            self.assertEqual(fields[5], api_row['status'])

    # ------------------------------------------------------------------
    # Custom input case: payment for non-existent invoice stays unmatched
    # ------------------------------------------------------------------

    def test_payment_for_nonexistent_invoice_stays_unmatched(self):
        """A valid payment referencing a non-existent invoice must be retained
        as unmatched. It must not change any invoice balance.
        This is a custom edge case I designed (not from the sample files).
        """
        outstanding_before = reporting.overview(self.db)['summary']['outstanding']

        csv_text = (
            'payment_id,customer_id,invoice_number,amount\n'
            'PAY-GHOST,HARBOR,INV-DOES-NOT-EXIST,500.00\n'
        )
        result = importing.import_csv(self.db, csv_text, 'payments')
        self.assertEqual(result['imported'], 1)

        outstanding_after = reporting.overview(self.db)['summary']['outstanding']
        self.assertEqual(outstanding_before, outstanding_after,
                         'Unmatched payment must not change outstanding total')

        # Verify it appears in unmatched payments
        overview = reporting.overview(self.db)
        unmatched_ids = [p['payment_id'] for p in overview['unmatched_payments']]
        self.assertIn('PAY-GHOST', unmatched_ids)

    def test_reimport_identical_payment_skips(self):
        """Re-importing an identical payment must skip."""
        csv_text = (
            'payment_id,customer_id,invoice_number,amount\n'
            'PAY-DUP,HARBOR,INV-100,100.00\n'
        )
        result1 = importing.import_csv(self.db, csv_text, 'payments')
        self.assertEqual(result1['imported'], 1)

        result2 = importing.import_csv(self.db, csv_text, 'payments')
        self.assertEqual(result2['skipped'], 1)
        self.assertEqual(result2['imported'], 0)

    def test_reimport_payment_different_details_rejects(self):
        """Re-importing a payment ID with different details must reject."""
        csv1 = 'payment_id,customer_id,invoice_number,amount\nPAY-CHG,HARBOR,INV-100,100.00\n'
        importing.import_csv(self.db, csv1, 'payments')

        csv2 = 'payment_id,customer_id,invoice_number,amount\nPAY-CHG,MAPLE,INV-200,200.00\n'
        result = importing.import_csv(self.db, csv2, 'payments')
        self.assertEqual(result['rejected'], 1)
        self.assertEqual(result['imported'], 0)

    def test_valid_header_no_data_rows_succeeds(self):
        """A valid header with no data rows is a successful import with zero counts."""
        csv_text = 'customer_id,invoice_number,amount,due_date\n'
        result = importing.import_csv(self.db, csv_text, 'invoices')
        self.assertEqual(result['imported'], 0)
        self.assertEqual(result['skipped'], 0)
        self.assertEqual(result['rejected'], 0)

    def test_overpayment_shows_negative_balance_and_paid_status(self):
        """Overpayment must show negative balance and mark invoice as paid."""
        csv_text = (
            'payment_id,customer_id,invoice_number,amount\n'
            'PAY-OVER,NORTH,INV-301,200.00\n'
        )
        result = importing.import_csv(self.db, csv_text, 'payments')
        self.assertEqual(result['imported'], 1)

        inv = next(r for r in reporting.invoices(self.db)
                   if r['customer_id'] == 'NORTH' and r['invoice_number'] == 'INV-301')
        self.assertEqual(inv['status'], 'paid')
        self.assertAlmostEqual(inv['balance'], -100.00, places=2)

    def test_overpayment_does_not_reduce_other_invoices(self):
        """An overpayment on one invoice must not reduce another invoice's outstanding."""
        csv_text = (
            'payment_id,customer_id,invoice_number,amount\n'
            'PAY-OVER2,NORTH,INV-301,200.00\n'
        )
        other_before = next(r for r in reporting.invoices(self.db)
                            if r['customer_id'] == 'NORTH' and r['invoice_number'] == 'INV-300')
        importing.import_csv(self.db, csv_text, 'payments')
        other_after = next(r for r in reporting.invoices(self.db)
                           if r['customer_id'] == 'NORTH' and r['invoice_number'] == 'INV-300')
        self.assertEqual(other_before['balance'], other_after['balance'])


class OverdueImprovementTests(unittest.TestCase):
    """Tests for the overdue invoice highlighting improvement."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = storage.connect(Path(self.tmp.name) / 'test.sqlite3')
        storage.seed(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_past_due_open_invoices_are_marked_overdue(self):
        """Open invoices with a due date before today should be marked overdue."""
        # Seed invoices have due dates in September 2026, which are past
        # (test runs after those dates). All open ones should be overdue.
        invoices = reporting.invoices(self.db)
        open_invoices = [i for i in invoices if i['status'] == 'open']
        for inv in open_invoices:
            self.assertTrue(inv['overdue'],
                            f"{inv['invoice_number']} due {inv['due_date']} should be overdue")

    def test_paid_invoices_are_never_overdue(self):
        """Paid invoices must never be marked overdue, even if past due date."""
        paid_invoices = reporting.invoices(self.db, 'paid')
        for inv in paid_invoices:
            self.assertFalse(inv['overdue'],
                             f"Paid invoice {inv['invoice_number']} should not be overdue")

    def test_overview_includes_overdue_count(self):
        """Overview summary must include an overdue_count field."""
        overview = reporting.overview(self.db)
        self.assertIn('overdue_count', overview['summary'])
        self.assertIsInstance(overview['summary']['overdue_count'], int)
        self.assertGreater(overview['summary']['overdue_count'], 0)

    def test_future_due_date_is_not_overdue(self):
        """An open invoice with a future due date should not be overdue."""
        csv_text = (
            'customer_id,invoice_number,amount,due_date\n'
            'HARBOR,FUTURE-1,100.00,2099-12-31\n'
        )
        importing.import_csv(self.db, csv_text, 'invoices')
        inv = next(r for r in reporting.invoices(self.db)
                   if r['invoice_number'] == 'FUTURE-1')
        self.assertFalse(inv['overdue'], 'Future due date should not be overdue')


if __name__ == '__main__':
    unittest.main()
