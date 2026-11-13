# fal.ai

Fumes reads your **fal balance** from the billing API and your **spend per endpoint** from the usage
API.

## Get a key

1. Sign in at [fal.ai](https://fal.ai/dashboard/keys) and open **Keys**. If you work in a team,
   pick the team in the top-left corner first: keys belong to the account that creates them.
2. Create a key named `Fumes` with the **`BILLING`** preset. It can read your balance and usage and
   nothing else: it cannot call models or change your account, so it is the safest key to give Fumes.
   (`FULL` also works, but grants far more than Fumes needs; the default `API` preset is refused by
   the usage API.)
3. Copy it (it is shown only once), paste it into the key field here and choose **Test**.

Fumes keeps the key in Windows Credential Manager and sends it only to `api.fal.ai`.

## What you will see

- **Balance**: fal's own credit balance.
- **Spend**: what each endpoint cost per day, for the month so far and the last 30 days.
