"""Fixture preservation tests.

Verify that the repaired code correctly handles the owner's existing register
from fixtures/existing-register.sqlite3, including all expected records,
summary totals, and the ability to import new data after restoration.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from ledger import storage, reporting, importing

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DB = ROOT / 'fixtures' / 'existing-register.sqlite3'
EXPECTED_RECORDS = ROOT / 'fixtures' / 'expected-records.json'


class FixturePreservationTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / 'clearledger.sqlite3'
        shutil.copy2(FIXTURE_DB, self.db_path)
        self.db = storage.connect(self.db_path)
        with open(EXPECTED_RECORDS, encoding='utf-8') as f:
            self.expected = json.load(f)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    # ------------------------------------------------------------------
    # Record identity and values
    # ------------------------------------------------------------------

    def test_customer_count(self):
        rows = self.db.execute('SELECT * FROM customers ORDER BY customer_id').fetchall()
        self.assertEqual(len(rows), len(self.expected['customers']))

    def test_customer_identities(self):
        for exp in self.expected['customers']:
            row = self.db.execute(
                'SELECT * FROM customers WHERE customer_id=?',
                (exp['customer_id'],)
            ).fetchone()
            self.assertIsNotNone(row, f"Customer {exp['customer_id']} missing")
            self.assertEqual(row['name'], exp['name'])

    def test_invoice_count(self):
        rows = self.db.execute('SELECT * FROM invoices').fetchall()
        self.assertEqual(len(rows), len(self.expected['invoices']))

    def test_invoice_identities_and_values(self):
        for exp in self.expected['invoices']:
            row = self.db.execute(
                'SELECT * FROM invoices WHERE id=?', (exp['id'],)
            ).fetchone()
            self.assertIsNotNone(row, f"Invoice id={exp['id']} missing")
            self.assertEqual(row['customer_id'], exp['customer_id'])
            self.assertEqual(row['invoice_number'], exp['invoice_number'])
            self.assertAlmostEqual(row['amount'], float(exp['amount']), places=2)
            self.assertEqual(row['due_date'], exp['due_date'])

    def test_payment_count(self):
        rows = self.db.execute('SELECT * FROM payments').fetchall()
        self.assertEqual(len(rows), len(self.expected['payments']))

    def test_payment_identities_and_allocations(self):
        for exp in self.expected['payments']:
            row = self.db.execute(
                'SELECT * FROM payments WHERE payment_id=?', (exp['payment_id'],)
            ).fetchone()
            self.assertIsNotNone(row, f"Payment {exp['payment_id']} missing")
            self.assertEqual(row['customer_id'], exp['customer_id'])
            self.assertEqual(row['invoice_number'], exp['invoice_number'])
            self.assertAlmostEqual(row['amount'], float(exp['amount']), places=2)
            if exp['invoice_id'] is None:
                self.assertIsNone(row['invoice_id'],
                                  f"Payment {exp['payment_id']} should be unmatched")
            else:
                self.assertEqual(row['invoice_id'], exp['invoice_id'])

    # ------------------------------------------------------------------
    # Summary totals
    # ------------------------------------------------------------------

    def test_summary_totals(self):
        overview = reporting.overview(self.db)
        summary = overview['summary']
        exp = self.expected['summary']
        self.assertEqual(summary['invoice_count'], exp['invoice_count'])
        self.assertEqual(summary['open_count'], exp['open_count'])
        self.assertAlmostEqual(summary['outstanding'], float(exp['outstanding']), places=2)

    def test_unmatched_payment_count(self):
        overview = reporting.overview(self.db)
        self.assertEqual(len(overview['unmatched_payments']), 1)
        self.assertEqual(overview['unmatched_payments'][0]['payment_id'], 'KEEP-U1')

    # ------------------------------------------------------------------
    # New imports work alongside existing data
    # ------------------------------------------------------------------

    def test_new_invoice_import_after_restore(self):
        """Valid new invoices must import successfully alongside existing records."""
        csv_text = (
            'customer_id,invoice_number,amount,due_date\n'
            'HARBOR,NEW-FIXTURE-1,500.00,2026-09-20\n'
        )
        result = importing.import_csv(self.db, csv_text, 'invoices')
        self.assertEqual(result['imported'], 1)

        # Existing records still present
        overview = reporting.overview(self.db)
        self.assertEqual(overview['summary']['invoice_count'], 10)

    def test_new_payment_import_after_restore(self):
        """Valid new payments must import and attach to existing invoices."""
        csv_text = (
            'payment_id,customer_id,invoice_number,amount\n'
            'NEW-PAY-1,MAPLE,INV-201,100.00\n'
        )
        result = importing.import_csv(self.db, csv_text, 'payments')
        self.assertEqual(result['imported'], 1)

        inv = next(r for r in reporting.invoices(self.db)
                   if r['customer_id'] == 'MAPLE' and r['invoice_number'] == 'INV-201')
        self.assertAlmostEqual(inv['paid'], 100.00, places=2)

    def test_data_survives_reconnection(self):
        """Data must survive closing and reopening the database (simulating app restart)."""
        # Import new data
        csv_text = (
            'customer_id,invoice_number,amount,due_date\n'
            'NORTH,RESTART-TEST,75.00,2026-09-25\n'
        )
        importing.import_csv(self.db, csv_text, 'invoices')
        self.db.close()

        # Reconnect (simulating app restart)
        self.db = storage.connect(self.db_path)
        inv = storage.invoice_by_key(self.db, 'NORTH', 'RESTART-TEST')
        self.assertIsNotNone(inv, 'New invoice must survive restart')
        self.assertEqual(inv['amount'], 75.00)

        # Original records also survive
        overview = reporting.overview(self.db)
        self.assertEqual(overview['summary']['invoice_count'], 10)

    def test_existing_invoice_reimport_skips(self):
        """Re-importing an existing fixture invoice must skip, not duplicate."""
        csv_text = (
            'customer_id,invoice_number,amount,due_date\n'
            'HARBOR,KEEP-700,456.78,2026-09-09\n'
        )
        result = importing.import_csv(self.db, csv_text, 'invoices')
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(result['imported'], 0)

        overview = reporting.overview(self.db)
        self.assertEqual(overview['summary']['invoice_count'], 9)


if __name__ == '__main__':
    unittest.main()
