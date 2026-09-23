# Handover

- Name: Siddhant
- Email used for this application: siddhantsingh4work@gmail.com
- Chosen track: Track A — Repair the register
- Why this track: I like digging into code to figure out what's wrong and fixing it, so this felt like the right fit.
- Approximate total time, including setup and handover: around 3.5 hours

## Run and verify

Python 3.10+ required. No extra packages to install. From the `track-a` directory:

```bash
# Run the full test suite
python -m unittest discover -s tests -v

# Start fresh and try the app
python app.py reset-demo
python app.py
# Then open http://127.0.0.1:8787

# Or restore the owner's existing register and run
python restore_fixture.py --replace
python app.py
# Should show 9 invoices, 7 open, INR 3,698.19 outstanding
```

## What I delivered

Found and fixed all six bugs. Added 35 new tests across two files. Built one small feature on top (overdue highlighting).

### The bugs I found, roughly in order of how bad they are

1. **Payment matching was based on amount, not identity** (`matching.py`) — This was the worst one. The code scanned all invoices looking for a matching amount first, and only fell back to the actual customer/invoice reference if nothing matched by amount. So a ₹1250 payment meant for MAPLE could silently end up on HARBOR's invoice. I stripped out the amount logic entirely and matched purely by `(customer_id, invoice_number)` as the spec requires.

2. **The "open invoices" filter showed paid invoices** (`reporting.py`) — A typo-level bug: `{'open': 'paid', 'paid': 'paid'}` — both map to `'paid'`. Straightforward fix, just filter by the actual requested status.

3. **One bad CSV row killed the whole import** (`importing.py`) — All rows were validated upfront in a list comprehension. If any single row failed, the entire import threw an error and nothing got saved. Moved validation inside the per-row loop so valid rows still get imported and bad ones get individually rejected with their line numbers.

4. **Re-importing invoices created duplicates** (`storage.py`) — `insert_invoice()` had no existence check at all. Import the same file twice and you'd get double the invoices, double the outstanding total. Added a lookup by `(customer_id, invoice_number)` — skip if identical, reject if the same identity has different details.

5. **The UI always said "Import complete" even when it failed** (`app.js`) — The fetch response was never actually read. I parsed the JSON response and now the UI shows the real counts (imported/skipped/rejected) and lists rejection reasons with line numbers. On a full failure it shows the error message instead of pretending things worked.

6. **CSV export truncated money instead of rounding** (`reporting.py`) — `int(19.99 * 100)` gives `1998` because of floating point, so the export would show `19.98` while the screen showed `19.99`. Switched to `round()`.

### Improvement: overdue highlighting

The owner mentioned preparing for a busy week. Right now every open invoice looks the same — there's no way to tell at a glance which ones are past due. I added a simple check: if an open invoice's due date is before today, it's marked overdue. In the UI, overdue rows get a light red background and the status shows "open · overdue". There's also a new "Overdue" count card at the top in amber. Nothing fancy, but it makes the important invoices stand out immediately.

## Evidence and limits

**Failing-before / passing-after** — `test_mixed_csv_partial_import`: I import a CSV with 2 valid rows and 1 row with `not-a-number` as the amount. Before my fix, this threw a ValueError and imported nothing. After the fix, it returns `imported: 2, rejected: 1` with the error pointing to line 3. I also verify both valid invoices actually landed in the database.

**Changed-input case** — `test_payment_for_nonexistent_invoice_stays_unmatched`: I made up a payment referencing `INV-DOES-NOT-EXIST`. I expected it to be saved as unmatched without touching any invoice balance. That's what happened — outstanding total didn't change, and the payment showed up in the unmatched list.

**Existing register** — `test_fixture.py` has 12 tests that restore the owner's database, check all records against `expected-records.json`, verify the summary numbers, try importing new data on top of it, and confirm everything survives a database reconnection (simulating app restart).

All 40 tests pass:
```
> python -m unittest discover -s tests -v
Ran 40 tests in 0.970s
OK
```

**What I didn't get to:**
- The JS fix (bug 5) is verified through the API response structure, not automated browser tests. I checked the UI manually and it works, but there's no Selenium/Playwright test for it.
- Money is stored as SQLite `REAL`. For two-decimal inputs it's fine, but a proper system should use integer cents or a decimal type. I didn't want to refactor the schema and risk breaking things within the time budget.
- Overdue detection uses `date.today()`, so it depends on the server's clock. A production version might need timezone handling.
- If I had more time, I'd add a `UNIQUE` constraint on `(customer_id, invoice_number)` at the database level so duplicates are impossible even if the app code has a bug.

## Tools and judgment

**AI assistant used**: Claude Opus 4 (Thinking mode) via Antigravity IDE throughout the session.

**What I used it for**: Reading through the codebase to understand the architecture, identifying the six bugs from the code, drafting fixes, writing the test files (`test_bugs.py` and `test_fixture.py`), and implementing the overdue feature. I also used it to help write this handover.

**How I checked its work**: I ran all tests after every change to make sure nothing broke. I also read through every code change and cross-checked against BUSINESS_RULES.md before accepting it. Three specific examples:

1. When I asked about fixing the payment matching, it initially suggested keeping amount as a fallback match criterion. I went back and re-read BUSINESS_RULES.md which clearly says "an amount alone does not establish identity," so I removed the amount-matching logic entirely rather than leaving it as a tiebreaker. The spec was unambiguous here and the AI's first suggestion would have been wrong.

2. It recommended switching all money handling to Python's `Decimal` type. That would be cleaner in theory but it means touching the schema, every query, and the JSON serialization — too much surface area for a time-boxed fix. I just fixed the specific truncation bug with `round()` and noted the broader limitation. Pragmatic over perfect.

3. For the improvement, it suggested a full aging report with 30/60/90 day buckets. The owner's actual problem was about a "busy week" — they just need to see what's overdue right now, not a full AR dashboard. I went with a simple overdue flag and count, which I could actually build, test, and verify in the remaining time.

