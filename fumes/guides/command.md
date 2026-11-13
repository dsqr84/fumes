# Command provider

For a service Fumes does not support yet, with an API or a local log you can read. Fumes runs a small
program you choose at each refresh and shows what it prints. You can write the program yourself, or
have an AI assistant write it from the prompt below.

## The contract

Fumes runs the program with no shell, from the Fumes folder, and waits up to the timeout you set
(20 seconds unless you change it). The program prints **one JSON object** and exits with code 0:

```json
{
  "balance": 12.40,
  "rows": [["2026-11-12", "model-x", 0.31], ["2026-11-13", "model-x", 0.18]],
  "note": "Optional text shown in the peek."
}
```

- `balance`: the credit left, in US dollars, or `null` if the service has none.
- `rows`: optional spend, one `[day, label, dollars]` per line, days as `YYYY-MM-DD` in UTC, the last
  30 days or more. Fumes adds them up for the chart, the month to date and the breakdown.
- `note`: optional, plain text.
- Print at least a balance or some rows. Anything else on standard output breaks the JSON; write
  logging to standard error, which Fumes ignores.

**The key.** If the service needs a key, tick **Needs a key** and save it with
`fumes key set <id>` (or on the provider's page in Settings). Fumes passes it to the program in the
environment variable `FUMES_KEY`. Never put a key in the command or in the script itself.

## Add one

1. Save the program somewhere permanent, for example `%USERPROFILE%\fumes-providers\acme.py`.
2. In Settings, choose **Add provider**, then **Command**. Give it a name and an id, choose the
   program (for a Python script, the program is `python.exe` and the script is its argument), and
   tick **Needs a key** if it does.
   Under **Will run**, Settings shows exactly the list of arguments the program will receive. Put
   quotes around a whole argument that contains spaces (`"C:\My Scripts\acme.py"`), not around part
   of one (`--name="a b"` does not work; use `--name` and `"a b"` as two arguments if the program
   allows it).
3. Choose **Test**. Fumes runs it once and shows the balance it printed, or what went wrong.

## Have an AI write it

Paste this into Claude, ChatGPT or another assistant, filling in the first line. Do **not** paste
your key into the chat: the script reads it from `FUMES_KEY`, and you save it in Fumes yourself.

```text
Write a Fumes command provider for <SERVICE> (<its API documentation URL, or the local log it reads>).

Fumes runs the program with no shell and reads one JSON object from standard output:
{"balance": <US dollars or null>, "rows": [["YYYY-MM-DD", "<label>", <dollars>], ...], "note": "<text>"}
- balance: the credit left now, if the service reports one.
- rows: spend per day (UTC) per model or line item, for at least the last 30 days, if available.
- Exit with code 0 on success, non-zero on any failure. Print nothing else to standard output;
  send logging and errors to standard error.
- The API key, if one is needed, is in the environment variable FUMES_KEY. Never print it, log it,
  write it to a file, or put it in a URL; send it only in the header the service documents, and only
  to the service's own HTTPS API host.
- Use only read-only endpoints (balance, usage, costs). Do not call anything that spends money,
  creates resources or changes the account.
- Use Python 3.11+ and only the standard library (urllib.request, json), with a 15-second timeout
  on every request.
- Do not follow redirects to another host, and do not disable certificate checks.

Then tell me: which kind of key to create on <SERVICE> and the narrowest permission it needs, and
the exact program and arguments to enter in Fumes.
```

Read what the assistant wrote before you run it: it will run with your Windows account's access, and
it is given your key.
