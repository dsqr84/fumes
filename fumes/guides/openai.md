# OpenAI

Fumes reads your **API spend** from OpenAI's organisation costs API. OpenAI has no balance API, so you
enter your current credit once and Fumes counts it down as you spend.

## Get an Admin key

The costs API only accepts an **Admin key**, not an ordinary project key.

1. Sign in at [platform.openai.com](https://platform.openai.com/settings/organization/admin-keys) as an
   organisation **Owner** and open **Settings → Organization → Admin keys**.
2. Choose **Create new admin key** and name it `Fumes`.
3. Under **Permissions**, choose **Restricted**. Set **Usage** to **Read** and leave everything
   else off.
4. Create the key and copy it (it starts with `sk-admin-`).
5. Paste it into the key field here and choose **Test**.

## Limit this key

- **Choose Restricted, with only Usage set to Read.** A full Admin key can manage your
  organisation: create keys, invite people and change settings. This one can only read usage and
  costs, which is all Fumes asks for.
- **Or choose Read only.** It is simpler and also works, but it can read the rest of your
  organisation's settings too.
- **Use it for Fumes alone.** Named `Fumes`, it can be revoked without breaking anything else.
- **Where it goes.** Fumes keeps the key in Windows Credential Manager and sends it only to
  `api.openai.com`.

## Enter your credit

Open [Billing](https://platform.openai.com/settings/organization/billing/overview), copy the
**credit balance**, and enter it under **Credit balance** here. Fumes subtracts what the costs API
reports from then on, so enter it again after each top-up.

The costs API reports whole days, so today's spend so far is read when you enter the balance and is
not subtracted twice.
