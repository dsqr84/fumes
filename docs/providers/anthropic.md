# Anthropic API

Fumes reads your **API-key spend** from Anthropic's cost report. This is the pay-as-you-go API, not a
Claude Pro or Max subscription (those show in the Claude limits cell instead). Anthropic has no
balance API, so you enter your current credit once and Fumes counts it down.

## Get an Admin key

The cost report is part of the Admin API, which needs an **organisation** account and an **Admin
key** (it starts with `sk-ant-admin`). An ordinary API key (`sk-ant-api…`) is refused.

1. If your Console account is an individual one, set up an organisation first:
   [Settings → Organization](https://platform.claude.com/settings/organization).
2. As an organisation admin, open [Settings → Admin keys](https://platform.claude.com/settings/admin-keys),
   create a key named `Fumes`, and copy it.
3. Paste it into the key field here and choose **Test**.

An Admin key can manage your organisation, not just read costs. Fumes only calls the cost report
with it, keeps it in Windows Credential Manager, and sends it only to `api.anthropic.com`.

## Enter your credit

Open [Billing](https://platform.claude.com/settings/billing), copy your **credit balance**, and enter
it under **Credit balance** here. Fumes subtracts the cost report's spend from then on; enter it
again after each top-up.
