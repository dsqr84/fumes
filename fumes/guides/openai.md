# OpenAI

Fumes reads your **API spend** from OpenAI's organisation costs API. OpenAI has no balance API, so you
enter your current credit once and Fumes counts it down as you spend.

## Get an Admin key

The costs API only accepts an **Admin key**, not an ordinary project key.

1. Sign in at [platform.openai.com](https://platform.openai.com/settings/organization/admin-keys) as an
   organisation **Owner** and open **Settings → Organization → Admin keys**.
2. Choose **Create new admin key**, name it `Fumes`, and copy it (it starts with `sk-admin-`).
3. Paste it into the key field here and choose **Test**.

An Admin key can manage your organisation, not just read costs. Fumes only ever calls the costs
endpoint with it, keeps it in Windows Credential Manager, and sends it only to `api.openai.com`.

## Enter your credit

Open [Billing](https://platform.openai.com/settings/organization/billing/overview), copy the
**credit balance**, and enter it under **Credit balance** here. Fumes subtracts what the costs API
reports from then on, so enter it again after each top-up.

The costs API reports whole days, so today's spend so far is read when you enter the balance and is
not subtracted twice.
