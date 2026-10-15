# BNI VP Scripts

Before running a live report, copy `.env.example` to `.env`,
`config/chapter.example.json` to `config/chapter.json`, and
`config/tradesheet.example.json` to `config/tradesheet.json`.
Fill in your own login, integration client secret, chapter IDs, and chapter settings.
These local files are ignored by Git. `.aws`, `tmp/`, and the entire `output/`
directory are also ignored. Do not commit downloaded member reports or credentials.

The files under `examples/` and `tests/fixtures/` contain fictional sample data.
The browser preview uses local report/config files when present, otherwise the
fictional examples. Preview builds can include private local data, so do not
publish a build made from live chapter reports.

Install Python dependencies with `uv sync` and browser dependencies with `npm install`.
Run tests with `uv run python -m unittest discover -s tests`.

# BNI Tradesheet

`tradesheet.py` creates the weekly two-page BNI Tradesheet in `output/pdf/`.
It obtains the chapter totals and current member names from BNI Connect at run
time. Edit `config/tradesheet.json` for the items maintained by the chapter:
the meeting programme, leadership, roster, events, target professions, and
member business/contact details.

Run it (it uses the next Wednesday as the meeting date and today's BNI report data):

```bash
uv run python tradesheet.py
```

Use `--date YYYY-MM-DD` to override the displayed meeting date, or
`--report-date YYYY-MM-DD` to select a different BNI report snapshot date.

Use `--offline` to check the layout without signing in to BNI Connect.

For an unattended weekly run, schedule `scripts/run_tradesheet.sh` in the
chapter's scheduler. Keep `.env` local: it supplies the BNI sign-in details.

## Notable Networker certificates

`notable_networker_certificates.py` creates one landscape Letter PDF for each
available monthly Notable Networker winner in `output/pdf/`. It uses the award
month and winner data from `output/bni_report.json`, along with the current
chapter President from the report leadership list.

```bash
uv run python notable_networker_certificates.py --date 2026-09-24
```

Use `--offline` to reuse the cached report for layout checks, `--output-dir`
to choose a destination, and `--save-html` to retain the rendered certificate
HTML beside each PDF.

## Monthly Chapter Success Report (CSR)

`monthly_csr.py` downloads the supporting reports and displays numerical
actuals in CSR order for copying onto the handwritten form. It saves the same
summary in `output/csr/YYYY-MM/summary.txt`, with the downloaded HTML/JSON
reports and snapshot metadata alongside it.

```bash
uv run python monthly_csr.py
uv run python monthly_csr.py --month 2026-09
uv run python monthly_csr.py --month 2026-09 --offline
```

The default is the previous completed calendar month in Australia/Brisbane.
Membership counts use the current completion-month dues snapshot; both dates
appear in the summary. Offline runs retain the cached snapshot's original
date. `--output-dir PATH` changes the root destination; each month gets its
own subdirectory.

Keep credentials in the local `.env`: `username` and `password` for BNI
Connect, and `r2y_username` and `r2y_password` for Reporting2You's separate
login. Chapter details come from `config/chapter.json` (with the existing
`IdOrg` and `IdRegion` environment overrides).

The output includes the President/VP indicators, supported renewal counts
with member names listed beneath each count, and chair value. One-month
member growth is the reporting month's ending chapter size minus the previous
month's ending size, using the two Chapter Traffic Lights snapshots.
An older offline cache needs an online run to download the preceding month's
Traffic Lights snapshot before one-month growth can be shown.
The MTL average is the mean of the **selected month's
scores**, not the historical Average column. Chair value is rolling
12-month TYFCB divided by the chapter size at the end of the reporting month.
The rolling TYFCB amount is displayed on its own line above chair value.

Unavailable fields are labelled with a reason. The regional CTL table cannot
establish an Australian ranking. Pending Renewal does not establish committee
review, and Dropped does not establish a decision not to renew. Those fields
stay unavailable unless the source explicitly records them. Seven-month
reviews need original join dates matched to the current dues roster.

The script supplies actuals and renewal-category member names: goals, targets,
and written management answers are left for manual completion. It does not email
the report. Unexpected download/parsing failures retain available results and
return exit status 1; an otherwise complete run with unsupported committee
fields returns 0.
