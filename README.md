# CloudPRNT

Star Micronics CloudPRNT printers for Frappe / ERPNext: till receipts, test tickets and the cash drawer, on
printers that reach the site over the Internet (mC-Print3, TSP100IV, TSP650II with an IFBD-HI01X interface…).

## How it works

A CloudPRNT printer polls one URL of the site every few seconds:

```
https://<site>/api/method/cloudprnt.protocol.serve
```

- **POST** (poll): the printer sends its MAC address and status; the site answers whether a job is ready, in
  which formats, and, when idle, asks the printer what it is (model, firmware, formats, paper width).
- **GET** `?mac=&type=&token=`: the job, in the format the printer chose.
- **DELETE** `?mac=&token=&code=`: the job is done; the code says whether it printed.

The protocol is served by the site itself (`cloudprnt/protocol.py`): no server beside the bench, no nginx rule.
Jobs are queued as Star Document Markup (`CloudPRNT Print Queue`) and rendered when fetched by Star's own
converter, `cputil` (`cloudprnt/bin/cputil`, v2.0.1 for linux x86-64), for the printer's paper width.

## Security

Each printer has an account of its own (a Website User with the *CloudPRNT Device* role, `devices.py`). The
printer is configured with that account's API key and secret as the CloudPRNT user name and password, which it
sends as HTTP Basic authentication. A job only leaves the site for the account of the printer it is queued for;
an anonymous poll from an unknown printer is noted for pairing and answered "no job".

`cputil` runs with a fixed argument list and no shell; the job's markup goes through a temporary file.
`tests/test_exposed_endpoints.py` keeps any shell out of the app and lists what an anonymous caller reaches.
Security reports: see `SECURITY.md`.

## Adding a printer

1. **CloudPRNT Settings → Printers → Add a printer**: give it a label. The site shows the server URL, user name
   and password, and a command.
2. On a computer on the printer's network, run the command. It finds the Star printers on the network, asks for
   the printer's administrator password, enables CloudPRNT with the three values and a 2 s polling interval,
   saves and restarts the printer (`cloudprnt/public/star_printer_setup.py`, Python 3, no dependency).
   Or enter the three values by hand on the printer's web page (CloudPRNT), then Save and restart it.
3. The printer signs in and prints a ticket saying it is connected. Its MAC address, model, firmware, paper
   width and the formats it prints fill in by themselves; its status (paper low or empty, cover open, cash
   drawer) follows its polls.

## Receipts and the cash drawer

- A printer row can be tied to a **POS Profile**: *Print Receipts Automatically* prints the receipt of every
  sale of that till when it is submitted (Sales Invoice with `is_pos`, or POS Invoice).
- *Open the Cash Drawer*: with the receipt of cash sales (default), always, or never. The drawer is the one
  wired to the printer's drawer port.
- From the desk: the **Thermal receipt** button of a submitted POS sale, and **Open the cash drawer** in the
  settings, which opens it without printing.
- From code: `cloudprnt.api.print_receipt(doctype, name, printer=None)`,
  `cloudprnt.api.open_cash_drawer(printer=None)`, and `cloudprnt.print_queue_manager.enqueue(printer, markup,
  open_drawer="start"|"end")` for any Star Document Markup.

## Tests

```
bench --site <a clone of the site> run-tests --app cloudprnt
```

Never on a live site: the suite creates printers, accounts and jobs.
